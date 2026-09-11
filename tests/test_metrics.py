"""Tests for backend/metrics.py and its two call sites (Groq, Judge0), plus
the HTTP-level RequestMetricsMiddleware in backend/main.py.

The Counter/Histogram objects in backend/metrics.py are module-level
singletons registered once at import time and shared across the whole test
process, so every test here reads a value *before* acting and asserts on the
delta rather than an absolute count — another test elsewhere in the suite
may have already incremented the same series.
"""

from __future__ import annotations

import os
import urllib.error
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from backend import config
from backend.dsa.code_executor import CodeExecutionError, _open_json_request
from backend.main import RequestMetricsMiddleware, create_app
from backend.nlp.groq_client import GroqCompletionError, GroqRateLimitError, create_chat_completion


def _sample(name: str, labels: dict[str, str]) -> float:
	return REGISTRY.get_sample_value(name, labels) or 0.0


class GroqMetricsTests(TestCase):
	def setUp(self) -> None:
		self.settings = type(
			"S", (), {"api_key": "k", "api_base_url": "https://example.invalid",
					  "timeout_seconds": 5.0, "max_retries": 2, "backoff_base_seconds": 0.0}
		)()

	def test_a_successful_call_records_one_success(self) -> None:
		before = _sample("groq_requests_total", {"outcome": "success"})
		client = type("C", (), {"chat": type("Chat", (), {"completions": type(
			"Comp", (), {"create": staticmethod(lambda **_kw: "ok")}
		)()})()})()
		with patch("backend.nlp.groq_client.get_groq_client", return_value=client):
			result = create_chat_completion(
				settings=self.settings, model="m", temperature=0.5,
				messages=[{"role": "user", "content": "hi"}],
			)
		self.assertEqual(result, "ok")
		self.assertEqual(_sample("groq_requests_total", {"outcome": "success"}), before + 1)

	def test_a_rate_limited_call_records_rate_limited_not_error(self) -> None:
		before_rl = _sample("groq_requests_total", {"outcome": "rate_limited"})
		before_err = _sample("groq_requests_total", {"outcome": "error"})

		def _raise(**_kw):
			raise RuntimeError("429 rate_limit_exceeded")

		client = type("C", (), {"chat": type("Chat", (), {"completions": type(
			"Comp", (), {"create": staticmethod(_raise)}
		)()})()})()
		with patch("backend.nlp.groq_client.get_groq_client", return_value=client):
			with self.assertRaises(GroqRateLimitError):
				create_chat_completion(
					settings=self.settings, model="m", temperature=0.5,
					messages=[{"role": "user", "content": "hi"}],
				)
		self.assertEqual(_sample("groq_requests_total", {"outcome": "rate_limited"}), before_rl + 1)
		self.assertEqual(_sample("groq_requests_total", {"outcome": "error"}), before_err)

	def test_exhausted_retries_record_one_error_per_attempt(self) -> None:
		before = _sample("groq_requests_total", {"outcome": "error"})

		def _raise(**_kw):
			raise RuntimeError("still broken")

		client = type("C", (), {"chat": type("Chat", (), {"completions": type(
			"Comp", (), {"create": staticmethod(_raise)}
		)()})()})()
		with patch("backend.nlp.groq_client.get_groq_client", return_value=client):
			with self.assertRaises(GroqCompletionError):
				create_chat_completion(
					settings=self.settings, model="m", temperature=0.5,
					messages=[{"role": "user", "content": "hi"}],
				)
		# max_retries=2 -> 3 attempts, each a recorded "error".
		self.assertEqual(_sample("groq_requests_total", {"outcome": "error"}), before + 3)


