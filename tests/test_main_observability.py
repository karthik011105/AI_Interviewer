"""End-to-end checks for the observability wiring added to backend/main.py:

- RequestIdMiddleware tags every request/response with a correlation id.
- The catch-all exception handler turns a genuinely unhandled exception into a
  structured 500 instead of leaking a traceback to the client.
- CORS origins are actually driven by settings, not hardcoded.

These go through the real FastAPI stack via TestClient rather than calling
functions directly, since the thing being verified is the wiring itself.
"""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import config
from backend.main import create_app


class _AppTestBase(TestCase):
	def setUp(self) -> None:
		self._env_patcher = patch.dict(
			os.environ,
			{"AUTH_JWT_SECRET": "observability-test-secret-32-bytes-min"},
			clear=False,
		)
		self._env_patcher.start()
		config.reset_settings()

	def tearDown(self) -> None:
		self._env_patcher.stop()
		config.reset_settings()

	def _create_app(self):
		with patch("backend.main.warmup_semantic_encoder"), \
			 patch("backend.main.warmup_research_asset_registry"):
			return create_app()


class RequestIdMiddlewareTests(_AppTestBase):
	def test_generates_a_request_id_when_none_is_supplied(self) -> None:
		app = self._create_app()
		with TestClient(app) as client:
			response = client.get("/health")

		self.assertTrue(response.headers.get("X-Request-ID"))

	def test_echoes_back_an_inbound_request_id_instead_of_replacing_it(self) -> None:
		app = self._create_app()
		with TestClient(app) as client:
			response = client.get("/health", headers={"X-Request-ID": "client-supplied-id"})

		self.assertEqual(response.headers.get("X-Request-ID"), "client-supplied-id")

	def test_each_request_without_a_supplied_id_gets_a_distinct_one(self) -> None:
		app = self._create_app()
		with TestClient(app) as client:
			first = client.get("/health").headers["X-Request-ID"]
			second = client.get("/health").headers["X-Request-ID"]

		self.assertNotEqual(first, second)


class UnhandledExceptionHandlerTests(_AppTestBase):
	"""Starlette's ServerErrorMiddleware — which is what actually calls a handler
	registered for the base Exception class — always re-raises the original
	exception after building the response, specifically so a real ASGI server
	can log it. TestClient mirrors that by default (`raise_server_exceptions`),
	so these use `raise_server_exceptions=False` to inspect the response that
	was already sent instead of letting the exception surface as a test error.
	"""

	def test_returns_a_structured_500_instead_of_leaking_a_traceback(self) -> None:
		app = self._create_app()
		with patch(
			"backend.main.get_research_asset_summary",
			side_effect=RuntimeError("a secret internal detail"),
		):
			with TestClient(app, raise_server_exceptions=False) as client:
				response = client.get("/health")

		self.assertEqual(response.status_code, 500)
		payload = response.json()
		self.assertEqual(payload["error"]["code"], "internal_error")
		self.assertIn("request_id", payload["error"])
		self.assertNotIn("a secret internal detail", response.text)

	def test_the_500_carries_the_same_request_id_as_the_response_header(self) -> None:
		app = self._create_app()
		with patch(
			"backend.main.get_research_asset_summary",
			side_effect=RuntimeError("boom"),
		):
			with TestClient(app, raise_server_exceptions=False) as client:
				response = client.get("/health", headers={"X-Request-ID": "trace-me"})

		self.assertEqual(response.headers.get("X-Request-ID"), "trace-me")
		self.assertEqual(response.json()["error"]["request_id"], "trace-me")

	def test_a_normal_request_is_unaffected(self) -> None:
		app = self._create_app()
		with TestClient(app) as client:
			response = client.get("/health")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["status"], "ok")


class CorsConfigWiringTests(_AppTestBase):
	def test_a_configured_origin_is_allowed_on_preflight(self) -> None:
		with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": "https://app.example.com"}):
			config.reset_settings()
			app = self._create_app()
			with TestClient(app) as client:
				response = client.options(
					"/health",
					headers={
						"Origin": "https://app.example.com",
						"Access-Control-Request-Method": "GET",
					},
				)

		self.assertEqual(response.headers.get("access-control-allow-origin"), "https://app.example.com")

	def test_an_unconfigured_origin_is_rejected(self) -> None:
		with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": "https://app.example.com"}):
			config.reset_settings()
			app = self._create_app()
			with TestClient(app) as client:
				response = client.options(
					"/health",
					headers={
						"Origin": "https://evil.example.com",
						"Access-Control-Request-Method": "GET",
					},
				)

		self.assertNotIn("access-control-allow-origin", response.headers)

	def test_the_dev_server_origin_still_works_by_default(self) -> None:
		app = self._create_app()
		with TestClient(app) as client:
			response = client.options(
				"/health",
				headers={
					"Origin": "http://localhost:5173",
					"Access-Control-Request-Method": "GET",
				},
			)

		self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:5173")


