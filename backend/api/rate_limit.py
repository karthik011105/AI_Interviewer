"""In-process rate limiting and failed-sign-in backoff for the auth routes.

Two complementary protections live here:

``FixedWindowRateLimiter``
    A per-client-IP request budget. This blunts scripted traffic against the
    auth endpoints generally, including signup spam.

``FailureTracker``
    A per-account backoff applied after consecutive failed sign-ins. This is the
    protection that actually matters against credential stuffing, because an
    attacker targeting one known account is not slowed much by a per-IP budget
    they can rotate around.

Scope and limitations
---------------------
The two classes below hold state in memory in this process, so under multiple
uvicorn workers each worker keeps its own counters and the effective limit is
multiplied by the worker count.

That swap is no longer hypothetical: ``rate_limit_store.py`` provides
MongoDB-backed equivalents with the same interface, and
``create_rate_limiter`` / ``create_failure_tracker`` below choose between them
from ``RATE_LIMIT_BACKEND``. Memory stays the default so local development and
the test suite need no database; ``backend/Dockerfile`` sets ``mongo``, and
``warn_if_throttles_are_process_local`` complains at startup about the one
genuinely dangerous combination -- memory plus more than one worker.

Both classes are safe to call from FastAPI's sync threadpool: all mutation
happens under a lock.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import Callable, Deque, Dict, Protocol


class RateLimiter(Protocol):
    """The contract both the in-process and MongoDB limiters satisfy.

    Stated as a Protocol rather than a base class because the two
    implementations share no code — one holds a dict under a lock, the other
    issues a single atomic aggregation update — and what matters is that call
    sites can be handed either without caring which.
    """

    def check(self, key: str) -> None:
        """Record an attempt; raise RateLimitExceeded if over budget."""

    def reset(self, key: str) -> None:
        """Forget one key's history."""

    def clear(self) -> None:
        """Forget everything. Used by tests."""

    def tracked_key_count(self) -> int:
        """How many keys are currently held."""


class FailureBackoff(Protocol):
    """The contract both failed-sign-in trackers satisfy."""

    def check(self, key: str) -> None:
        """Raise RateLimitExceeded while the key is locked out."""

    def record_failure(self, key: str) -> None:
        """Count one failed attempt against the key."""

    def reset(self, key: str) -> None:
        """Clear the streak, on a successful sign-in."""

    def clear(self) -> None:
        """Forget everything. Used by tests."""

    def tracked_key_count(self) -> int:
        """How many keys are currently held."""


class RateLimitExceeded(Exception):
    """Raised when a caller has exhausted its budget.

    ``retry_after_seconds`` is always at least 1 so it can be sent directly as a
    ``Retry-After`` header, which must be a positive integer.
    """

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = max(1, int(round(retry_after_seconds)))
        super().__init__(
            f"Too many attempts. Retry in {self.retry_after_seconds} seconds."
        )


