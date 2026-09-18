"""Prometheus metrics: HTTP-level request metrics, plus the two external
calls that cost money or can fail independently of an HTTP request's own
success — Groq and Judge0.

Deliberately not ``prometheus-fastapi-instrumentator`` for the HTTP side:
its latest release pins ``starlette<1.0.0``, which would silently downgrade
the starlette version this project actually resolves and runs on (verified
by installing it and watching pip do exactly that — see requirements.txt).
``RequestMetricsMiddleware`` in ``backend/main.py`` covers the same ground
— request count and latency by route/method/status — with no dependency
risk.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

http_requests_total = Counter(
	"http_requests_total",
	"HTTP requests handled, by method, route template, and status code.",
	["method", "path", "status"],
)

http_request_duration_seconds = Histogram(
	"http_request_duration_seconds",
	"HTTP request latency in seconds, by method and route template.",
	["method", "path"],
)

groq_requests_total = Counter(
	"groq_requests_total",
	"Groq API call attempts, by outcome.",
	["outcome"],  # success | rate_limited | error
)

groq_request_duration_seconds = Histogram(
	"groq_request_duration_seconds",
	"Groq API call latency in seconds, per attempt.",
)

judge0_requests_total = Counter(
	"judge0_requests_total",
	"Judge0 API call attempts, by outcome.",
	["outcome"],  # success | error
)

judge0_request_duration_seconds = Histogram(
	"judge0_request_duration_seconds",
	"Judge0 API call latency in seconds.",
)

# The interview WebSocket needs its own counter because it cannot reuse the
# HTTP ones. RequestMetricsMiddleware is a BaseHTTPMiddleware subclass, and
# those never see a websocket scope — so a live interview that fails is
# completely absent from http_requests_total. Without this, the single most
# important user-facing flow in the application was the one flow with no
# metrics at all.
#
# `round` is bounded to hr / technical / project_discussion by the route, so it
# cannot become an unbounded label the way a session id would.
interview_ws_sessions_total = Counter(
	"interview_ws_sessions_total",
	"Interview WebSocket sessions, by round and how the session ended.",
	["round", "outcome"],  # completed | client_disconnect | rejected | error
)

__all__ = [
	# The two HTTP metrics were missing from this list even though
	# backend/main.py imports them by name. Importing by name works either
	# way, so nothing was broken -- but a reader checking what this module
	# exports was told the wrong answer.
	"http_requests_total",
	"http_request_duration_seconds",
	"interview_ws_sessions_total",
	"groq_requests_total",
	"groq_request_duration_seconds",
	"judge0_requests_total",
	"judge0_request_duration_seconds",
]
