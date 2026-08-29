"""Per-user spend quotas for the routes that cost real money.

These are distinct from the throttles in ``rate_limit.py``, which exist to stop
credential attacks on ``/auth/*``. The quotas here exist to bound **cost**: every
route they guard either calls a paid LLM API (Groq) or consumes sandboxed compute
(Judge0, speech synthesis/transcription). A signed-in user hammering resume
parsing is not doing anything that looks like an attack — they would simply run
up a bill.

Quotas are keyed by authenticated user id rather than client IP. These are all
authenticated routes, so the account is the entity that actually maps to spend,
and an IP key would both punish shared networks and be trivially evaded.

Scope and limitations
---------------------
Counters live in this process's memory, inheriting the caveat documented in
``rate_limit.py``: with N uvicorn workers the effective allowance is N times the
configured value, and nothing is shared across instances. That is acceptable for
bounding a runaway bill — the order of magnitude is what matters — but a
multi-instance deployment that needs exact accounting should back these with
Redis or enforce them at the gateway.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from fastapi import Depends, HTTPException, status

from backend.api.auth import AuthenticatedUser, require_current_user
from backend.api.rate_limit import FixedWindowRateLimiter, RateLimitExceeded
from backend.config import get_settings

_LOGGER = logging.getLogger(__name__)

# Quota names, used both as the limiter registry key and as the prefix on the
# per-user counter key so separate quotas never share a budget.
RESUME_PARSE = "resume_parse"
ROLE_MATCH = "role_match"
DSA_EXECUTION = "dsa_execution"
VOICE = "voice"
# One "turn" is a single answer evaluated by Groq during a live interview round.
# Metered separately from the REST quotas because it is driven by WebSocket
# messages rather than requests, and a client can send those in a tight loop.
INTERVIEW_TURN = "interview_turn"

_lock = threading.Lock()
_limiters: dict[str, FixedWindowRateLimiter] = {}


def _max_events_for(quota_name: str) -> int:
	quotas = get_settings().quotas
	if quota_name == RESUME_PARSE:
		return quotas.resume_parse_max_per_hour
	if quota_name == ROLE_MATCH:
		return quotas.role_match_max_per_hour
	if quota_name == DSA_EXECUTION:
		return quotas.dsa_execution_max_per_hour
	if quota_name == VOICE:
		return quotas.voice_max_per_hour
	if quota_name == INTERVIEW_TURN:
		return quotas.interview_turn_max_per_hour
	raise ValueError(f"Unknown quota name: {quota_name!r}")


def _get_limiter(quota_name: str) -> FixedWindowRateLimiter:
	with _lock:
		limiter = _limiters.get(quota_name)
		if limiter is None:
			limiter = FixedWindowRateLimiter(
				max_events=_max_events_for(quota_name),
				window_seconds=get_settings().quotas.quota_window_seconds,
			)
			_limiters[quota_name] = limiter
		return limiter


def reset_quotas() -> None:
	"""Drop all cached quota limiters.

	Tests use this to get a clean slate and to pick up overridden settings.
	"""

	with _lock:
		_limiters.clear()


def try_consume_quota(quota_name: str, user_id: str) -> int | None:
	"""Consume one unit of ``quota_name`` for ``user_id``.

	Returns ``None`` when the call is allowed, or the number of seconds to wait
	when the allowance is exhausted. This non-raising form exists for callers
	that cannot use an HTTP exception — notably the interview WebSocket, which
	must report the refusal as a message frame and keep the socket open rather
	than tearing down a live interview.
	"""

	try:
		_get_limiter(quota_name).check(f"{quota_name}:{user_id}")
	except RateLimitExceeded as exc:
		_LOGGER.warning(
			"Quota %s exhausted for user %s; retry in %ss.",
			quota_name,
			user_id,
			exc.retry_after_seconds,
		)
		return exc.retry_after_seconds
	return None


def enforce_quota(quota_name: str, user_id: str) -> None:
	"""Consume one unit of ``quota_name`` for ``user_id``; raise 429 if exhausted."""

	retry_after_seconds = try_consume_quota(quota_name, user_id)
	if retry_after_seconds is None:
		return

	raise HTTPException(
		status_code=status.HTTP_429_TOO_MANY_REQUESTS,
		detail=(
			"You have reached the hourly limit for this operation. "
			f"Try again in {retry_after_seconds} seconds."
		),
		headers={"Retry-After": str(retry_after_seconds)},
	)


def quota_dependency(quota_name: str) -> Callable[..., AuthenticatedUser]:
	"""Build a FastAPI dependency that enforces a quota and returns the user.

	Use in place of ``Depends(require_current_user)`` so the route keeps its
	authenticated-user argument while also being metered:

	    current_user: AuthenticatedUser = Depends(quota_dependency(DSA_EXECUTION))
	"""

	def _dependency(
		current_user: AuthenticatedUser = Depends(require_current_user),
	) -> AuthenticatedUser:
		enforce_quota(quota_name, current_user.user_id)
		return current_user

	return _dependency


__all__ = [
	"DSA_EXECUTION",
	"INTERVIEW_TURN",
	"RESUME_PARSE",
	"ROLE_MATCH",
	"VOICE",
	"enforce_quota",
	"quota_dependency",
	"reset_quotas",
	"try_consume_quota",
]
