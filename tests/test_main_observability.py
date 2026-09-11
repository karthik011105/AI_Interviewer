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