class FixedWindowRateLimiter:
    """Allow at most ``max_events`` per ``window_seconds`` for a given key.

    Implemented as a sliding window over recent event timestamps rather than a
    bucket that resets on a wall-clock boundary, so a caller cannot burst twice
    by straddling a boundary.
    """

    def __init__(
        self,
        *,
        max_events: int,
        window_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1.")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive.")
        self._max_events = max_events
        self._window_seconds = float(window_seconds)
        # monotonic, so the limiter is unaffected by system clock adjustments.
        self._clock = clock or time.monotonic
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep_at: float | None = None

    def _prune(self, timestamps: Deque[float], now: float) -> None:
        cutoff = now - self._window_seconds
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

    def _maybe_sweep(self, now: float) -> None:
        """Drop keys with no events left inside the window.

        Without this, ``_events`` grows with the number of distinct keys *ever*
        seen rather than the number currently active, because a key's deque is
        only pruned when that same key is checked again — and a caller who
        never returns is never checked again. Measured before this existed:
        10,000 one-off callers left 10,000 entries behind, still holding their
        timestamps, long after every window had lapsed. For the auth limiter
        the key is a client address and for the quota limiters it is a user id,
        so in a long-running process that is unbounded growth driven by
        ordinary traffic.

        Runs at most once per window, under the caller's lock. A sweep is O(n)
        in tracked keys, and bounding it to once per window keeps the amortised
        cost per check negligible while still ensuring an idle key cannot
        outlive its window by more than one sweep interval.
        """

        if self._last_sweep_at is not None and now - self._last_sweep_at < self._window_seconds:
            return
        self._last_sweep_at = now

        cutoff = now - self._window_seconds
        stale = [
            key
            for key, timestamps in self._events.items()
            if not timestamps or timestamps[-1] <= cutoff
        ]
        for key in stale:
            del self._events[key]

    def check(self, key: str) -> None:
        """Record an attempt for ``key``; raise if it exceeds the budget.

        A rejected attempt is not recorded, so a caller that keeps hammering
        while blocked does not extend its own lockout indefinitely.
        """
        now = self._clock()
        with self._lock:
            self._maybe_sweep(now)
            timestamps = self._events.setdefault(key, deque())
            self._prune(timestamps, now)
            if len(timestamps) >= self._max_events:
                retry_after = self._window_seconds - (now - timestamps[0])
                raise RateLimitExceeded(retry_after)
            timestamps.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._last_sweep_at = None

    def tracked_key_count(self) -> int:
        """Number of keys currently held. Exposed for tests and diagnostics."""

        with self._lock:
            return len(self._events)


