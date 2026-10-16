"""Finish interview: the candidate ends the whole interview early."""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.api import routes_interview_end, routes_workflow
from backend.api.routes_interview_end import (
	FinishInterviewRequest,
	ensure_interview_open,
	finish_interview,
	interview_end_state,
)
from backend.database.mongo_client import MongoRepository

_USER = routes_interview_end.AuthenticatedUser(
	user_id="user-1",
	email="c@example.com",
	raw_user={"id": "user-1", "email": "c@example.com"},
)
_FINISHED = {"id": "s1", "user_id": "user-1", "interview_ended": {"reason": "finished", "round": "technical"}}


class StateTests(TestCase):
	def test_an_open_interview(self) -> None:
		self.assertEqual(interview_end_state({"id": "s1"}), {"finished": False, "finished_round": None})
		ensure_interview_open({"id": "s1"})

	def test_old_warning_counts_no_longer_end_anything(self) -> None:
		# Sessions from before the full-screen rule was dropped.
		ensure_interview_open({"proctoring": {"warnings": 3, "terminated": True}})

	def test_a_finished_interview_blocks_round_actions(self) -> None:
		with self.assertRaises(HTTPException) as ctx:
			ensure_interview_open(_FINISHED)
		self.assertEqual(ctx.exception.status_code, 409)
		self.assertIn("finished this interview early", ctx.exception.detail)


class FinishTests(TestCase):
	def _finish(self, session: dict, updated: dict | None = None):
		request = FinishInterviewRequest(session_id="s1", round="technical")
		with patch("backend.api.routes_interview_end.get_session", return_value=session), \
			 patch("backend.api.routes_interview_end.mark_interview_finished", return_value=updated) as mark:
			return finish_interview(request, _USER), mark

	def test_finishing_records_the_round(self) -> None:
		result, mark = self._finish({"id": "s1", "user_id": "user-1"}, _FINISHED)
		mark.assert_called_once_with("s1", "technical")
		self.assertEqual(result, {"finished": True, "finished_round": "technical"})

	def test_finishing_twice_changes_nothing(self) -> None:
		result, mark = self._finish(_FINISHED)
		mark.assert_not_called()
		self.assertTrue(result["finished"])

	def test_someone_elses_session_is_refused(self) -> None:
		with self.assertRaises(HTTPException) as ctx:
			self._finish({"id": "s1", "user_id": "someone-else"})
		self.assertEqual(ctx.exception.status_code, 403)


class RepositoryTests(TestCase):
	def test_finish_is_stored_by_mongo_id(self) -> None:
		repo = MongoRepository.__new__(MongoRepository)
		sessions = MagicMock()
		sessions.find_one_and_update.return_value = {"_id": "s1", "interview_ended": {"round": "hr"}}
		repo.db = {"sessions": sessions}
		repo.mark_interview_finished(session_id="s1", round_name="hr")
		filters, update = sessions.find_one_and_update.call_args.args[:2]
		self.assertEqual(filters, {"_id": "s1"})
		self.assertEqual(update["$set"]["interview_ended"]["round"], "hr")


class ResetTests(TestCase):
	def test_rounds_of_a_finished_interview_cannot_be_reset(self) -> None:
		request = routes_workflow.WorkflowResetRequest(session_id="s1", target="technical")
		with patch("backend.api.routes_workflow._require_parent_session", return_value=_FINISHED):
			with self.assertRaises(HTTPException) as ctx:
				routes_workflow.reset_workflow_stage(request, _USER)
		self.assertEqual(ctx.exception.status_code, 409)