class ErrorPathObservabilityTests(_AppTestBase):
	"""An unhandled exception must still be observable.

	Both behaviours checked here were broken and are easy to break again,
	because both depend on middleware *order* rather than on any single
	function. A handler registered for the base Exception class is invoked by
	Starlette's ServerErrorMiddleware, which wraps the user middleware stack
	from the outside — so before ErrorHandlingMiddleware existed, the 500 it
	produced had bypassed every middleware on the way out, and was therefore
	absent from the metrics and carried no CORS headers.
	"""

	@staticmethod
	def _counter(method: str, path: str, status: str) -> float:
		from prometheus_client import REGISTRY

		value = REGISTRY.get_sample_value(
			"http_requests_total",
			{"method": method, "path": path, "status": status},
		)
		# A label combination that has never been observed has no sample at
		# all, which is distinct from a sample of zero.
		return 0.0 if value is None else value

	def test_an_unhandled_exception_is_counted_as_a_500(self) -> None:
		app = self._create_app()
		before = self._counter("GET", "/health", "500")

		with patch(
			"backend.main.get_research_asset_summary",
			side_effect=RuntimeError("boom"),
		):
			with TestClient(app, raise_server_exceptions=False) as client:
				response = client.get("/health")

		self.assertEqual(response.status_code, 500)
		# The regression this guards against is not a wrong count but *no*
		# count: a route failing on every request used to look identical to a
		# route receiving no traffic.
		self.assertEqual(self._counter("GET", "/health", "500"), before + 1)

	def test_a_successful_request_is_counted_as_a_200(self) -> None:
		app = self._create_app()
		before = self._counter("GET", "/health", "200")

		with TestClient(app) as client:
			response = client.get("/health")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(self._counter("GET", "/health", "200"), before + 1)

	def test_a_500_still_carries_cors_headers(self) -> None:
		"""Without this the browser reports an opaque CORS failure instead of
		the JSON body, so the request_id that body exists to deliver never
		reaches the person reporting the bug. The deployed frontend and API are
		on different origins by design, so this applies to every 500.
		"""

		with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": "https://app.example.com"}):
			config.reset_settings()
			app = self._create_app()
			with patch(
				"backend.main.get_research_asset_summary",
				side_effect=RuntimeError("boom"),
			):
				with TestClient(app, raise_server_exceptions=False) as client:
					response = client.get(
						"/health", headers={"Origin": "https://app.example.com"}
					)

		self.assertEqual(response.status_code, 500)
		self.assertEqual(
			response.headers.get("access-control-allow-origin"),
			"https://app.example.com",
		)
		# The body must still be readable and still carry the correlation id.
		self.assertEqual(response.json()["error"]["code"], "internal_error")
		self.assertTrue(response.json()["error"]["request_id"])

	def test_a_500_response_carries_the_request_id_header(self) -> None:
		app = self._create_app()

		with patch(
			"backend.main.get_research_asset_summary",
			side_effect=RuntimeError("boom"),
		):
			with TestClient(app, raise_server_exceptions=False) as client:
				response = client.get("/health", headers={"X-Request-ID": "trace-500"})

		self.assertEqual(response.status_code, 500)
		self.assertEqual(response.headers.get("X-Request-ID"), "trace-500")
		self.assertEqual(response.json()["error"]["request_id"], "trace-500")

	def test_the_exception_detail_is_not_leaked_to_the_client(self) -> None:
		app = self._create_app()

		with patch(
			"backend.main.get_research_asset_summary",
			side_effect=RuntimeError("password=hunter2"),
		):
			with TestClient(app, raise_server_exceptions=False) as client:
				response = client.get("/health")

		self.assertEqual(response.status_code, 500)
		self.assertNotIn("hunter2", response.text)
		self.assertNotIn("RuntimeError", response.text)


class MiddlewareOrderTests(_AppTestBase):
	"""Order is the actual mechanism behind the two fixes above, so assert it
	directly rather than only through its symptoms.

	``add_middleware`` prepends, so ``app.user_middleware`` is already in
	outermost-first order. CORS must be outermost to decorate every response
	including errors, and the error handler must be innermost so that the
	response it builds still travels back out through the metrics middleware.
	"""

	def test_cors_is_outermost_and_error_handling_is_innermost(self) -> None:
		from backend.main import (
			ErrorHandlingMiddleware,
			RequestIdMiddleware,
			RequestMetricsMiddleware,
		)
		from fastapi.middleware.cors import CORSMiddleware

		app = self._create_app()
		stack = [m.cls for m in app.user_middleware]

		self.assertEqual(
			stack,
			[
				CORSMiddleware,
				RequestIdMiddleware,
				RequestMetricsMiddleware,
				ErrorHandlingMiddleware,
			],
		)

	def test_the_request_id_middleware_wraps_the_metrics_middleware(self) -> None:
		"""The metrics middleware logs through the same correlation id, so it
		has to run inside the one that sets it.
		"""

		from backend.main import RequestIdMiddleware, RequestMetricsMiddleware

		app = self._create_app()
		stack = [m.cls for m in app.user_middleware]

		self.assertLess(
			stack.index(RequestIdMiddleware),
			stack.index(RequestMetricsMiddleware),
		)
