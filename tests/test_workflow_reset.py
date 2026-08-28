from unittest import TestCase
from unittest.mock import patch

from backend.api import routes_workflow


class WorkflowResetTests(TestCase):
	def setUp(self) -> None:
		self.current_user = routes_workflow.AuthenticatedUser(
			user_id="user-123",
			email="candidate@example.com",
			raw_user={"id": "user-123", "email": "candidate@example.com"},
		)

	def test_reset_technical_round_cascades_downstream_rounds(self) -> None:
		request = routes_workflow.WorkflowResetRequest(
			session_id="session-123",
			target="technical",
		)

		with patch("backend.api.routes_workflow._require_parent_session", return_value={"status": "dsa_complete", "role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_workflow.delete_interview_round_session", side_effect=[1, 1, 1]) as mock_delete_round, \
			 patch("backend.api.routes_workflow.delete_interview_responses", side_effect=[2, 3, 1]) as mock_delete_responses, \
			 patch("backend.api.routes_workflow.delete_dsa_sessions", return_value=2) as mock_delete_dsa, \
			 patch("backend.api.routes_workflow.delete_final_report", return_value=1) as mock_delete_report, \
			 patch("backend.api.routes_workflow.update_session_status", return_value={"status": "created"}) as mock_update_status:
			result = routes_workflow.reset_workflow_stage(request, self.current_user)

		self.assertEqual(result["cleared_targets"], ["technical", "dsa", "project_discussion", "hr", "report"])
		self.assertEqual(result["session_status"], "created")
		self.assertEqual(mock_delete_round.call_count, 3)
		self.assertEqual(mock_delete_responses.call_count, 3)
		mock_delete_dsa.assert_called_once_with("session-123")
		mock_delete_report.assert_called_once_with("session-123")
		mock_update_status.assert_called_once_with("session-123", "created")

		cleared_rounds = [call.args[1] for call in mock_delete_round.call_args_list]
		self.assertEqual(cleared_rounds, ["technical", "project_discussion", "hr"])

	def test_reset_hr_round_only_clears_hr_and_report(self) -> None:
		request = routes_workflow.WorkflowResetRequest(
			session_id="session-123",
			target="hr",
		)

		with patch("backend.api.routes_workflow._require_parent_session", return_value={"status": "dsa_complete", "role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_workflow.delete_interview_round_session", return_value=1) as mock_delete_round, \
			 patch("backend.api.routes_workflow.delete_interview_responses", return_value=4) as mock_delete_responses, \
			 patch("backend.api.routes_workflow.delete_final_report", return_value=1) as mock_delete_report, \
			 patch("backend.api.routes_workflow.update_session_status") as mock_update_status:
			result = routes_workflow.reset_workflow_stage(request, self.current_user)

		self.assertEqual(result["cleared_targets"], ["hr", "report"])
		self.assertEqual(result["session_status"], "dsa_complete")
		mock_delete_round.assert_called_once_with("session-123", "hr")
		mock_delete_responses.assert_called_once_with("session-123", "hr")
		mock_delete_report.assert_called_once_with("session-123")
		mock_update_status.assert_not_called()

	def test_reset_entire_interview_clears_assessment_and_all_rounds(self) -> None:
		request = routes_workflow.WorkflowResetRequest(
			session_id="session-123",
			target="entire_interview",
		)

		with patch("backend.api.routes_workflow._require_parent_session", return_value={"status": "dsa_complete", "role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_workflow.delete_assessment_session", return_value=1) as mock_delete_assessment, \
			 patch("backend.api.routes_workflow.delete_interview_round_session", side_effect=[1, 1, 1]) as mock_delete_round, \
			 patch("backend.api.routes_workflow.delete_interview_responses", side_effect=[2, 2, 2]) as mock_delete_responses, \
			 patch("backend.api.routes_workflow.delete_dsa_sessions", return_value=2) as mock_delete_dsa, \
			 patch("backend.api.routes_workflow.delete_final_report", return_value=1) as mock_delete_report, \
			 patch("backend.api.routes_workflow.update_session_status", return_value={"status": "created"}) as mock_update_status:
			result = routes_workflow.reset_workflow_stage(request, self.current_user)

		self.assertEqual(result["cleared_targets"], ["assessment", "technical", "dsa", "project_discussion", "hr", "report"])
		self.assertEqual(result["summary"]["assessment_session"], 1)
		mock_delete_assessment.assert_called_once_with("session-123")
		self.assertEqual(mock_delete_round.call_count, 3)
		self.assertEqual(mock_delete_responses.call_count, 3)
		mock_delete_dsa.assert_called_once_with("session-123")
		mock_delete_report.assert_called_once_with("session-123")
		mock_update_status.assert_called_once_with("session-123", "created")