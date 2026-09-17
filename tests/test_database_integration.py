"""Integration tests that run MongoRepository against a REAL MongoDB.

Every other test module mocks the repository layer and passes against an
unreachable database, which means the persistence layer -- indexes, unique
constraints, optimistic concurrency, the atomicity of the token paths -- had
never been executed by a test at all. Four defects were found the first time it
was, and the regressions below are the ones that cover them.

These skip when no MongoDB is reachable, so a contributor without one still
gets a green suite. CI provides one as a service container, so they do run
there. If you see these skipping locally and want them, start MongoDB (or
`docker compose up -d mongo`) and set MONGO_TEST_URI if it is not on the
default port.
"""

from __future__ import annotations

import datetime as dt
import os
import threading
import unittest
import uuid

from backend.config import MongoSettings
from backend.database.db_errors import (
    ConcurrentUpdateError,
    DuplicateRecordError,
    RecordNotFoundError,
)
from backend.database.mongo_client import MongoRepository

_TEST_URI = os.environ.get("MONGO_TEST_URI") or "mongodb://localhost:27017"


def _mongo_available() -> bool:
    """Probe once at import time, closing the client.

    pymongo warns if a MongoClient is garbage collected while still open, and
    that warning is noise on every single test run. The client is short-lived
    and only used to answer "is there a database here at all".
    """

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
class _RepositoryTestBase(unittest.TestCase):
    """Each test class gets its own database, dropped on the way out, so a
    failing test can never leave state that changes another test's result."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.db_name = f"interview_sim_test_{uuid.uuid4().hex[:12]}"
        cls.repo = MongoRepository(
            MongoSettings(uri=_TEST_URI, database_name=cls.db_name)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.repo.client.drop_database(cls.db_name)
        cls.repo.client.close()

    def _new_session(self, user_id: str = "user-1") -> str:
        return self.repo.create_session(user_id=user_id)["id"]


class IndexTests(_RepositoryTestBase):
    """The unique indexes are the only thing preventing duplicate rows, and
    the TTL indexes are the only thing bounding the two token collections."""

    def _index_names(self, collection: str) -> dict[str, dict]:
        return {i["name"]: i for i in self.repo.db[collection].list_indexes()}

    def test_the_declared_unique_indexes_exist(self) -> None:
        expected = {
            "users": "email_1",
            "resume_data": "session_id_1",
            "assessment_sessions": "session_id_1",
            "final_reports": "session_id_1",
            "interview_round_contexts": "session_id_1_round_1",
            "interview_round_sessions": "session_id_1_round_1",
            "dsa_sessions": "session_id_1_question_number_1",
            "revoked_tokens": "jti_1",
        }
        for collection, index_name in expected.items():
            with self.subTest(collection=collection):
                index = self._index_names(collection).get(index_name)
                self.assertIsNotNone(index, f"{collection}.{index_name} missing")
                self.assertTrue(
                    index.get("unique"),
                    f"{collection}.{index_name} exists but is not unique",
                )

    def test_the_token_collections_have_ttl_indexes(self) -> None:
        """Without these, revoked_tokens and password_reset_tokens grow without
        bound, because nothing else ever deletes from them."""

        for collection in ("revoked_tokens", "password_reset_tokens"):
            with self.subTest(collection=collection):
                index = self._index_names(collection).get("expires_at_1")
                self.assertIsNotNone(index)
                # 0 means "expire at the time stored in the field", not
                # "expire immediately".
                self.assertEqual(index.get("expireAfterSeconds"), 0)


class DuplicateInsertTests(_RepositoryTestBase):
    """pymongo's DuplicateKeyError is not a DatabaseClientError, so before it
    was translated it escaped every `except DatabaseClientError` in the route
    layer and became an unhandled 500 instead of a handled response."""

    def test_a_duplicate_dsa_session_raises_a_domain_error(self) -> None:
        session_id = self._new_session()
        self.repo.create_dsa_session(
            session_id=session_id, problem_id="p1", question_number=1
        )
        with self.assertRaises(DuplicateRecordError):
            self.repo.create_dsa_session(
                session_id=session_id, problem_id="p1", question_number=1
            )

    def test_a_duplicate_final_report_raises_a_domain_error(self) -> None:
        session_id = self._new_session()
        self.repo.save_final_report({"session_id": session_id, "overall_score": 1})
        with self.assertRaises(DuplicateRecordError):
            self.repo.save_final_report({"session_id": session_id, "overall_score": 2})

    def test_the_domain_error_is_catchable_as_a_database_client_error(self) -> None:
        """This is the property that actually matters: the route layer catches
        DatabaseClientError, so the translation is only useful if the subclass
        relationship holds."""

        from backend.database.db_errors import DatabaseClientError

        self.assertTrue(issubclass(DuplicateRecordError, DatabaseClientError))


class OptimisticConcurrencyTests(_RepositoryTestBase):
    def test_advance_interview_question_rejects_a_stale_version(self) -> None:
        session_id = self._new_session()
        self.repo.create_interview_round_session(
            session_id=session_id, round="hr", role_key="backend"
        )
        self.repo.advance_interview_question(
            session_id=session_id, round="hr", new_index=1,
            difficulty_signal=0.5, questions_json=None, expected_state_version=1,
        )
        with self.assertRaises(ConcurrentUpdateError):
            self.repo.advance_interview_question(
                session_id=session_id, round="hr", new_index=2,
                difficulty_signal=0.5, questions_json=None, expected_state_version=1,
            )

    def test_persist_dsa_state_rejects_a_stale_version(self) -> None:
        session_id = self._new_session()
        self.repo.create_dsa_session(
            session_id=session_id, problem_id="p1", question_number=1
        )
        record = self.repo.require_dsa_session(
            session_id=session_id, question_number=1
        )
        version = record["state_version"]
        self.repo.persist_dsa_state(
            session_id=session_id, question_number=1,
            state_json=record["state_json"], expected_state_version=version,
        )
        with self.assertRaises(ConcurrentUpdateError):
            self.repo.persist_dsa_state(
                session_id=session_id, question_number=1,
                state_json=record["state_json"], expected_state_version=version,
            )

    def test_an_unguarded_write_cannot_move_the_version_backwards(self) -> None:
        """The regression that matters most in this file.

        `expected_state_version=None` is the unguarded path. It used to derive
        the next version from the caller's own state_json, so a caller holding
        a stale copy could rewind the stored counter -- after which a writer
        holding an old version was accepted and silently overwrote newer
        state, defeating the guard for everyone. The stored version must be
        the only source of truth.
        """

        session_id = self._new_session()
        self.repo.create_dsa_session(
            session_id=session_id, problem_id="p1", question_number=1
        )
        for _ in range(4):
            record = self.repo.require_dsa_session(
                session_id=session_id, question_number=1
            )
            self.repo.persist_dsa_state(
                session_id=session_id, question_number=1,
                state_json=record["state_json"],
                expected_state_version=record["state_version"],
            )

        record = self.repo.require_dsa_session(
            session_id=session_id, question_number=1
        )
        version_before = record["state_version"]
        self.assertGreater(version_before, 1, "precondition: version has advanced")

        stale_state = dict(record["state_json"])
        stale_state["state_version"] = 1
        updated = self.repo.persist_dsa_state(
            session_id=session_id, question_number=1,
            state_json=stale_state, expected_state_version=None,
        )

        self.assertEqual(updated["state_version"], version_before + 1)

        # And the guard still holds afterwards: a writer with the old version
        # must not be accepted.
        with self.assertRaises(ConcurrentUpdateError):
            self.repo.persist_dsa_state(
                session_id=session_id, question_number=1,
                state_json=stale_state, expected_state_version=1,
            )

    def test_an_unguarded_write_to_a_missing_session_raises_not_found(self) -> None:
        with self.assertRaises(RecordNotFoundError):
            self.repo.persist_dsa_state(
                session_id="no-such-session", question_number=1,
                state_json={"stage": "coding"}, expected_state_version=None,
            )

    def test_concurrent_submission_appends_do_not_corrupt_the_record(self) -> None:
        """Optimistic concurrency means the losers are rejected rather than
        merged, because the append is a read-modify-write of a whole array.
        What must not happen is a torn or duplicated array.
        """

        session_id = self._new_session()
        self.repo.create_dsa_session(
            session_id=session_id, problem_id="p1", question_number=1
        )

        rejections: list[str] = []
        accepted: list[int] = []
        barrier = threading.Barrier(5)

        def worker(n: int) -> None:
            barrier.wait()
            try:
                self.repo.append_dsa_submission(
                    session_id=session_id, question_number=1,
                    submission={"code": f"s{n}"}, current_code_draft=f"s{n}",
                )
                accepted.append(n)
            except ConcurrentUpdateError:
                rejections.append("conflict")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        record = self.repo.require_dsa_session(
            session_id=session_id, question_number=1
        )
        submissions = record.get("all_code_submissions") or []

        self.assertEqual(len(accepted) + len(rejections), 4)
        # Exactly the accepted writes are stored: nothing lost from a winner,
        # nothing duplicated, no partial array.
        self.assertEqual(len(submissions), len(accepted))
        codes = [s["code"] for s in submissions]
        self.assertEqual(len(codes), len(set(codes)), "a submission was duplicated")

    def test_a_serial_sequence_of_appends_keeps_every_submission(self) -> None:
        session_id = self._new_session()
        self.repo.create_dsa_session(
            session_id=session_id, problem_id="p1", question_number=1
        )
        for i in range(3):
            self.repo.append_dsa_submission(
                session_id=session_id, question_number=1,
                submission={"code": f"s{i}"}, current_code_draft=f"s{i}",
            )
        record = self.repo.require_dsa_session(
            session_id=session_id, question_number=1
        )
        self.assertEqual(len(record["all_code_submissions"]), 3)


class TokenPathTests(_RepositoryTestBase):
    """These two paths are security-relevant: a reset token that can be
    consumed twice is an account-takeover primitive, and a revocation that is
    not idempotent turns a retried logout into a 500."""

    def test_a_reset_token_can_only_be_consumed_once(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        self.repo.create_password_reset_token(
            token_hash="hash-single-use",
            user_id="user-1",
            expires_at=now + dt.timedelta(minutes=30),
        )
        self.assertIsNotNone(
            self.repo.consume_password_reset_token(
                token_hash="hash-single-use", now=now
            )
        )
        self.assertIsNone(
            self.repo.consume_password_reset_token(
                token_hash="hash-single-use", now=now
            )
        )

    def test_exactly_one_racing_caller_consumes_a_reset_token(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        self.repo.create_password_reset_token(
            token_hash="hash-race",
            user_id="user-1",
            expires_at=now + dt.timedelta(minutes=30),
        )

        winners: list[int] = []
        barrier = threading.Barrier(9)

        def worker() -> None:
            barrier.wait()
            if self.repo.consume_password_reset_token(
                token_hash="hash-race", now=now
            ):
                winners.append(1)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(len(winners), 1, "more than one caller consumed the token")

    def test_an_expired_reset_token_is_not_consumable(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        self.repo.create_password_reset_token(
            token_hash="hash-expired",
            user_id="user-1",
            expires_at=now - dt.timedelta(minutes=1),
        )
        self.assertIsNone(
            self.repo.consume_password_reset_token(token_hash="hash-expired", now=now)
        )

    def test_revoking_the_same_token_twice_is_a_no_op(self) -> None:
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
        self.repo.revoke_token(jti="jti-1", user_id="user-1", expires_at=expires_at)
        self.repo.revoke_token(jti="jti-1", user_id="user-1", expires_at=expires_at)

        self.assertTrue(self.repo.is_token_revoked(jti="jti-1"))
        self.assertEqual(
            self.repo.db.revoked_tokens.count_documents({"jti": "jti-1"}), 1
        )

    def test_an_unrevoked_token_reports_as_not_revoked(self) -> None:
        self.assertFalse(self.repo.is_token_revoked(jti="never-revoked"))


class ReadPathRobustnessTests(_RepositoryTestBase):
    def test_a_partial_context_document_does_not_break_the_read(self) -> None:
        """A comprehension that guarded `round` but indexed `context_json`
        unguarded meant one partially written row raised KeyError and took out
        report generation for the entire session, even though the other rows
        were usable.
        """

        session_id = self._new_session()
        self.repo.save_interview_contexts(
            session_id=session_id, contexts={"hr": {"topic": "intro"}}
        )
        self.repo.db.interview_round_contexts.insert_one(
            {"session_id": session_id, "round": "technical"}  # no context_json
        )

        contexts = self.repo.get_all_interview_contexts(session_id=session_id)

        self.assertEqual(contexts, {"hr": {"topic": "intro"}})

    def test_updating_a_missing_record_raises_not_found(self) -> None:
        with self.assertRaises(RecordNotFoundError):
            self.repo.update_session_status(session_id="no-such-session", status="done")

    def test_concurrent_report_upserts_produce_exactly_one_document(self) -> None:
        """upsert_final_report reads then upserts, which looks like a race, and
        final_reports has a unique index on session_id. It holds up because
        MongoDB serialises upserts on the same filter, so this records the
        behaviour rather than assuming it.
        """

        session_id = self._new_session()
        errors: list[str] = []
        barrier = threading.Barrier(7)

        def worker(n: int) -> None:
            barrier.wait()
            try:
                self.repo.upsert_final_report(session_id=session_id, overall_score=n)
            except Exception as exc:  # noqa: BLE001 - recording, not handling
                errors.append(type(exc).__name__)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(
            self.repo.db.final_reports.count_documents({"session_id": session_id}), 1
        )


class SessionRoundTripTests(_RepositoryTestBase):
    """Basic persistence round trips, which nothing had ever verified against a
    real database because the repository layer is mocked everywhere else."""

    def test_a_session_round_trips(self) -> None:
        session_id = self._new_session(user_id="round-trip-user")
        fetched = self.repo.get_session(session_id)

        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["user_id"], "round-trip-user")
        # _id is mapped to a string id, and _id must not leak through.
        self.assertEqual(fetched["id"], session_id)
        self.assertNotIn("_id", fetched)

    def test_creating_a_session_without_a_user_is_refused(self) -> None:
        from backend.database.db_errors import DatabaseClientError

        with self.assertRaises(DatabaseClientError):
            self.repo.create_session(user_id="   ")

    def test_sessions_for_a_user_come_back_newest_first(self) -> None:
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        self.repo.create_session(user_id=user_id, created_at="2026-01-01T00:00:00+00:00")
        self.repo.create_session(user_id=user_id, created_at="2026-03-01T00:00:00+00:00")
        self.repo.create_session(user_id=user_id, created_at="2026-02-01T00:00:00+00:00")

        sessions = self.repo.list_sessions_for_user(user_id=user_id)

        self.assertEqual(
            [s["created_at"] for s in sessions],
            [
                "2026-03-01T00:00:00+00:00",
                "2026-02-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ],
        )

    def test_resume_data_upserts_rather_than_duplicating(self) -> None:
        session_id = self._new_session()
        self.repo.save_resume_data(
            session_id=session_id, raw_text="first", parsed_json={"v": 1}
        )
        self.repo.save_resume_data(
            session_id=session_id, raw_text="second", parsed_json={"v": 2}
        )

        self.assertEqual(
            self.repo.db.resume_data.count_documents({"session_id": session_id}), 1
        )
        stored = self.repo.fetch_one("resume_data", filters={"session_id": session_id})
        self.assertEqual(stored["raw_text"], "second")


if __name__ == "__main__":
    unittest.main()
