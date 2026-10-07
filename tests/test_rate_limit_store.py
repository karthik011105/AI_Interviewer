"""MongoDB-backed throttles, exercised against a real database.

These replace the in-process limiters in a deployment so that limits survive a
restart and hold across workers. The in-process versions document the problem
they solve: with N workers each keeps its own counters, so every configured
limit is silently multiplied by N, and the spend quotas exist to bound a bill.

The property that matters most here is **atomicity**. Counting and then deciding
would be a time-of-check/time-of-use race in which two concurrent requests both
observe the count below the limit and both get admitted. Each check is instead a
single ``find_one_and_update`` whose pipeline prunes and conditionally appends
inside the server, so the decision cannot be separated from the mutation. The
concurrency tests below are the reason that design was chosen, not decoration.

Skips when no MongoDB is reachable, matching ``test_database_integration.py``.
"""

from __future__ import annotations

import os
import threading
import unittest
import uuid

from backend.api.rate_limit import RateLimitExceeded
from backend.api.rate_limit_store import (
    MongoFailureTracker,
    MongoFixedWindowRateLimiter,
)

_TEST_URI = os.environ.get("MONGO_TEST_URI") or "mongodb://localhost:27017"


def _mongo_available() -> bool:
    client = None
    try:
        import pymongo

        client = pymongo.MongoClient(_TEST_URI, serverSelectionTimeoutMS=1500)
        client.server_info()
        return True
    except Exception:
        return False
    finally:
        if client is not None:
            client.close()


_MONGO_UP = _mongo_available()


@unittest.skipUnless(
    _MONGO_UP, f"no MongoDB reachable at {_TEST_URI}; skipping integration tests"
)
class _StoreTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import pymongo

        cls.db_name = f"interview_sim_rl_{uuid.uuid4().hex[:12]}"
        cls.client = pymongo.MongoClient(_TEST_URI)
        cls.db = cls.client[cls.db_name]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.drop_database(cls.db_name)
        cls.client.close()