class FailureTracker:
    """Lock a key out for a period after consecutive failures.

    Callers should invoke :meth:`check` before verifying a credential,
    :meth:`record_failure` when verification fails, and :meth:`reset` on
    success.

    The key should be derived from the *submitted* identifier rather than from a
    looked-up account, so that attempts against addresses with no account are
    throttled identically. Doing otherwise turns the lockout into a user
    enumeration oracle.
    """

    def __init__(
        self,
        *,
        max_failures: int,
        lockout_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be at least 1.")
        if lockout_seconds <= 0:
            raise ValueError("lockout_seconds must be positive.")
        self._max_failures = max_failures
        self._lockout_seconds = float(lockout_seconds)
        self._clock = clock or time.monotonic
        # key -> (consecutive failure count, timestamp of most recent failure)
        self._failures: Dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self._last_sweep_at: float | None = None

    def _maybe_sweep(self, now: float) -> None:
        """Drop entries whose lockout window has fully elapsed.

        ``check`` already forgets an expired streak, but only for the key being
        checked. An address that fails a few sign-ins and never returns is
        never checked again, so its entry was retained forever: 10,000 failed
        sign-ins against 10,000 distinct identifiers left 10,000 entries in
        place long after every lockout had expired. Since the key is the
        *submitted* email (deliberately, so that unknown accounts throttle
        identically and the lockout is not an enumeration oracle), an attacker
        enumerating addresses was also growing this dict.

        Runs at most once per lockout window, under the caller's lock.
        """

        if (
            self._last_sweep_at is not None
            and now - self._last_sweep_at < self._lockout_seconds
        ):
            return
        self._last_sweep_at = now

        stale = [
            key
            for key, (_, last_failure_at) in self._failures.items()
            if now - last_failure_at >= self._lockout_seconds
        ]
        for key in stale:
            del self._failures[key]

    def check(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            self._maybe_sweep(now)
            entry = self._failures.get(key)
            if entry is None:
                return
            count, last_failure_at = entry
            elapsed = now - last_failure_at
            if elapsed >= self._lockout_seconds:
                # The lockout window has passed; forget the streak entirely.
                self._failures.pop(key, None)
                return
            if count >= self._max_failures:
                raise RateLimitExceeded(self._lockout_seconds - elapsed)

    def record_failure(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            self._maybe_sweep(now)
            count, last_failure_at = self._failures.get(key, (0, now))
            if now - last_failure_at >= self._lockout_seconds:
                count = 0
            self._failures[key] = (count + 1, now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()
            self._last_sweep_at = None

    def tracked_key_count(self) -> int:
        """Number of keys currently held. Exposed for tests and diagnostics."""

        with self._lock:
            return len(self._failures)


__all__ = [
    "FailureBackoff",
    "FailureTracker",
    "RateLimiter",
    "FixedWindowRateLimiter",
    "RateLimitExceeded",
]


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

RATE_LIMIT_BACKEND_ENV_VAR = "RATE_LIMIT_BACKEND"


def rate_limit_backend(env: Mapping[str, str] | None = None) -> str:
    """Which throttle store to use: ``"memory"`` (default) or ``"mongo"``.

    Defaults to memory so that local development and the test suite need no
    database — the suite deliberately points ``MONGO_URI`` at a dead port, and
    a throttle that required Mongo would turn every auth test into a
    connection error.

    A real deployment sets ``RATE_LIMIT_BACKEND=mongo``. ``backend/Dockerfile``
    does. Memory is safe for a single worker and actively wrong for more than
    one, which is what ``warn_if_throttles_are_process_local`` exists to say
    out loud.
    """

    source = os.environ if env is None else env
    value = (source.get(RATE_LIMIT_BACKEND_ENV_VAR) or "memory").strip().casefold()
    return "mongo" if value == "mongo" else "memory"


def create_rate_limiter(*, max_events: int, window_seconds: float):
    """Build the configured rate limiter."""

    if rate_limit_backend() == "mongo":
        from backend.api.rate_limit_store import (
            RATE_LIMIT_COLLECTION,
            MongoFixedWindowRateLimiter,
        )
        from backend.database.mongo_client import get_repository

        return MongoFixedWindowRateLimiter(
            max_events=max_events,
            window_seconds=window_seconds,
            collection=get_repository().db[RATE_LIMIT_COLLECTION],
        )

    return FixedWindowRateLimiter(
        max_events=max_events, window_seconds=window_seconds
    )


def create_failure_tracker(*, max_failures: int, lockout_seconds: float):
    """Build the configured failed-sign-in tracker."""

    if rate_limit_backend() == "mongo":
        from backend.api.rate_limit_store import (
            FAILURE_TRACKER_COLLECTION,
            MongoFailureTracker,
        )
        from backend.database.mongo_client import get_repository

        return MongoFailureTracker(
            max_failures=max_failures,
            lockout_seconds=lockout_seconds,
            collection=get_repository().db[FAILURE_TRACKER_COLLECTION],
        )

    return FailureTracker(
        max_failures=max_failures, lockout_seconds=lockout_seconds
    )


def warn_if_throttles_are_process_local(env: Mapping[str, str] | None = None) -> bool:
    """Warn when the in-memory store is paired with more than one worker.

    This is the specific misconfiguration the in-process limiters have always
    carried a caveat about: with N workers each keeps its own counters, so every
    configured limit is silently multiplied by N. A deployment that scales
    workers for throughput without switching the store gets N times the auth
    budget and N times the spend quota, with nothing to indicate it.

    Returns True when the warning fired, so startup code and tests can tell.
    """

    source = os.environ if env is None else env
    if rate_limit_backend(source) == "mongo":
        return False

    raw_workers = (source.get("UVICORN_WORKERS") or "1").strip()
    try:
        workers = int(raw_workers)
    except ValueError:
        workers = 1
    if workers <= 1:
        return False

    logging.getLogger(__name__).warning(
        "UVICORN_WORKERS=%s but %s is 'memory', so rate limits and spend quotas "
        "are per-process: every configured limit is effectively multiplied by %s. "
        "Set %s=mongo to share them.",
        workers,
        RATE_LIMIT_BACKEND_ENV_VAR,
        workers,
        RATE_LIMIT_BACKEND_ENV_VAR,
    )
    return True


__all__ += [
    "RATE_LIMIT_BACKEND_ENV_VAR",
    "create_failure_tracker",
    "create_rate_limiter",
    "rate_limit_backend",
    "warn_if_throttles_are_process_local",
]
