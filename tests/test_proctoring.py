"""Full-screen proctoring: three warnings end the whole interview."""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.api import routes_proctoring, routes_workflow
from backend.api.routes_proctoring import (
	ProctoringWarningRequest,
	ensure_interview_open,
	post_warning,
	proctoring_state,
)
from backend.database.mongo_client import MongoRepository

_USER = routes_proctoring.AuthenticatedUser(
	user_id="user-1",
	email="c@example.com",
	raw_user={"id": "user-1", "email": "c@example.com"},
)
_ENDED = {"id": "s1", "user_id": "user-1", "proctoring": {"warnings": 3, "terminated": True}}


class StateTests(TestCase):
	def test_fresh_session_has_all_warnings_left(self) -> None:
		state = proctoring_state({"id": "s1"})
		self.assertEqual((state["warnings"], state["remaining"], state["terminated"]), (0, 3, False))

	def test_ended_interview_blocks_round_actions(self) -> None:
		ensure_interview_open({"proctoring": {"warnings": 2}})
		with self.assertRaises(HTTPException) as ctx:
			ensure_interview_open(_ENDED)
		self.assertEqual(ctx.exception.status_code, 409)


class WarningRouteTests(TestCase):
	def _post(self, session: dict, updated: dict | None = None):
		request = ProctoringWarningRequest(session_id="s1", round="technical", reason="exit_fullscreen")
		with patch("backend.api.routes_proctoring.get_session", return_value=session), \
			 patch("backend.api.routes_proctoring.record_proctoring_warning", return_value=updated) as record:
			return post_warning(request, _USER), record

	def test_a_warning_is_recorded_against_the_limit(self) -> None:
		result, record = self._post(
			{"id": "s1", "user_id": "user-1"},
			{"id": "s1", "user_id": "user-1", "proctoring": {"warnings": 1}},
		)
		record.assert_called_once_with("s1", "technical", "exit_fullscreen", 3)
		self.assertEqual((result["warnings"], result["remaining"]), (1, 2))

	def test_no_more_warnings_count_once_the_interview_ended(self) -> None:
		result, record = self._post(_ENDED)
		record.assert_not_called()
		self.assertTrue(result["terminated"])

	def test_someone_elses_session_is_refused(self) -> None:
		with self.assertRaises(HTTPException) as ctx:
			self._post({"id": "s1", "user_id": "someone-else"})
		self.assertEqual(ctx.exception.status_code, 403)


class RepositoryTests(TestCase):
	def _repo(self, docs: list[dict]) -> tuple[MongoRepository, MagicMock]:
		repo = MongoRepository.__new__(MongoRepository)
		sessions = MagicMock()
		sessions.find_one_and_update.side_effect = docs
		repo.db = {"sessions": sessions}
		return repo, sessions

	def test_third_warning_ends_the_interview(self) -> None:
		repo, sessions = self._repo([
			{"id": "s1", "proctoring": {"warnings": 3}},
			{"id": "s1", "proctoring": {"warnings": 3, "terminated": True}},
		])
		doc = repo.record_proctoring_warning(session_id="s1", round_name="hr", reason="tab_hidden", limit=3)
		self.assertTrue(doc["proctoring"]["terminated"])
		self.assertEqual(sessions.find_one_and_update.call_count, 2)
		# Sessions are stored by Mongo _id; filtering on "id" matched nothing.
		for call in sessions.find_one_and_update.call_args_list:
			self.assertEqual(call.args[0], {"_id": "s1"})
		self.assertEqual(sessions.find_one_and_update.call_args.args[1]["$set"]["proctoring.terminated_round"], "hr")

	def test_earlier_warnings_only_count(self) -> None:
		repo, sessions = self._repo([{"id": "s1", "proctoring": {"warnings": 2}}])
		doc = repo.record_proctoring_warning(session_id="s1", round_name="hr", reason="tab_hidden", limit=3)
		self.assertNotIn("terminated", doc["proctoring"])
		self.assertEqual(sessions.find_one_and_update.call_count, 1)


class ResetTests(TestCase):
	def test_rounds_of_an_ended_interview_cannot_be_reset(self) -> None:
		request = routes_workflow.WorkflowResetRequest(session_id="s1", target="technical")
		with patch("backend.api.routes_workflow._require_parent_session", return_value=_ENDED):
			with self.assertRaises(HTTPException) as ctx:
				routes_workflow.reset_workflow_stage(request, _USER)
		self.assertEqual(ctx.exception.status_code, 409)