class MongoRateLimiterTests(_StoreTestBase):
    def _limiter(self, *, max_events=3, window=60.0, clock=None):
        name = f"rl_{uuid.uuid4().hex[:8]}"
        return MongoFixedWindowRateLimiter(
            max_events=max_events,
            window_seconds=window,
            collection=self.db[name],
            clock=clock,
        )

    def test_it_allows_up_to_the_budget_then_refuses(self) -> None:
        limiter = self._limiter(max_events=3)

        for _ in range(3):
            limiter.check("caller")

        with self.assertRaises(RateLimitExceeded):
            limiter.check("caller")

    def test_separate_keys_have_separate_budgets(self) -> None:
        limiter = self._limiter(max_events=1)

        limiter.check("caller-a")
        limiter.check("caller-b")

        with self.assertRaises(RateLimitExceeded):
            limiter.check("caller-a")

    def test_the_window_slides_rather_than_resetting_on_a_boundary(self) -> None:
        """A bucket that resets on a wall-clock boundary lets a caller spend its
        whole budget twice by straddling it. This is why the store keeps
        timestamps instead of a counter."""

        now = [1_000.0]
        limiter = self._limiter(max_events=2, window=10.0, clock=lambda: now[0])

        limiter.check("caller")
        limiter.check("caller")
        with self.assertRaises(RateLimitExceeded):
            limiter.check("caller")

        # Half a window later the earlier events are still inside it.
        now[0] += 5.0
        with self.assertRaises(RateLimitExceeded):
            limiter.check("caller")

        # Past the window, the budget is available again.
        now[0] += 6.0
        limiter.check("caller")

    def test_a_refused_attempt_is_not_recorded(self) -> None:
        """Otherwise a caller hammering while blocked extends its own lockout
        indefinitely, which turns a rate limit into a permanent ban."""

        now = [1_000.0]
        limiter = self._limiter(max_events=1, window=10.0, clock=lambda: now[0])

        limiter.check("caller")
        for _ in range(5):
            with self.assertRaises(RateLimitExceeded):
                limiter.check("caller")

        # Only the single admitted event should be stored, so the window clears
        # 10s after that one — not after the last rejected attempt.
        now[0] += 11.0
        limiter.check("caller")

    def test_concurrent_callers_cannot_exceed_the_budget(self) -> None:
        """The reason the check is one atomic pipeline update rather than a read
        followed by a write."""

        limiter = self._limiter(max_events=5, window=60.0)
        admitted: list[int] = []
        refused: list[int] = []
        barrier = threading.Barrier(13)

        def worker(index: int) -> None:
            barrier.wait()
            try:
                limiter.check("shared-key")
                admitted.append(index)
            except RateLimitExceeded:
                refused.append(index)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(len(admitted), 5, f"admitted={admitted} refused={refused}")
        self.assertEqual(len(refused), 7)

    def test_state_survives_a_new_limiter_instance(self) -> None:
        """The whole point: a restart, or a second worker, must not hand the
        caller a fresh budget."""

        name = f"rl_shared_{uuid.uuid4().hex[:8]}"
        first = MongoFixedWindowRateLimiter(
            max_events=2, window_seconds=60.0, collection=self.db[name]
        )
        first.check("caller")
        first.check("caller")

        # A different instance over the same collection is what a second worker
        # or a restarted process looks like.
        second = MongoFixedWindowRateLimiter(
            max_events=2, window_seconds=60.0, collection=self.db[name]
        )
        with self.assertRaises(RateLimitExceeded):
            second.check("caller")

    def test_reset_and_clear_forget_state(self) -> None:
        limiter = self._limiter(max_events=1)
        limiter.check("caller")

        limiter.reset("caller")
        limiter.check("caller")  # allowed again

        limiter.clear()
        self.assertEqual(limiter.tracked_key_count(), 0)

    def test_retry_after_is_a_positive_integer(self) -> None:
        """It is sent straight back as a Retry-After header, which must be a
        positive integer."""

        limiter = self._limiter(max_events=1, window=30.0)
        limiter.check("caller")

        with self.assertRaises(RateLimitExceeded) as context:
            limiter.check("caller")

        self.assertGreaterEqual(context.exception.retry_after_seconds, 1)
        self.assertIsInstance(context.exception.retry_after_seconds, int)

    def test_invalid_configuration_is_refused(self) -> None:
        for kwargs in ({"max_events": 0}, {"window": 0.0}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self._limiter(**kwargs)


class MongoFailureTrackerTests(_StoreTestBase):
    def _tracker(self, *, max_failures=3, lockout=300.0, clock=None):
        name = f"ft_{uuid.uuid4().hex[:8]}"
        return MongoFailureTracker(
            max_failures=max_failures,
            lockout_seconds=lockout,
            collection=self.db[name],
            clock=clock,
        )

    def test_an_unknown_key_is_not_locked_out(self) -> None:
        self._tracker().check("nobody@example.com")  # must not raise

    def test_it_locks_out_after_the_configured_failures(self) -> None:
        tracker = self._tracker(max_failures=3)

        for _ in range(3):
            tracker.record_failure("target@example.com")

        with self.assertRaises(RateLimitExceeded):
            tracker.check("target@example.com")

    def test_it_does_not_lock_out_before_the_threshold(self) -> None:
        tracker = self._tracker(max_failures=3)

        tracker.record_failure("target@example.com")
        tracker.record_failure("target@example.com")

        tracker.check("target@example.com")  # must not raise

    def test_the_streak_expires_after_the_lockout_window(self) -> None:
        now = [1_000.0]
        tracker = self._tracker(max_failures=2, lockout=300.0, clock=lambda: now[0])

        tracker.record_failure("target@example.com")
        tracker.record_failure("target@example.com")
        with self.assertRaises(RateLimitExceeded):
            tracker.check("target@example.com")

        now[0] += 301.0
        tracker.check("target@example.com")  # forgotten

    def test_a_failure_after_the_window_starts_a_fresh_streak(self) -> None:
        """Not a continuation. Otherwise a slow attacker accumulates a lockout
        across hours without ever being throttled."""

        now = [1_000.0]
        tracker = self._tracker(max_failures=2, lockout=100.0, clock=lambda: now[0])

        tracker.record_failure("target@example.com")
        now[0] += 200.0
        tracker.record_failure("target@example.com")

        # The old failure is outside the window, so this is streak length 1.
        tracker.check("target@example.com")  # must not raise

    def test_reset_clears_the_streak(self) -> None:
        tracker = self._tracker(max_failures=1)
        tracker.record_failure("target@example.com")
        with self.assertRaises(RateLimitExceeded):
            tracker.check("target@example.com")

        tracker.reset("target@example.com")

        tracker.check("target@example.com")

    def test_concurrent_failures_are_all_counted(self) -> None:
        """Two concurrent failures must not both read the same count and both
        write count+1, which would undercount the streak and let an attacker
        get extra attempts by parallelising."""

        tracker = self._tracker(max_failures=8, lockout=300.0)
        barrier = threading.Barrier(9)

        def worker() -> None:
            barrier.wait()
            tracker.record_failure("target@example.com")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        with self.assertRaises(RateLimitExceeded):
            tracker.check("target@example.com")

    def test_state_survives_a_new_tracker_instance(self) -> None:
        name = f"ft_shared_{uuid.uuid4().hex[:8]}"
        first = MongoFailureTracker(
            max_failures=2, lockout_seconds=300.0, collection=self.db[name]
        )
        first.record_failure("target@example.com")
        first.record_failure("target@example.com")

        second = MongoFailureTracker(
            max_failures=2, lockout_seconds=300.0, collection=self.db[name]
        )
        with self.assertRaises(RateLimitExceeded):
            second.check("target@example.com")

    def test_invalid_configuration_is_refused(self) -> None:
        for kwargs in ({"max_failures": 0}, {"lockout": 0.0}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self._tracker(**kwargs)


class TtlIndexTests(_StoreTestBase):
    """Both collections must be reclaimable by the server. The in-process
    limiters needed a periodic sweep to avoid growing with every key ever seen;
    here that job belongs to a TTL index."""

    def test_both_stores_create_a_ttl_index(self) -> None:
        limiter_name = f"rl_ttl_{uuid.uuid4().hex[:8]}"
        tracker_name = f"ft_ttl_{uuid.uuid4().hex[:8]}"
        MongoFixedWindowRateLimiter(
            max_events=1, window_seconds=60.0, collection=self.db[limiter_name]
        )
        MongoFailureTracker(
            max_failures=1, lockout_seconds=60.0, collection=self.db[tracker_name]
        )

        for name in (limiter_name, tracker_name):
            with self.subTest(collection=name):
                indexes = {i["name"]: i for i in self.db[name].list_indexes()}
                self.assertIn("expires_at_1", indexes)
                self.assertEqual(indexes["expires_at_1"].get("expireAfterSeconds"), 0)


if __name__ == "__main__":
    unittest.main()


class BackendSelectionTests(unittest.TestCase):
    """Choosing the store is configuration, and the default has to be safe.

    Memory is the default specifically so the test suite and a local run need
    no database — the suite deliberately points MONGO_URI at a dead port, and a
    throttle that required Mongo would turn every auth test into a connection
    error. Production opts in explicitly.
    """

    def test_the_default_is_memory(self) -> None:
        from backend.api.rate_limit import rate_limit_backend

        self.assertEqual(rate_limit_backend({}), "memory")

    def test_mongo_is_selected_only_by_exact_opt_in(self) -> None:
        from backend.api.rate_limit import rate_limit_backend

        self.assertEqual(rate_limit_backend({"RATE_LIMIT_BACKEND": "mongo"}), "mongo")
        self.assertEqual(rate_limit_backend({"RATE_LIMIT_BACKEND": "MONGO"}), "mongo")
        # Anything unrecognised must fall back to the safe store rather than
        # failing closed and taking auth down.
        for value in ("redis", "", "  ", "memory", "nonsense"):
            with self.subTest(value=value):
                self.assertEqual(
                    rate_limit_backend({"RATE_LIMIT_BACKEND": value}), "memory"
                )

    def test_the_factories_build_the_in_process_store_by_default(self) -> None:
        import os as _os
        from unittest.mock import patch

        from backend.api.rate_limit import (
            FailureTracker,
            FixedWindowRateLimiter,
            create_failure_tracker,
            create_rate_limiter,
        )

        with patch.dict(_os.environ, {}, clear=False):
            _os.environ.pop("RATE_LIMIT_BACKEND", None)
            limiter = create_rate_limiter(max_events=5, window_seconds=60.0)
            tracker = create_failure_tracker(max_failures=3, lockout_seconds=60.0)

        self.assertIsInstance(limiter, FixedWindowRateLimiter)
        self.assertIsInstance(tracker, FailureTracker)


class ProcessLocalWarningTests(unittest.TestCase):
    """The dangerous combination is memory plus more than one worker: every
    configured limit is then multiplied by the worker count, with nothing to
    indicate it. That caveat lived only in a docstring, which is the wrong
    place for it to be noticed."""

    def _warn(self, env: dict) -> bool:
        from backend.api.rate_limit import warn_if_throttles_are_process_local

        return warn_if_throttles_are_process_local(env)

    def test_it_warns_for_memory_with_multiple_workers(self) -> None:
        self.assertTrue(
            self._warn({"RATE_LIMIT_BACKEND": "memory", "UVICORN_WORKERS": "4"})
        )

    def test_it_stays_quiet_for_a_single_worker(self) -> None:
        self.assertFalse(
            self._warn({"RATE_LIMIT_BACKEND": "memory", "UVICORN_WORKERS": "1"})
        )

    def test_it_stays_quiet_when_the_store_is_shared(self) -> None:
        """Many workers are fine once the counters are shared, which is the
        whole point of the change."""

        self.assertFalse(
            self._warn({"RATE_LIMIT_BACKEND": "mongo", "UVICORN_WORKERS": "8"})
        )

    def test_an_unparseable_worker_count_is_treated_as_one(self) -> None:
        """A malformed value must not produce a spurious warning."""

        self.assertFalse(
            self._warn({"RATE_LIMIT_BACKEND": "memory", "UVICORN_WORKERS": "abc"})
        )

    def test_the_warning_names_the_variable_to_set(self) -> None:
        import logging

        from backend.api import rate_limit

        with self.assertLogs(
            logging.getLogger(rate_limit.__name__), level="WARNING"
        ) as captured:
            self._warn({"RATE_LIMIT_BACKEND": "memory", "UVICORN_WORKERS": "4"})

        joined = "\n".join(captured.output)
        self.assertIn("RATE_LIMIT_BACKEND", joined)
        self.assertIn("mongo", joined)
