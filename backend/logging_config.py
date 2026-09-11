"""Structured (JSON) logging and optional error tracking for the backend.

Logging is otherwise ad hoc across this codebase (some modules configure
nothing and rely on Python's default handler, one route used a raw ``print()``
instead of a logger). This module gives the process exactly one place that
decides log format and destination, and a request id that flows from the
inbound HTTP request through every log line emitted while handling it.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import datetime, timezone

# Set by RequestIdMiddleware (backend.main) for the lifetime of one request,
# and read back by _RequestIdFilter so every log line emitted while handling
# that request — from any module — carries the same id.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
	"request_id", default=None
)


class _RequestIdFilter(logging.Filter):
	def filter(self, record: logging.LogRecord) -> bool:
		record.request_id = request_id_var.get()
		return True


class _JsonFormatter(logging.Formatter):
	def format(self, record: logging.LogRecord) -> str:
		payload: dict[str, object] = {
			"timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
			"level": record.levelname,
			"logger": record.name,
			"message": record.getMessage(),
		}
		request_id = getattr(record, "request_id", None)
		if request_id:
			payload["request_id"] = request_id
		if record.exc_info:
			payload["exception"] = self.formatException(record.exc_info)
		return json.dumps(payload, default=str)


_logging_configured = False


def configure_logging(env: dict[str, str] | None = None) -> None:
	"""Configure process-wide JSON logging to stdout. Safe to call more than once.

	Log level is read from ``LOG_LEVEL`` (default ``INFO``). Every log record,
	regardless of which module emitted it, is formatted as one JSON object per
	line — this is what lets a log aggregator (or `jq`, in the meantime) filter
	and query logs instead of grepping free-text lines.
	"""

	global _logging_configured
	if _logging_configured:
		return
	_logging_configured = True

	source = env if env is not None else os.environ
	level_name = (source.get("LOG_LEVEL") or "INFO").strip().upper()
	level = getattr(logging, level_name, None)
	if not isinstance(level, int):
		level = logging.INFO

	handler = logging.StreamHandler(sys.stdout)
	handler.setFormatter(_JsonFormatter())
	handler.addFilter(_RequestIdFilter())

	root = logging.getLogger()
	root.handlers = [handler]
	root.setLevel(level)


def reset_logging_for_tests() -> None:
	"""Allow tests to reconfigure logging after patching environment variables."""

	global _logging_configured
	_logging_configured = False


_error_tracking_configured = False


def configure_error_tracking(env: dict[str, str] | None = None) -> bool:
	"""Initialize Sentry if ``SENTRY_DSN`` is set. No-op (and no import) otherwise.

	Returns True if Sentry was actually initialized, so startup code and tests
	can tell whether error tracking is active without re-reading the environment.
	"""

	global _error_tracking_configured
	if _error_tracking_configured:
		return True

	source = env if env is not None else os.environ
	dsn = (source.get("SENTRY_DSN") or "").strip()
	if not dsn:
		return False

	try:
		import sentry_sdk
	except ModuleNotFoundError:
		logging.getLogger(__name__).warning(
			"SENTRY_DSN is set but the sentry-sdk package is not installed; "
			"error tracking is disabled."
		)
		return False

	traces_sample_rate = 0.0
	raw_rate = (source.get("SENTRY_TRACES_SAMPLE_RATE") or "").strip()
	if raw_rate:
		try:
			traces_sample_rate = float(raw_rate)
		except ValueError:
			logging.getLogger(__name__).warning(
				"SENTRY_TRACES_SAMPLE_RATE=%r is not a number; using 0.0.", raw_rate
			)

	sentry_sdk.init(
		dsn=dsn,
		environment=(source.get("APP_ENV") or "development").strip(),
		traces_sample_rate=traces_sample_rate,
	)
	_error_tracking_configured = True
	return True


def reset_error_tracking_for_tests() -> None:
	global _error_tracking_configured
	_error_tracking_configured = False


__all__ = [
	"configure_logging",
	"configure_error_tracking",
	"request_id_var",
	"reset_logging_for_tests",
	"reset_error_tracking_for_tests",
]
