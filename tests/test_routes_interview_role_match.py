from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from backend.api import routes_interview
from backend.database.db_errors import DatabaseClientError


class ResolveRoleMatchTests(TestCase):
	"""`/resume/select-role` already computes and persists the role title and
	skill gaps into `interview_round_contexts`, but `_build_round_context` used
	to hardcode `role_match = None` and never read it back -- so every technical
	context got `selected_role_title=None` and `skill_gaps=[]` regardless of
	what was actually selected. `_resolve_role_match` is the fix: it recovers
	that stored context, and only falls back to a humanized role key when
	nothing was ever persisted or the lookup fails.
	"""

	def test_recovers_title_and_skill_gaps_from_stored_context(self) -> None:
		parent_session = {"id": "session-123", "role_selected": "embedded_systems_engineer"}
		stored_context = {
			"selected_role_key": "embedded_systems_engineer",
			"selected_role_title": "Embedded Systems Engineer",
			"skill_gaps": ["RTOS", "interrupt handling"],
		}

		with patch(
			"backend.api.routes_interview.get_interview_context", return_value=stored_context
		) as mock_get:
			role_match = routes_interview._resolve_role_match(parent_session, "embedded_systems_engineer")

		mock_get.assert_called_once_with(session_id="session-123", round="technical")
		self.assertEqual(
			role_match,
			{
				"role_key": "embedded_systems_engineer",
				"title": "Embedded Systems Engineer",
				"skill_gaps": ["RTOS", "interrupt handling"],
			},
		)

	def test_falls_back_to_humanized_title_when_nothing_stored(self) -> None:
		parent_session = {"id": "session-123", "role_selected": "embedded_systems_engineer"}

		with patch("backend.api.routes_interview.get_interview_context", return_value=None):
			role_match = routes_interview._resolve_role_match(parent_session, "embedded_systems_engineer")

		self.assertEqual(
			role_match,
			{"role_key": "embedded_systems_engineer", "title": "Embedded Systems Engineer"},
		)

	def test_falls_back_to_humanized_title_when_lookup_raises(self) -> None:
		parent_session = {"id": "session-123", "role_selected": "embedded_systems_engineer"}

		with patch(
			"backend.api.routes_interview.get_interview_context",
			side_effect=DatabaseClientError("mongo down"),
		):
			role_match = routes_interview._resolve_role_match(parent_session, "embedded_systems_engineer")

		self.assertEqual(
			role_match,
			{"role_key": "embedded_systems_engineer", "title": "Embedded Systems Engineer"},
		)

	def test_returns_none_without_a_role_key(self) -> None:
		self.assertIsNone(routes_interview._resolve_role_match({"id": "session-123"}, None))
