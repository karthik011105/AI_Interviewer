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

__all__ = [
	"groq_requests_total",
	"groq_request_duration_seconds",
	"judge0_requests_total",
	"judge0_request_duration_seconds",
]
