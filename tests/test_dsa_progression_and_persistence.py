from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from backend.api.auth import AuthenticatedUser
from backend.api import routes_dsa


class DsaProgressionAndPersistenceTests(TestCase):
	def setUp(self) -> None:
		self.current_user = AuthenticatedUser(
			user_id="user-123",
			email="user@example.com",
			raw_user={"id": "user-123", "email": "user@example.com"},
		)

	def test_start_dsa_round_blocks_question_two_until_q1_hidden_tests_pass(self) -> None:
		request = routes_dsa.DSAStartRequest(
			session_id="session-123",
			question_number=2,
			role_key="backend_python_developer",
		)

		with patch("backend.api.routes_dsa._require_parent_session", return_value={"role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_dsa.get_dsa_session", return_value=None):
			with self.assertRaises(HTTPException) as context:
				routes_dsa.start_dsa_round(request, self.current_user)

		self.assertEqual(context.exception.status_code, 409)
		self.assertIn("Question 1 hidden tests must pass", str(context.exception.detail))

	def test_start_dsa_round_allows_question_two_once_q1_enters_debrief(self) -> None:
		request = routes_dsa.DSAStartRequest(
			session_id="session-123",
			question_number=2,
			role_key="backend_python_developer",
		)
		ready_q1_record = {
			"session_id": "session-123",
			"question_number": 1,
			"problem_id": "cf_4a_watermelon",
			"stage": routes_dsa.DSAStage.DEBRIEF.value,
			"state_json": {"current_language": "python", "question_started_at": "2026-05-11T10:00:00+00:00", "deadline_at": "2026-05-11T10:20:00+00:00"},
			"execution_results": {"passed_count": 4, "total_count": 4},
		}
		created_q2_record = {
			"session_id": "session-123",
			"question_number": 2,
			"problem_id": "cf_158a_next_round",
			"stage": routes_dsa.DSAStage.PROBLEM_SETUP.value,
			"state_json": {"current_language": "python", "question_started_at": "2026-05-11T10:30:00+00:00", "deadline_at": "2026-05-11T10:50:00+00:00"},
			"deadline_at": "2026-05-11T10:50:00+00:00",
			"current_code_draft": "def solve():\n    pass\n",
		}
		problem_q2 = routes_dsa.CertifiedProblem(
			problem_id="cf_158a_next_round",
			title="Next Round Cutoff",
			statement="statement",
			constraints=(),
			source="codeforces",
			source_problem_id="158A",
			source_url="https://codeforces.com/problemset/problem/158/A",
			rating=800,
			tags=(),
			roles=("backend_python_developer",),
			difficulty="easy",
			starter_code="def solve() -> None:\n    pass\n",
			reference_solution="def solve() -> None:\n    pass\n",
			brute_force_solution=None,
			examples=(),
			hidden_test_cases=(),
			hints=(),
			followup_questions=(),
		)

		with patch("backend.api.routes_dsa._require_parent_session", return_value={"role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_dsa.get_dsa_session", side_effect=[ready_q1_record, None]), \
			 patch("backend.api.routes_dsa.judge0_health_check", return_value={"available": True}), \
			 patch("backend.api.routes_dsa.select_problem_pair", return_value=(problem_q2, problem_q2)), \
			 patch("backend.api.routes_dsa.create_dsa_session", return_value=created_q2_record), \
			 patch("backend.api.routes_dsa._serialize_dsa_session_record", return_value={"question_number": 2, "stage": routes_dsa.DSAStage.PROBLEM_SETUP.value}):
			response = routes_dsa.start_dsa_round(request, self.current_user)

		self.assertEqual(response["question_number"], 2)

	def test_persist_round_completion_state_merges_dsa_score_into_final_report(self) -> None:
		progress_summary = {
			"round_completed": True,
			"aggregate_score": 0.91,
			"completed_question_count": 2,
			"questions": [
				{"question_number": 1, "score": 0.8, "status": "complete"},
				{"question_number": 2, "score": 1.0, "status": "complete"},
			],
		}
		existing_report = {
			"round_scores": {"technical": 0.7},
			"dimension_scores": {"technical": {"evaluation_mode": "groq"}},
			"report_json": {"technical": {"status": "complete"}},
		}

		with patch("backend.api.routes_dsa.update_session_status") as mock_update_session_status, \
			 patch("backend.api.routes_dsa.get_final_report", return_value=existing_report), \
			 patch("backend.api.routes_dsa.list_dsa_sessions", return_value=[]), \
			 patch("backend.api.routes_dsa.upsert_final_report") as mock_upsert_final_report:
			result = routes_dsa._persist_round_completion_state(
				session_id="session-123",
				progress_summary=progress_summary,
			)

		self.assertTrue(result["persisted"])
		self.assertEqual(result["session_status"], "dsa_complete")
		self.assertAlmostEqual(result["overall_score"], 0.805, places=4)
		mock_update_session_status.assert_called_once_with("session-123", "dsa_complete")
		mock_upsert_final_report.assert_called_once()
		call = mock_upsert_final_report.call_args.kwargs
		self.assertEqual(call["session_id"], "session-123")
		self.assertAlmostEqual(call["overall_score"], 0.805, places=4)
		self.assertEqual(call["round_scores"]["technical"], 0.7)
		self.assertEqual(call["round_scores"]["dsa"], 0.91)
		self.assertEqual(call["dimension_scores"]["dsa"]["question_scores"], {"q1": 0.8, "q2": 1.0})
		self.assertEqual(call["report_json"]["dsa"]["status"], "complete")

	def test_last_question_submit_auto_completes_round_after_hidden_test_pass(self) -> None:
		record = {
			"session_id": "session-123",
			"question_number": 2,
			"stage": routes_dsa.DSAStage.DEBRIEF.value,
			"state_json": {"current_language": "python"},
		}
		problem = routes_dsa.CertifiedProblem(
			problem_id="cf_158a_next_round",
			title="Next Round Cutoff",
			statement="statement",
			constraints=(),
			source="codeforces",
			source_problem_id="158A",
			source_url="https://codeforces.com/problemset/problem/158/A",
			rating=800,
			tags=(),
			roles=("backend_python_developer",),
			difficulty="easy",
			starter_code="def solve() -> None:\n    pass\n",
			reference_solution="def solve() -> None:\n    pass\n",
			brute_force_solution=None,
			examples=(),
			hidden_test_cases=(),
			hints=(),
			followup_questions=(),
		)
		completed_record = {
			**record,
			"stage": routes_dsa.DSAStage.COMPLETE.value,
			"completed_at": "2026-05-11T11:00:00+00:00",
		}
		progress_summary = {"round_completed": True}
		report_state = {"session_status": "dsa_complete", "persisted": True}

		with patch("backend.api.routes_dsa._finalize_completed_question", return_value=completed_record) as mock_finalize, \
			 patch("backend.api.routes_dsa._get_dsa_progress_summary", return_value=progress_summary) as mock_progress, \
			 patch("backend.api.routes_dsa._persist_round_completion_state", return_value=report_state) as mock_report:
			result_record, result_progress, result_report = routes_dsa._maybe_complete_last_question_after_submission(
				record=record,
				session_id="session-123",
				question_number=2,
				problem=problem,
			)

		self.assertEqual(result_record["stage"], routes_dsa.DSAStage.COMPLETE.value)
		self.assertEqual(result_progress, progress_summary)
		self.assertEqual(result_report, report_state)
		mock_finalize.assert_called_once()
		mock_progress.assert_called_once_with("session-123")
		mock_report.assert_called_once_with(session_id="session-123", progress_summary=progress_summary)

	def test_progress_summary_counts_q1_debrief_with_passed_hidden_tests_as_complete(self) -> None:
		records = [
			{
				"session_id": "session-123",
				"question_number": 1,
				"stage": routes_dsa.DSAStage.DEBRIEF.value,
				"execution_results": {"passed_count": 4, "total_count": 4},
			},
			{
				"session_id": "session-123",
				"question_number": 2,
				"stage": routes_dsa.DSAStage.COMPLETE.value,
				"completed_at": "2026-05-11T11:00:00+00:00",
				"total_score": 0.9,
				"execution_results": {"passed_count": 5, "total_count": 5},
			},
		]

		summary = routes_dsa._build_dsa_progress_summary_from_records(records)

		self.assertTrue(summary["round_completed"])
		self.assertEqual(summary["completed_question_count"], 2)
		self.assertEqual(summary["questions"][0]["status"], "complete")
		self.assertAlmostEqual(summary["aggregate_score"], 0.945, places=4)