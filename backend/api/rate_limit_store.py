"""MongoDB-backed throttle state, so limits are shared rather than per-process.

The in-process implementations in ``rate_limit.py`` are correct for a single
worker, and they document their own limitation: with N uvicorn workers each
worker keeps its own counters, so the effective limit is multiplied by N, and
nothing is shared across instances. A restart also resets every counter, which
matters more than it sounds — the spend quotas exist to bound a bill, and
"wait for the next deploy" is not a difficulty an attacker respects.

These classes are drop-in replacements with the same interface, backed by a
single collection each, so the throttles survive a restart and hold across
workers.

Two deliberate design points:

**Wall clock, not monotonic.** The in-process limiters use ``time.monotonic()``,
which is the right choice there because it is immune to clock adjustments. It is
useless for shared state: monotonic clocks have a per-process origin, so two
workers cannot compare values. These use epoch seconds, which means a large NTP
correction could briefly widen or narrow a window. That is the price of sharing
state at all, and it is a far smaller problem than counters that do not.

**One round trip, and atomic.** Counting and then deciding would be a
time-of-check/time-of-use race: two concurrent requests could both observe the
count below the limit and both be admitted. Instead each check is a single
``find_one_and_update`` whose aggregation pipeline prunes expired entries and
conditionally appends the new one *inside the server*. The caller learns whether
it was admitted by looking for its own unique token in the returned array, so
the decision and the mutation cannot be separated.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pymongo.collection import ReturnDocument

from backend.api.rate_limit import RateLimitExceeded

# Collections are created lazily on first write. Both carry a TTL index on
# `expires_at` so a key that stops being used is reclaimed by the server rather
# than accumulating forever — the same unbounded-growth problem the in-process
# limiters had to solve with a periodic sweep.
RATE_LIMIT_COLLECTION = "rate_limit_windows"
FAILURE_TRACKER_COLLECTION = "login_failure_windows"


def _ensure_ttl_index(collection: Any) -> None:
    collection.create_index("expires_at", expireAfterSeconds=0)


class MongoFixedWindowRateLimiter:
    """Sliding-window limiter sharing state through MongoDB.

    Matches ``FixedWindowRateLimiter``'s interface. Sliding rather than a
    wall-clock bucket for the same reason as the in-process version: a bucket
    that resets on a boundary lets a caller spend its whole budget twice by
    straddling it.
    """

    def __init__(
        self,
        *,
        max_events: int,
        window_seconds: float,
        collection: Any,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1.")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive.")
        self._max_events = int(max_events)
        self._window_seconds = float(window_seconds)
        self._collection = collection
        self._clock = clock or time.time
        _ensure_ttl_index(self._collection)

    def check(self, key: str) -> None:
        """Record an attempt for ``key``; raise if it exceeds the budget.

        A rejected attempt is deliberately not recorded — the pipeline appends
        only when the pruned window is still under the limit — so a caller that
        keeps hammering while blocked does not extend its own lockout.
        """

        now = self._clock()
        cutoff = now - self._window_seconds
        token = uuid.uuid4().hex
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._window_seconds
        )

        pipeline = [
            {
                "$set": {
                    "events": {
                        "$let": {
                            "vars": {
                                "kept": {
                                    "$filter": {
                                        "input": {"$ifNull": ["$events", []]},
                                        "as": "event",
                                        "cond": {"$gt": ["$$event.t", cutoff]},
                                    }
                                }
                            },
                            "in": {
                                "$cond": [
                                    {
                                        "$lt": [
                                            {"$size": "$$kept"},
                                            self._max_events,
                                        ]
                                    },
                                    {
                                        "$concatArrays": [
                                            "$$kept",
                                            [{"t": now, "id": token}],
                                        ]
                                    },
                                    "$$kept",
                                ]
                            },
                        }
                    },
                    "expires_at": expires_at,
                }
            }
        ]

        document = self._collection.find_one_and_update(
            {"_id": key},
            pipeline,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        events = list((document or {}).get("events") or [])
        # Our token is present only if the server's conditional append fired,
        # which is the same expression that enforced the limit. There is no
        # separate read to race against.
        if any(event.get("id") == token for event in events):
            return

        timestamps = [
            float(event.get("t", now))
            for event in events
            if event.get("t") is not None
        ]
        oldest = min(timestamps) if timestamps else now
        raise RateLimitExceeded(self._window_seconds - (now - oldest))

    def reset(self, key: str) -> None:
        self._collection.delete_one({"_id": key})

    def clear(self) -> None:
        self._collection.delete_many({})

    def tracked_key_count(self) -> int:
        return int(self._collection.count_documents({}))


class MongoFailureTracker:
    """Per-account sign-in backoff sharing state through MongoDB.

    Matches ``FailureTracker``'s interface. As with the in-process version, the
    key should be derived from the *submitted* identifier rather than a
    looked-up account, so attempts against addresses with no account are
    throttled identically — otherwise the lockout becomes a user-enumeration
    oracle.
    """

    def __init__(
        self,
        *,
        max_failures: int,
        lockout_seconds: float,
        collection: Any,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be at least 1.")
        if lockout_seconds <= 0:
            raise ValueError("lockout_seconds must be positive.")
        self._max_failures = int(max_failures)
        self._lockout_seconds = float(lockout_seconds)
        self._collection = collection
        self._clock = clock or time.time
        _ensure_ttl_index(self._collection)

    def check(self, key: str) -> None:
        now = self._clock()
        document = self._collection.find_one({"_id": key})
        if not document:
            return

        count = int(document.get("count") or 0)
        last_failure_at = float(document.get("last_failure_at") or 0.0)
        elapsed = now - last_failure_at

        if elapsed >= self._lockout_seconds:
            # The window has passed; the streak is forgotten. The document is
            # left for the TTL index to reclaim rather than deleted here, so a
            # read stays a read.
            return
        if count >= self._max_failures:
            raise RateLimitExceeded(self._lockout_seconds - elapsed)

    def record_failure(self, key: str) -> None:
        now = self._clock()
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._lockout_seconds
        )

        # Reset the streak to 1 when the previous failure is older than the
        # lockout window, otherwise increment. Done in the pipeline so two
        # concurrent failures cannot both read the same count and both write
        # count+1, which would undercount the streak.
        #
        # For a brand-new key `$ifNull` makes the elapsed term 0, and 0 is never
        # >= a positive lockout, so it takes the increment branch and lands on 1.
        pipeline = [
            {
                "$set": {
                    "count": {
                        "$cond": [
                            {
                                "$gte": [
                                    {
                                        "$subtract": [
                                            now,
                                            {"$ifNull": ["$last_failure_at", now]},
                                        ]
                                    },
                                    self._lockout_seconds,
                                ]
                            },
                            1,
                            {"$add": [{"$ifNull": ["$count", 0]}, 1]},
                        ]
                    },
                    "last_failure_at": now,
                    "expires_at": expires_at,
                }
            }
        ]
        self._collection.find_one_and_update({"_id": key}, pipeline, upsert=True)

    def reset(self, key: str) -> None:
        self._collection.delete_one({"_id": key})

    def clear(self) -> None:
        self._collection.delete_many({})

    def tracked_key_count(self) -> int:
        return int(self._collection.count_documents({}))


__all__ = [
    "FAILURE_TRACKER_COLLECTION",
    "RATE_LIMIT_COLLECTION",
    "MongoFailureTracker",
    "MongoFixedWindowRateLimiter",
]
