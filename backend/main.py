"""FastAPI application entrypoint for the AI Interview Simulator."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import get_settings
from backend.logging_config import (
	configure_error_tracking,
	configure_logging,
	request_id_var,
)
from backend.metrics import http_request_duration_seconds, http_requests_total
from backend.api.rate_limit import warn_if_throttles_are_process_local
from backend.api.routes_assessment import router as assessment_router
from backend.api.routes_auth import router as auth_router
from backend.api.routes_dsa import router as dsa_router
from backend.api.routes_interview import router as interview_router
from backend.api.routes_report import router as report_router
from backend.api.routes_resume import router as resume_router
from backend.api.routes_workflow import router as workflow_router
from backend.api.routes_voice import router as voice_router
from backend.api.ws_interview import router as ws_interview_router
from backend.nlp.role_matcher import get_semantic_backend_status, warmup_semantic_encoder
from backend.research_assets import get_research_asset_summary, warmup_research_asset_registry
from backend.voice.stt import warmup_stt

_LOGGER = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
	"""Tag every request with an id so its log lines can be correlated.

	Reuses an inbound ``X-Request-ID`` when a proxy/gateway already assigned
	one, otherwise mints a new one. Echoed back on the response so a client
	report ("it happened around 14:02") can be turned into an exact log query.
	"""

	async def dispatch(self, request: Request, call_next):
		incoming = request.headers.get("X-Request-ID")
		request_id = incoming.strip() if incoming and incoming.strip() else uuid.uuid4().hex

		# Starlette's ServerErrorMiddleware — which is what actually invokes a
		# handler registered for the base Exception class — sits *outside* this
		# middleware and rebuilds its own Request from the same ASGI scope only
		# after this dispatch's `finally` has already reset the contextvar. So
		# a truly unhandled exception needs the id recovered from `request.state`
		# (backed by that shared scope), not from the contextvar. Logging calls
		# made *during* normal request handling still read the contextvar.
		request.state.request_id = request_id

		token = request_id_var.set(request_id)
		try:
			response = await call_next(request)
		finally:
			request_id_var.reset(token)
		response.headers["X-Request-ID"] = request_id
		return response


class RequestMetricsMiddleware(BaseHTTPMiddleware):
	"""Record request count and latency, labelled by route template.

	Labelling by ``request.url.path`` directly would give every session id
	and UUID embedded in a URL its own Prometheus time series — an unbounded
	label is exactly the mistake that makes a metrics endpoint fall over. The
	route *template* (``/dsa/{session_id}/run``, not the resolved path) is
	only available on ``request.scope["route"]`` after routing has actually
	matched, which happens inside ``call_next`` — hence reading it after,
	not before.
	"""

	async def dispatch(self, request: Request, call_next):
		if request.url.path == "/metrics":
			return await call_next(request)

		started_at = time.monotonic()
		try:
			response = await call_next(request)
		except Exception:
			# Record the failure before re-raising. Without this, an exception
			# propagating out of call_next skips the counters entirely, so a
			# route that raises on every request looks like *no traffic at all*
			# rather than a route that is failing — the metrics go quiet
			# exactly when they matter most.
			#
			# ErrorHandlingMiddleware sits inside this one and converts route
			# exceptions into a 500 response, so in practice this path only
			# catches something raised by the middleware between the two. It is
			# kept because "the counter lies about failures" is the more
			# expensive bug of the two.
			self._record(request, "500", time.monotonic() - started_at)
			raise

		self._record(request, str(response.status_code), time.monotonic() - started_at)
		return response

	@staticmethod
	def _record(request: Request, status: str, duration: float) -> None:
		route = request.scope.get("route")
		path = getattr(route, "path", None) or "unmatched"

		http_requests_total.labels(
			method=request.method, path=path, status=status
		).inc()
		http_request_duration_seconds.labels(method=request.method, path=path).observe(duration)


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
	"""Turn an unhandled exception into the structured 500 from *inside* the
	middleware stack.

	There is already an ``@app.exception_handler(Exception)`` below, and it
	produces the same body — but a handler registered for the base Exception
	class is invoked by Starlette's ``ServerErrorMiddleware``, which wraps the
	entire user middleware stack from the outside. A response built there has
	bypassed every middleware on the way out, with two consequences that only
	show up in production:

	1. ``RequestMetricsMiddleware`` never sees it, so 500s are missing from
	   ``http_requests_total``.
	2. ``CORSMiddleware`` never sees it, so the response carries no
	   ``Access-Control-Allow-Origin``. The browser then reports an opaque CORS
	   failure instead of surfacing the JSON body — which means the
	   ``request_id`` deliberately included in that body, the whole point of
	   correlating a user report back to a log line, never reaches the user.
	   This is not a hypothetical: the deployed frontend and API are on
	   different origins by design, so *every* 500 would be unreadable.

	Catching here, innermost, means the exception becomes an ordinary response
	that then travels back out through metrics and CORS like any other.
	The outer handler is kept as a fallback for anything raised by the
	middleware itself, outside this one.
	"""

	async def dispatch(self, request: Request, call_next):
		try:
			return await call_next(request)
		except Exception as exc:
			request_id = getattr(request.state, "request_id", None) or request_id_var.get()
			_LOGGER.error(
				"Unhandled exception on %s %s",
				request.method,
				request.url.path,
				exc_info=exc,
			)
			return _internal_error_response(request_id)


def _internal_error_response(request_id: str | None) -> JSONResponse:
	"""The one structured 500 body, shared by the middleware and the handler.

	Deliberately says nothing about the exception. The detail belongs in the
	log line, correlated by ``request_id``; putting it here would leak internal
	structure to any caller who can trigger an error.
	"""

	return JSONResponse(
		status_code=500,
		content={
			"error": {
				"code": "internal_error",
				"message": "An unexpected error occurred.",
				"request_id": request_id,
			}
		},
		headers={"X-Request-ID": request_id} if request_id else None,
	)


@asynccontextmanager
async def _lifespan(_: FastAPI):
	# Load Whisper off the event loop so the first candidate to speak does not
	# pay the model load inside their turn. Startup continues if it fails.
	warmup_task = asyncio.create_task(asyncio.to_thread(warmup_stt))
	try:
		yield
	finally:
		warmup_task.cancel()
		with contextlib.suppress(asyncio.CancelledError, Exception):
			await warmup_task


def create_app() -> FastAPI:
	configure_logging()
	configure_error_tracking()

	# Warm up before the event loop starts: importing sentence-transformers'
	# native extensions (pyarrow/datasets) while asyncio's loop is already
	# running triggers an intermittent access violation on Windows.
	warmup_semantic_encoder()
	warmup_research_asset_registry()

	# Say it out loud if the throttles are per-process while more than one
	# worker is running: every configured limit is then silently multiplied by
	# the worker count. This is the caveat rate_limit.py has always carried in
	# a docstring, which is exactly the wrong place for it to be noticed.
	warn_if_throttles_are_process_local()

	settings = get_settings()

	app = FastAPI(title="AI Interview Simulator", version="0.1.0", lifespan=_lifespan)

	# ORDER IS LOAD-BEARING, AND IT READS BACKWARDS.
	#
	# `add_middleware` prepends, so the LAST one added is the OUTERMOST. The
	# stack below therefore runs, outermost to innermost:
	#
	#     CORSMiddleware            <- must be outermost, so it can attach
	#                                  Access-Control-* to every response that
	#                                  leaves, including error responses
	#       RequestIdMiddleware     <- sets the contextvar the two below log with
	#         RequestMetricsMiddleware
	#           ErrorHandlingMiddleware   <- innermost, so a route exception
	#                                        becomes a response that the two
	#                                        above still get to see
	#             ...routes
	#
	# The previous order had CORS added last of the three and therefore
	# innermost, which is why 500s reached the browser with no CORS headers.
	# Adding a middleware here without reading the above will silently
	# reintroduce that.
	app.add_middleware(ErrorHandlingMiddleware)
	app.add_middleware(RequestMetricsMiddleware)
	app.add_middleware(RequestIdMiddleware)
	app.add_middleware(
		CORSMiddleware,
		allow_origins=list(settings.cors.allowed_origins),
		allow_origin_regex=settings.cors.allow_origin_regex,
		allow_credentials=True,
		allow_methods=["*"],
		allow_headers=["*"],
	)

	@app.exception_handler(Exception)
	async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
		# FALLBACK ONLY. ErrorHandlingMiddleware now catches route exceptions
		# from inside the stack, which is what gets them counted in metrics and
		# decorated with CORS headers. This handler is invoked by Starlette's
		# ServerErrorMiddleware, which wraps the user middleware stack from the
		# outside, so it is reached only when something raises in the
		# middleware *between* ServerErrorMiddleware and ErrorHandlingMiddleware
		# — a genuinely unusual case, but one that would otherwise return an
		# opaque 500 with no log line.
		#
		# ServerErrorMiddleware rebuilds its own Request from the shared ASGI
		# scope, and does so only after RequestIdMiddleware's `finally` has
		# already reset the contextvar. `request.state` is backed by that same
		# scope object for the whole connection, so it survives where the
		# contextvar does not; the contextvar is kept as a fallback for the
		# unit tests that call this handler directly.
		request_id = getattr(request.state, "request_id", None) or request_id_var.get()
		_LOGGER.error(
			"Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc
		)
		return _internal_error_response(request_id)

	app.include_router(auth_router)
	app.include_router(assessment_router)
	app.include_router(dsa_router)
	app.include_router(interview_router)
	app.include_router(report_router)
	app.include_router(workflow_router)
	app.include_router(ws_interview_router)
	app.include_router(voice_router)
	app.include_router(resume_router)

	@app.get("/health")
	def health() -> dict[str, object]:
		settings = get_settings()
		groq_settings = settings.groq
		return {
			"status": "ok",
			"semantic_matching": get_semantic_backend_status(),
			"research_assets": get_research_asset_summary(),
			"groq": {
				"configured": groq_settings is not None,
				"model": groq_settings.resume_parser_model if groq_settings is not None else None,
				"max_retries": groq_settings.max_retries if groq_settings is not None else 0,
				"backoff_base_seconds": (
					groq_settings.backoff_base_seconds if groq_settings is not None else 0.0
				),
			},
		}

	@app.get("/metrics", include_in_schema=False)
	def metrics() -> Response:
		return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

	return app


app = create_app()


__all__ = ["app", "create_app"]
