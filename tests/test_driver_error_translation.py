"""pymongo errors must not escape the data layer.

Every route module catches ``DatabaseClientError`` — 46 handlers across seven of
them — and turns it into a 503. But ``MongoRepository`` calls pymongo directly in
roughly forty places, and ``routes_auth`` reaches ``repo.db.users`` directly, so
a raw ``PyMongoError`` bypassed all of them. Phase 1.8 measured the result:
``AutoReconnect`` (an Atlas failover) and ``ExecutionTimeout`` (a slow
shared-tier query) each produced a **500 instead of a 503**, which changes
whether a client retries. Both are expected events on Atlas M0.

``MongoRepository.db`` is now a proxy that translates at the boundary, so these
tests check the translation and — just as importantly — that ordinary queries
still behave exactly as before.
"""

from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

from pymongo.errors import (
    AutoReconnect,
    DuplicateKeyError,
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
    WriteError,
)

from backend.config import MongoSettings
from backend.database.db_errors import (
    DatabaseClientError,
    DatabaseDependencyError,
    DuplicateRecordError,
)
from backend.database.driver_errors import translate_driver_error
from backend.database.mongo_client import MongoRepository

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


class TranslationMappingTests(unittest.TestCase):
    """Pure mapping, no database needed."""

    def test_transient_failures_become_dependency_errors(self) -> None:
        for error in (
            AutoReconnect("failover"),
            ServerSelectionTimeoutError("no server"),
            NetworkTimeout("timed out"),
            ExecutionTimeout("slow query"),
        ):
            with self.subTest(error=type(error).__name__):
                translated = translate_driver_error(error)
                self.assertIsInstance(translated, DatabaseDependencyError)

    def test_a_unique_violation_becomes_a_duplicate_record_error(self) -> None:
        self.assertIsInstance(
            translate_driver_error(DuplicateKeyError("E11000")), DuplicateRecordError
        )

    def test_other_driver_failures_become_the_base_domain_error(self) -> None:
        for error in (OperationFailure("quota exceeded"), WriteError("rejected")):
            with self.subTest(error=type(error).__name__):
                translated = translate_driver_error(error)
                self.assertIsInstance(translated, DatabaseClientError)

    def test_everything_is_catchable_as_a_database_client_error(self) -> None:
        """The property the whole change rests on: one `except
        DatabaseClientError` in a route must catch all of these."""

        for error in (
            AutoReconnect("x"),
            ExecutionTimeout("x"),
            DuplicateKeyError("x"),
            OperationFailure("x"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertIsInstance(
                    translate_driver_error(error), DatabaseClientError
                )


@unittest.skipUnless(_MONGO_UP, f"no MongoDB reachable at {_TEST_URI}")
class ProxyBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_name = f"interview_sim_dx_{uuid.uuid4().hex[:12]}"
        cls.repo = MongoRepository(
            MongoSettings(uri=_TEST_URI, database_name=cls.db_name)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.repo.client.drop_database(cls.db_name)
        cls.repo.client.close()

    def _raw_collection_type(self):
        return type(self.repo.client[self.db_name]["sessions"])

    def test_a_failing_read_is_translated(self) -> None:
        session_id = self.repo.create_session(user_id="u")["id"]

        with patch.object(
            self._raw_collection_type(), "find_one", side_effect=AutoReconnect("failover")
        ):
            with self.assertRaises(DatabaseClientError):
                self.repo.get_session(session_id)

    def test_a_failing_write_is_translated(self) -> None:
        with patch.object(
            self._raw_collection_type(),
            "insert_one",
            side_effect=OperationFailure("quota exceeded"),
        ):
            with self.assertRaises(DatabaseClientError):
                self.repo.create_session(user_id="u")

    def test_a_failure_during_cursor_iteration_is_translated(self) -> None:
        """find() returns immediately and the server work happens while
        iterating, so a failover mid-iteration would otherwise bypass the
        translation completely — which is the exact case this exists for."""

        self.repo.create_session(user_id="cursor-user")

        real_find = self._raw_collection_type().find

        def exploding_find(self_collection, *args, **kwargs):
            cursor = real_find(self_collection, *args, **kwargs)

            class Exploding:
                def __iter__(self):
                    raise AutoReconnect("failover mid-iteration")

                def next(self):
                    raise AutoReconnect("failover mid-iteration")

                def sort(self, *_args, **_kwargs):
                    # Return self, not the real cursor: sort() is chained before
                    # iteration, and delegating it would hand the caller a
                    # healthy cursor and quietly defeat this test.
                    return self

                def __getattr__(self, name):
                    return getattr(cursor, name)

            return Exploding()

        with patch.object(self._raw_collection_type(), "find", exploding_find):
            with self.assertRaises(DatabaseClientError):
                self.repo.list_sessions_for_user(user_id="cursor-user")

    def test_ordinary_queries_are_unaffected(self) -> None:
        """The proxy must be invisible in the normal case."""

        session_id = self.repo.create_session(user_id="plain-user")["id"]

        fetched = self.repo.get_session(session_id)
        listed = self.repo.list_sessions_for_user(user_id="plain-user")

        self.assertEqual(fetched["id"], session_id)
        self.assertNotIn("_id", fetched)
        self.assertEqual([s["id"] for s in listed], [session_id])

    def test_a_sorted_cursor_still_sorts(self) -> None:
        """sort() returns the cursor, so the proxy has to re-wrap it without
        breaking the chain."""

        user_id = f"sorted-{uuid.uuid4().hex[:6]}"
        for created_at in ("2026-01-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00"):
            self.repo.create_session(user_id=user_id, created_at=created_at)

        sessions = self.repo.list_sessions_for_user(user_id=user_id)

        self.assertEqual(
            [s["created_at"] for s in sessions],
            ["2026-03-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"],
        )

    def test_a_duplicate_insert_still_reports_a_duplicate(self) -> None:
        session_id = self.repo.create_session(user_id="dup-user")["id"]

        with self.assertRaises(DuplicateRecordError):
            self.repo.insert_one("sessions", {"id": session_id, "user_id": "dup-user"})

    def test_direct_collection_access_is_also_translated(self) -> None:
        """routes_auth uses repo.db.users directly, bypassing every repository
        method, so the proxy has to cover attribute access as well."""

        with patch.object(
            self._raw_collection_type(), "find_one", side_effect=AutoReconnect("failover")
        ):
            with self.assertRaises(DatabaseClientError):
                self.repo.db.users.find_one({"email": "x@example.com"})

    def test_index_creation_is_translated(self) -> None:
        with patch.object(
            self._raw_collection_type(),
            "create_index",
            side_effect=OperationFailure("not authorized"),
        ):
            with self.assertRaises(DatabaseClientError):
                self.repo.db.sessions.create_index("nope")


if __name__ == "__main__":
    unittest.main()
