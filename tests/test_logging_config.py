"""Tests for backend/logging_config.py.

This module is the only place that decides log format/destination and whether
Sentry gets initialized, so its behaviour is worth locking down directly rather
than only through whatever happens to exercise it via the app.
"""

from __future__ import annotations

import json
import logging
import sys
from unittest import TestCase
from unittest.mock import patch

from backend.logging_config import (
	_JsonFormatter,
	_RequestIdFilter,
	configure_error_tracking,
	configure_logging,
	request_id_var,
	reset_error_tracking_for_tests,
	reset_logging_for_tests,
)


class JsonFormatterTests(TestCase):
	def _make_record(self, **overrides) -> logging.LogRecord:
		defaults = dict(
			name="my.logger",
			level=logging.INFO,
			pathname=__file__,
			lineno=1,
			msg="hello %s",
			args=("world",),
			exc_info=None,
		)
		defaults.update(overrides)
		return logging.LogRecord(**defaults)

	def test_formats_a_plain_record_as_json_with_the_expected_fields(self) -> None:
		payload = json.loads(_JsonFormatter().format(self._make_record()))

		self.assertEqual(payload["level"], "INFO")
		self.assertEqual(payload["logger"], "my.logger")
		self.assertEqual(payload["message"], "hello world")
		self.assertIn("timestamp", payload)
		self.assertNotIn("request_id", payload)

	def test_includes_the_request_id_when_the_filter_has_attached_one(self) -> None:
		record = self._make_record()
		record.request_id = "req-abc"

		payload = json.loads(_JsonFormatter().format(record))

		self.assertEqual(payload["request_id"], "req-abc")

	def test_includes_a_formatted_traceback_for_exceptions(self) -> None:
		try:
			raise ValueError("boom")
		except ValueError:
			record = self._make_record(level=logging.ERROR, msg="failed", args=(), exc_info=sys.exc_info())

		payload = json.loads(_JsonFormatter().format(record))

		self.assertIn("ValueError: boom", payload["exception"])


class RequestIdFilterTests(TestCase):
	def test_attaches_the_current_contextvar_value_to_the_record(self) -> None:
		record = logging.LogRecord(
			name="x", level=logging.INFO, pathname=__file__, lineno=1,
			msg="m", args=(), exc_info=None,
		)
		token = request_id_var.set("req-123")
		try:
			_RequestIdFilter().filter(record)
		finally:
			request_id_var.reset(token)

		self.assertEqual(record.request_id, "req-123")

	def test_attaches_none_when_no_request_is_in_flight(self) -> None:
		record = logging.LogRecord(
			name="x", level=logging.INFO, pathname=__file__, lineno=1,
			msg="m", args=(), exc_info=None,
		)
		_RequestIdFilter().filter(record)

		self.assertIsNone(record.request_id)


class ConfigureLoggingTests(TestCase):
	def tearDown(self) -> None:
		reset_logging_for_tests()
		# Restore a sane default so later tests in this process aren't left
		# with an elevated log level from a test that ran before them.
		configure_logging(env={})

	def test_reads_the_level_from_env(self) -> None:
		reset_logging_for_tests()
		configure_logging(env={"LOG_LEVEL": "WARNING"})

		self.assertEqual(logging.getLogger().level, logging.WARNING)

	def test_defaults_to_info_when_unset(self) -> None:
		reset_logging_for_tests()
		configure_logging(env={})

		self.assertEqual(logging.getLogger().level, logging.INFO)

	def test_an_invalid_level_falls_back_to_info_instead_of_raising(self) -> None:
		reset_logging_for_tests()
		configure_logging(env={"LOG_LEVEL": "NOT_A_LEVEL"})

		self.assertEqual(logging.getLogger().level, logging.INFO)

	def test_is_a_noop_on_a_second_call_without_a_reset(self) -> None:
		reset_logging_for_tests()
		configure_logging(env={"LOG_LEVEL": "WARNING"})
		configure_logging(env={"LOG_LEVEL": "ERROR"})

		self.assertEqual(logging.getLogger().level, logging.WARNING)


class ErrorTrackingTests(TestCase):
	def tearDown(self) -> None:
		reset_error_tracking_for_tests()

	def test_is_a_noop_without_a_dsn(self) -> None:
		with patch("sentry_sdk.init") as mock_init:
			initialized = configure_error_tracking(env={})

		self.assertFalse(initialized)
		mock_init.assert_not_called()

	def test_initializes_sentry_when_a_dsn_is_configured(self) -> None:
		with patch("sentry_sdk.init") as mock_init:
			initialized = configure_error_tracking(
				env={"SENTRY_DSN": "https://key@sentry.example/1", "APP_ENV": "production"}
			)

		self.assertTrue(initialized)
		mock_init.assert_called_once()
		_, kwargs = mock_init.call_args
		self.assertEqual(kwargs["dsn"], "https://key@sentry.example/1")
		self.assertEqual(kwargs["environment"], "production")

	def test_is_a_noop_on_a_second_call_without_a_reset(self) -> None:
		with patch("sentry_sdk.init") as mock_init:
			configure_error_tracking(env={"SENTRY_DSN": "https://key@sentry.example/1"})
			configure_error_tracking(env={"SENTRY_DSN": "https://key@sentry.example/1"})

		mock_init.assert_called_once()

	def test_an_invalid_sample_rate_falls_back_to_zero_instead_of_raising(self) -> None:
		with patch("sentry_sdk.init") as mock_init:
			configure_error_tracking(
				env={
					"SENTRY_DSN": "https://key@sentry.example/1",
					"SENTRY_TRACES_SAMPLE_RATE": "not-a-number",
				}
			)

		_, kwargs = mock_init.call_args
		self.assertEqual(kwargs["traces_sample_rate"], 0.0)
