"""Liveness, readiness, and the metrics endpoint's access control.

Three related changes are covered here.

``/health`` is liveness only and must stay that way. A liveness probe that
fails when MongoDB blips would restart a perfectly healthy process, turning a
brief database problem into a restart loop — and the container HEALTHCHECK uses
this endpoint.

``/ready`` is new and does reach MongoDB, closing the "/health does not check
Mongo" gap that PRODUCTION_READINESS.md §14 carried from the first assessment.
It returns 503 when the database is unreachable so a load balancer stops
routing to an instance that can only produce errors.

``/metrics`` was served to anyone who asked. Its payload names every route
template in the application and its traffic volume, which is a free map of the
API surface. It is now gated, and answers 404 rather than 403 when nobody is
authorized so a caller cannot even confirm it exists.
"""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import config
from backend import main as main_module


class _AppTestBase(TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(
            os.environ,
            {"AUTH_JWT_SECRET": "health-test-secret-32-bytes-minimum"},
            clear=False,
        )
        self._env.start()
        for variable in ("METRICS_TOKEN", "METRICS_PUBLIC"):
            os.environ.pop(variable, None)
        config.reset_settings()

    def tearDown(self) -> None:
        self._env.stop()
        config.reset_settings()

    def _client(self) -> TestClient:
        with patch("backend.main.warmup_semantic_encoder"), patch(
            "backend.main.warmup_research_asset_registry"
        ):
            return TestClient(main_module.create_app())


class LivenessTests(_AppTestBase):
    def test_health_reports_ok(self) -> None:
        with self._client() as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_health_does_not_disclose_filenames(self) -> None:
        """It used to return `registered_model_names` and
        `registered_notebook_names` — real paths from the server's filesystem —
        on an unauthenticated endpoint."""

        with self._client() as client:
            body = client.get("/health").text

        for leak in ("registered_model_names", "registered_notebook_names", ".pth", ".ipynb"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, body)

    def test_health_stays_up_when_the_database_is_unreachable(self) -> None:
        """The critical property. This endpoint backs the container
        HEALTHCHECK, so failing it on a database blip would restart a process
        that is serving perfectly well."""

        with patch.dict(
            os.environ, {"MONGO_URI": "mongodb://127.0.0.1:59999"}, clear=False
        ):
            config.reset_settings()
            with self._client() as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)


class ReadinessTests(_AppTestBase):
    def test_ready_returns_503_when_the_database_is_unreachable(self) -> None:
        with patch.dict(
            os.environ, {"MONGO_URI": "mongodb://127.0.0.1:59999"}, clear=False
        ):
            config.reset_settings()
            with self._client() as client:
                response = client.get("/ready")

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["status"], "not_ready")
        self.assertFalse(payload["checks"]["mongo"]["ok"])

    def test_the_failure_does_not_leak_the_connection_string(self) -> None:
        """A pymongo error message embeds the URI, which in a real deployment
        carries credentials. Only the exception type is reported."""

        with patch.dict(
            os.environ,
            {"MONGO_URI": "mongodb://someuser:hunter2@127.0.0.1:59999"},
            clear=False,
        ):
            config.reset_settings()
            with self._client() as client:
                body = client.get("/ready").text

        self.assertNotIn("hunter2", body)
        self.assertNotIn("someuser", body)
        self.assertNotIn("59999", body)

    def test_ready_reports_the_checks_it_performed(self) -> None:
        with patch.dict(
            os.environ, {"MONGO_URI": "mongodb://127.0.0.1:59999"}, clear=False
        ):
            config.reset_settings()
            with self._client() as client:
                checks = client.get("/ready").json()["checks"]

        for expected in ("mongo", "groq", "semantic_matching", "research_assets"):
            with self.subTest(check=expected):
                self.assertIn(expected, checks)

    def test_ready_does_not_probe_judge0(self) -> None:
        """Judge0 is optional and reaching it is an HTTP round trip with a
        15 second timeout. A readiness probe polled every few seconds must not
        make a call that slow, or the probe becomes the outage."""

        import inspect

        source = inspect.getsource(main_module.create_app)
        ready_source = source[source.index('@app.get("/ready")') :]
        ready_source = ready_source[: ready_source.index('@app.get("/metrics"')]

        self.assertNotIn("judge0_health_check", ready_source)


class MetricsAccessControlTests(_AppTestBase):
    def test_metrics_is_404_when_nothing_is_configured(self) -> None:
        """Fails closed. 404 rather than 403, because a 403 confirms there is
        something here worth scraping."""

        with self._client() as client:
            response = client.get("/metrics")

        self.assertEqual(response.status_code, 404)

    def test_a_correct_bearer_token_is_accepted(self) -> None:
        with patch.dict(os.environ, {"METRICS_TOKEN": "scrape-me"}, clear=False):
            with self._client() as client:
                response = client.get(
                    "/metrics", headers={"Authorization": "Bearer scrape-me"}
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("http_requests_total", response.text)

    def test_a_wrong_or_missing_token_is_refused(self) -> None:
        with patch.dict(os.environ, {"METRICS_TOKEN": "scrape-me"}, clear=False):
            with self._client() as client:
                for label, headers in (
                    ("no header", {}),
                    ("wrong token", {"Authorization": "Bearer nope"}),
                    ("wrong scheme", {"Authorization": "Basic scrape-me"}),
                    ("bare token", {"Authorization": "scrape-me"}),
                ):
                    with self.subTest(case=label):
                        self.assertEqual(
                            client.get("/metrics", headers=headers).status_code, 404
                        )

    def test_metrics_public_restores_open_access(self) -> None:
        """An escape hatch for someone who genuinely wants it, rather than
        forcing a token on a deployment scraping over a private network."""

        with patch.dict(os.environ, {"METRICS_PUBLIC": "true"}, clear=False):
            with self._client() as client:
                response = client.get("/metrics")

        self.assertEqual(response.status_code, 200)

    def test_public_wins_over_an_absent_token(self) -> None:
        for value, expected_public in (
            ("true", True), ("1", True), ("yes", True), ("on", True),
            ("false", False), ("", False), ("nonsense", False),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    main_module._metrics_is_public({"METRICS_PUBLIC": value}),
                    expected_public,
                )

    def test_the_open_metrics_warning_fires_and_names_the_alternative(self) -> None:
        import logging

        with self.assertLogs(
            logging.getLogger(main_module.__name__), level="WARNING"
        ) as captured:
            fired = main_module.warn_if_metrics_are_public({"METRICS_PUBLIC": "true"})

        self.assertTrue(fired)
        joined = "\n".join(captured.output)
        self.assertIn("METRICS_TOKEN", joined)

    def test_no_warning_when_metrics_are_protected(self) -> None:
        self.assertFalse(main_module.warn_if_metrics_are_public({}))
        self.assertFalse(
            main_module.warn_if_metrics_are_public({"METRICS_TOKEN": "scrape-me"})
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
