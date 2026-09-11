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
		response = await call_next(request)
		duration = time.monotonic() - started_at

		route = request.scope.get("route")
		path = getattr(route, "path", None) or "unmatched"

		http_requests_total.labels(
			method=request.method, path=path, status=str(response.status_code)
		).inc()
		http_request_duration_seconds.labels(method=request.method, path=path).observe(duration)
		return response


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

	settings = get_settings()

	app = FastAPI(title="AI Interview Simulator", version="0.1.0", lifespan=_lifespan)
	app.add_middleware(
		CORSMiddleware,
		allow_origins=list(settings.cors.allowed_origins),
		allow_origin_regex=settings.cors.allow_origin_regex,
		allow_credentials=True,
		allow_methods=["*"],
		allow_headers=["*"],
	)
	app.add_middleware(RequestIdMiddleware)
	app.add_middleware(RequestMetricsMiddleware)

	@app.exception_handler(Exception)
	async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
		# FastAPI/Starlette resolve HTTPException (and its subclasses) to their
		# own handler first, so this only ever fires for genuinely unhandled
		# exceptions — the ones that would otherwise produce an opaque 500 with
		# no log line and no way to correlate a user report back to it.
		#
		# A handler registered for the base Exception class runs inside
		# ServerErrorMiddleware, which sits outside RequestIdMiddleware and
		# rebuilds its own Request only after that middleware's `finally` has
		# already reset the contextvar. `request.state` is backed by the same
		# ASGI scope object throughout the connection, so it survives where the
		# contextvar does not; the contextvar is kept as a fallback for the
		# unit tests that call this handler directly.
		request_id = getattr(request.state, "request_id", None) or request_id_var.get()
		_LOGGER.error(
			"Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc
		)
		# RequestIdMiddleware never gets a chance to stamp this response itself:
		# the exception propagated out of its `call_next`, past the line that
		# would have set this header, straight to ServerErrorMiddleware.
		response_headers = {"X-Request-ID": request_id} if request_id else None
		return JSONResponse(
			status_code=500,
			content={
				"error": {
					"code": "internal_error",
					"message": "An unexpected error occurred.",
					"request_id": request_id,
				}
			},
			headers=response_headers,
		)

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
