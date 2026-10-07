"""The workflow reset endpoint is the only destructive route in the API.

``POST /workflow/reset`` deletes assessment state, interview rounds, recorded
responses, DSA sessions, and the final report. Every delete is scoped by
``session_id`` alone — there is no user filter on the delete calls themselves.
That is a sound design, but it rests entirely on ``ensure_session_access``
having rejected the caller first.

``ensure_session_access`` is unit-tested in ``test_auth.py``. What was missing
is proof that this route actually calls it, and that a rejected call leaves the
owner's data untouched. A unit test on the guard does not catch a route that
forgets to invoke it, and for a destructive endpoint that gap is expensive.

These run against a real MongoDB and skip when none is reachable, matching
``test_database_integration.py``.
"""

from __future__ import annotations

import os
import unittest
import uuid

from fastapi import HTTPException

from backend.api.auth import AuthenticatedUser
from backend.api import routes_workflow
from backend.config import MongoSettings
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


def _user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id, email=f"{user_id}@example.com", raw_user={"id": user_id}
    )


@unittest.skipUnless(
    _MONGO_UP, f"no MongoDB reachable at {_TEST_URI}; skipping integration tests"
)
class WorkflowResetOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_name = f"interview_sim_wf_{uuid.uuid4().hex[:12]}"
        cls.repo = MongoRepository(
            MongoSettings(uri=_TEST_URI, database_name=cls.db_name)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.repo.client.drop_database(cls.db_name)
        cls.repo.client.close()

    def setUp(self) -> None:
        # Point the module-level repository accessor at this test database.
        import backend.database.mongo_client as mongo_client

        self._saved_repo = mongo_client._repository
        mongo_client._repository = self.repo

    def tearDown(self) -> None:
        import backend.database.mongo_client as mongo_client

        mongo_client._repository = self._saved_repo

    def _seed_session_with_data(self, owner_id: str) -> str:
        """Create a session owned by ``owner_id`` with data in every collection
        the reset endpoint deletes from."""

        session_id = self.repo.create_session(user_id=owner_id)["id"]
        self.repo.create_assessment_session(
            session_id=session_id,
            role_key="backend",
            total_questions=5,
            batch_json={"questions": []},
            state_json={"answers": []},
        )
        self.repo.create_interview_round_session(
            session_id=session_id, round="technical", role_key="backend"
        )
        self.repo.save_interview_response(
            {"session_id": session_id, "round": "technical", "score": 0.8}
        )
        self.repo.create_dsa_session(
            session_id=session_id, problem_id="p1", question_number=1
        )
        self.repo.save_final_report(
            {"session_id": session_id, "overall_score": 0.75}
        )
        return session_id

    def _data_counts(self, session_id: str) -> dict[str, int]:
        return {
            "assessment": self.repo.db.assessment_sessions.count_documents(
                {"session_id": session_id}
            ),
            "rounds": self.repo.db.interview_round_sessions.count_documents(
                {"session_id": session_id}
            ),
            "responses": self.repo.db.interview_responses.count_documents(
                {"session_id": session_id}
            ),
            "dsa": self.repo.db.dsa_sessions.count_documents(
                {"session_id": session_id}
            ),
            "reports": self.repo.db.final_reports.count_documents(
                {"session_id": session_id}
            ),
        }

    def _reset(self, session_id: str, caller: AuthenticatedUser, target: str):
        return routes_workflow.reset_workflow_stage(
            routes_workflow.WorkflowResetRequest(
                session_id=session_id, target=target
            ),
            current_user=caller,
        )

    def test_a_different_user_cannot_reset_someone_elses_session(self) -> None:
        owner_session = self._seed_session_with_data("owner-user")
        before = self._data_counts(owner_session)
        self.assertTrue(all(count > 0 for count in before.values()), before)

        with self.assertRaises(HTTPException) as context:
            self._reset(owner_session, _user("attacker-user"), "entire_interview")

        self.assertEqual(context.exception.status_code, 403)
        # The point of the test: nothing was deleted.
        self.assertEqual(self._data_counts(owner_session), before)

    def test_the_owner_can_reset_their_own_session(self) -> None:
        """The guard must not be so strict that it blocks the legitimate case."""

        session_id = self._seed_session_with_data("owner-2")
        self.assertTrue(all(c > 0 for c in self._data_counts(session_id).values()))

        result = self._reset(session_id, _user("owner-2"), "entire_interview")

        self.assertEqual(result["session_id"], session_id)
        self.assertEqual(self._data_counts(session_id),
                         {"assessment": 0, "rounds": 0, "responses": 0,
                          "dsa": 0, "reports": 0})

    def test_an_ownerless_session_is_refused_rather_than_reset(self) -> None:
        """Fails closed. A session with no owner must not be resettable by
        whoever asks first."""

        session_id = self.repo.insert_one(
            "sessions", {"user_id": "", "status": "created"}
        )["id"]
        # insert_one bypasses create_session's own guard, which is the point:
        # this is what a legacy or partially written row looks like.
        self.repo.db.sessions.update_one(
            {"_id": session_id}, {"$set": {"user_id": ""}}
        )

        with self.assertRaises(HTTPException) as context:
            self._reset(session_id, _user("anyone"), "entire_interview")

        self.assertEqual(context.exception.status_code, 409)

    def test_a_missing_session_is_a_404_not_a_silent_success(self) -> None:
        with self.assertRaises(HTTPException) as context:
            self._reset("no-such-session", _user("someone"), "entire_interview")

        self.assertEqual(context.exception.status_code, 404)

    def test_a_partial_reset_only_touches_the_cascaded_rounds(self) -> None:
        """Resetting 'hr' must not delete the technical round's work."""

        session_id = self._seed_session_with_data("owner-3")

        self._reset(session_id, _user("owner-3"), "hr")

        counts = self._data_counts(session_id)
        # technical round and its response survive; the report is cascaded away.
        self.assertEqual(counts["rounds"], 1)
        self.assertEqual(counts["responses"], 1)
        self.assertEqual(counts["reports"], 0)

    def test_one_users_reset_does_not_touch_another_users_session(self) -> None:
        """Deletes are scoped by session_id only, so prove that scoping is
        real rather than assumed."""

        mine = self._seed_session_with_data("user-a")
        theirs = self._seed_session_with_data("user-b")

        self._reset(mine, _user("user-a"), "entire_interview")

        self.assertEqual(sum(self._data_counts(mine).values()), 0)
        self.assertTrue(all(c > 0 for c in self._data_counts(theirs).values()))


if __name__ == "__main__":
    unittest.main()