class Judge0MetricsTests(TestCase):
	def setUp(self) -> None:
		self._env_patcher = patch.dict(
			os.environ,
			{"AUTH_JWT_SECRET": "metrics-test-secret-padded-to-32-bytes-min"},
			clear=False,
		)
		self._env_patcher.start()
		config.reset_settings()

	def tearDown(self) -> None:
		self._env_patcher.stop()
		config.reset_settings()

	def test_a_successful_request_records_success(self) -> None:
		before = _sample("judge0_requests_total", {"outcome": "success"})

		class _FakeResponse:
			headers = type("H", (), {"get_content_charset": staticmethod(lambda: "utf-8")})()

			def read(self):
				return b'{"ok": true}'

			def __enter__(self):
				return self

			def __exit__(self, *exc):
				return False

		with patch("backend.dsa.code_executor.urllib_request.urlopen", return_value=_FakeResponse()):
			result = _open_json_request("http://judge0.invalid/health", method="GET")

		self.assertEqual(result, {"ok": True})
		self.assertEqual(_sample("judge0_requests_total", {"outcome": "success"}), before + 1)

	def test_an_http_error_records_error(self) -> None:
		before = _sample("judge0_requests_total", {"outcome": "error"})

		def _raise(*_args, **_kwargs):
			raise urllib.error.HTTPError(
				"http://judge0.invalid/health", 502, "Bad Gateway", None, None
			)

		with patch("backend.dsa.code_executor.urllib_request.urlopen", side_effect=_raise):
			with self.assertRaises(CodeExecutionError):
				_open_json_request("http://judge0.invalid/health", method="GET")

		self.assertEqual(_sample("judge0_requests_total", {"outcome": "error"}), before + 1)

	def test_a_connection_error_records_error(self) -> None:
		before = _sample("judge0_requests_total", {"outcome": "error"})

		def _raise(*_args, **_kwargs):
			raise urllib.error.URLError("connection refused")

		with patch("backend.dsa.code_executor.urllib_request.urlopen", side_effect=_raise):
			with self.assertRaises(CodeExecutionError):
				_open_json_request("http://judge0.invalid/health", method="GET")

		self.assertEqual(_sample("judge0_requests_total", {"outcome": "error"}), before + 1)


class RequestMetricsMiddlewareTests(IsolatedAsyncioTestCase):
	"""Exercises the middleware directly against a small throwaway app, rather
	than the full application, so the route-template behaviour can be checked
	without needing auth or any of the real routers.
	"""

	async def test_labels_by_route_template_not_the_resolved_path(self) -> None:
		app = FastAPI()

		@app.get("/widgets/{widget_id}")
		def _get_widget(widget_id: str):
			return {"id": widget_id}

		app.add_middleware(RequestMetricsMiddleware)

		before_template = _sample(
			"http_requests_total", {"method": "GET", "path": "/widgets/{widget_id}", "status": "200"}
		)
		before_raw = _sample(
			"http_requests_total", {"method": "GET", "path": "/widgets/abc123", "status": "200"}
		)

		with TestClient(app) as client:
			response = client.get("/widgets/abc123")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			_sample("http_requests_total", {"method": "GET", "path": "/widgets/{widget_id}", "status": "200"}),
			before_template + 1,
		)
		# The raw resolved path must never become a label value — that's an
		# unbounded cardinality source (one series per id ever requested).
		self.assertEqual(
			_sample("http_requests_total", {"method": "GET", "path": "/widgets/abc123", "status": "200"}),
			before_raw,
		)

	async def test_an_unmatched_route_is_labelled_unmatched_not_the_404_path(self) -> None:
		app = FastAPI()
		app.add_middleware(RequestMetricsMiddleware)

		before = _sample("http_requests_total", {"method": "GET", "path": "unmatched", "status": "404"})

		with TestClient(app) as client:
			response = client.get("/this-route-does-not-exist")

		self.assertEqual(response.status_code, 404)
		self.assertEqual(
			_sample("http_requests_total", {"method": "GET", "path": "unmatched", "status": "404"}),
			before + 1,
		)


class MetricsEndpointTests(TestCase):
	def setUp(self) -> None:
		self._env_patcher = patch.dict(
			os.environ,
			{"AUTH_JWT_SECRET": "metrics-endpoint-test-secret-32-bytes-min"},
			clear=False,
		)
		self._env_patcher.start()
		config.reset_settings()

	def tearDown(self) -> None:
		self._env_patcher.stop()
		config.reset_settings()

	def test_metrics_endpoint_exposes_prometheus_text_format(self) -> None:
		with patch("backend.main.warmup_semantic_encoder"), \
			 patch("backend.main.warmup_research_asset_registry"):
			app = create_app()
			with TestClient(app) as client:
				client.get("/health")
				response = client.get("/metrics")

		self.assertEqual(response.status_code, 200)
		self.assertIn("text/plain", response.headers["content-type"])
		self.assertIn("http_requests_total", response.text)
		self.assertIn('path="/health"', response.text)

	def test_scraping_metrics_itself_is_not_counted(self) -> None:
		with patch("backend.main.warmup_semantic_encoder"), \
			 patch("backend.main.warmup_research_asset_registry"):
			app = create_app()
			with TestClient(app) as client:
				client.get("/metrics")
				before = _sample("http_requests_total", {"method": "GET", "path": "/metrics", "status": "200"})
				client.get("/metrics")
				after = _sample("http_requests_total", {"method": "GET", "path": "/metrics", "status": "200"})

		self.assertEqual(after, before)
