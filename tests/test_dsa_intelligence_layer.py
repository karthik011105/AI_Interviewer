from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from backend.api import routes_dsa
from backend.database.supabase_client import DSAStage, build_default_candidate_model, build_default_dsa_state
from backend.dsa.code_analyzer import analyze_submission_code
from backend.dsa.interview_brain import apply_message_intelligence
from backend.dsa.problem_selector import CertifiedProblem, HiddenTestCase, ProblemExample


def _build_problem() -> CertifiedProblem:
	return CertifiedProblem(
		problem_id="two_sum_like",
		title="Two Sum Variant",
		statement="Find whether any pair sums to target.",
		constraints=("1 <= n <= 2e5",),
		source="custom",
		source_problem_id="tsv-1",
		source_url="https://example.com/problem",
		rating=1200,
		tags=("hashmap", "two_pointers"),
		roles=("backend_python_developer",),
		difficulty="easy_medium",
		starter_code="def solve() -> None:\n    pass\n",
		reference_solution="def solve():\n    pass\n",
		brute_force_solution=None,
		examples=(ProblemExample(input_text="4 9\n2 7 11 15", output_text="YES"),),
		hidden_test_cases=(HiddenTestCase(input_text="4 9\n2 7 11 15", output_text="YES"),),
		hints=("Track seen values in a hash set.", "Avoid comparing every pair explicitly."),
		followup_questions=("How would you return the indices?",),
	)


class DsaIntelligenceLayerTests(TestCase):
	def test_code_analyzer_detects_bruteforce_python_shape(self) -> None:
		analysis = analyze_submission_code(
			"def solve():\n    nums = [1, 2, 3]\n    for i in nums:\n        for j in nums:\n            print(i + j)\n",
			language="python",
			problem=_build_problem(),
		)

		self.assertEqual(analysis["max_loop_nesting_depth"], 2)
		self.assertEqual(analysis["brute_force_likelihood"], "high")
		self.assertEqual(analysis["optimality_guess"], "no")

	def test_interview_brain_escalates_hint_and_forces_coding_when_stuck(self) -> None:
		state = build_default_dsa_state(stage=DSAStage.APPROACH_DISCUSSION)
		state["approach_exchange_count"] = 3

		result = apply_message_intelligence(
			state=state,
			current_stage=DSAStage.APPROACH_DISCUSSION.value,
			message_text="I am stuck and not sure what data structure to use.",
			message_kind="approach",
			problem=_build_problem(),
		)

		self.assertEqual(result["hint_level"], 2)
		self.assertEqual(result["next_stage"], DSAStage.CODING.value)
		self.assertIn("Avoid comparing every pair explicitly", result["coaching_note"])

	def test_apply_post_submit_state_enters_optimization_for_bruteforce_acceptance(self) -> None:
		record = {
			"session_id": "session-123",
			"question_number": 1,
			"stage": DSAStage.CODING.value,
			"state_version": 4,
			"current_code_draft": (
				"def solve():\n"
				"    nums = [1, 2, 3]\n"
				"    for i in nums:\n"
				"        for j in nums:\n"
				"            if i != j:\n"
				"                print(i + j)\n"
			),
			"execution_results": {"passed_count": 3, "total_count": 3, "status": "accepted"},
			"state_json": {
				**build_default_dsa_state(stage=DSAStage.CODING),
				"current_language": "python",
				"candidate_model": build_default_candidate_model(),
			},
			"last_submission_id": "submit_abc",
			"last_judge_status": "ac",
		}

		def _persist_stub(**kwargs):
			return {
				"session_id": kwargs["session_id"],
				"question_number": kwargs["question_number"],
				"stage": kwargs["stage"],
				"state_json": kwargs["state_json"],
				"state_version": kwargs["expected_state_version"] + 1,
				"current_code_draft": record["current_code_draft"],
				"execution_results": record["execution_results"],
				"last_submission_id": record["last_submission_id"],
				"last_judge_status": record["last_judge_status"],
			}

		with patch("backend.api.routes_dsa.persist_dsa_state", side_effect=_persist_stub):
			updated_record = routes_dsa._apply_post_submit_state(
				record=record,
				session_id="session-123",
				question_number=1,
				problem=_build_problem(),
				next_stage=DSAStage.DEBRIEF.value,
			)

		self.assertEqual(updated_record["stage"], DSAStage.OPTIMIZATION.value)
		self.assertTrue(updated_record["state_json"]["optimization_used"])
		self.assertEqual(updated_record["state_json"]["latest_analysis"]["brute_force_likelihood"], "high")

	def test_finalize_completed_question_uses_rich_dimension_scoring(self) -> None:
		record = {
			"session_id": "session-123",
			"question_number": 1,
			"current_code_draft": (
				"def solve():\n"
				"    nums = [1, 2, 3]\n"
				"    for i in nums:\n"
				"        for j in nums:\n"
				"            if i < j:\n"
				"                print(i + j)\n"
			),
			"execution_results": {"passed_count": 3, "total_count": 3, "status": "accepted"},
			"state_json": {
				**build_default_dsa_state(stage=DSAStage.DEBRIEF),
				"current_language": "python",
				"hint_level": 2,
				"debrief_messages": [{"text": "I would improve this using a set and explain the complexity tradeoff."}],
				"candidate_model": {
					**build_default_candidate_model(),
					"understands_problem": "high",
					"identified_correct_approach": True,
					"explanation_quality": "clear",
				},
			},
		}

		def _complete_stub(**kwargs):
			return kwargs

		with patch("backend.api.routes_dsa.complete_dsa_session", side_effect=_complete_stub):
			result = routes_dsa._finalize_completed_question(
				record=record,
				session_id="session-123",
				question_number=1,
				problem=_build_problem(),
			)

		self.assertIn("approach_quality", result["dimension_scores"])
		self.assertIn("question_report", result["dimension_scores"])
		self.assertLess(result["total_score"], 1.0)

	def test_persist_round_completion_state_writes_rich_dsa_round_report(self) -> None:
		progress_summary = {
			"round_completed": True,
			"aggregate_score": 0.855,
			"completed_question_count": 2,
			"questions": [
				{"question_number": 1, "score": 0.8, "status": "complete"},
				{"question_number": 2, "score": 0.9, "status": "complete"},
			],
		}
		records = [
			{
				"question_number": 1,
				"problem_id": "two_sum_like",
				"total_score": 0.8,
				"dimension_scores": {
					"question_report": {
						"question_number": 1,
						"score": 0.8,
						"strengths": ["Hidden-test correctness remained strong."],
						"recommendations": ["Explain complexity more explicitly."],
						"dimension_scores": {"code_correctness": 0.8},
					},
				},
			},
			{
				"question_number": 2,
				"problem_id": "two_sum_like",
				"total_score": 0.9,
				"dimension_scores": {
					"question_report": {
						"question_number": 2,
						"score": 0.9,
						"strengths": ["The detected strategy looks close to the intended optimal shape."],
						"recommendations": ["Preserve this reasoning style."],
						"dimension_scores": {"code_correctness": 0.9},
					},
				},
			},
		]

		with patch("backend.api.routes_dsa.update_session_status"), \
			 patch("backend.api.routes_dsa.get_final_report", return_value=None), \
			 patch("backend.api.routes_dsa.list_dsa_sessions", return_value=records), \
			 patch("backend.api.routes_dsa._resolve_record_problem", return_value=_build_problem()), \
			 patch("backend.api.routes_dsa.upsert_final_report") as mock_upsert_final_report:
			result = routes_dsa._persist_round_completion_state(
				session_id="session-123",
				progress_summary=progress_summary,
			)

		self.assertTrue(result["persisted"])
		call = mock_upsert_final_report.call_args.kwargs
		self.assertIn("question_reports", call["report_json"]["dsa"])
		self.assertIn("question_dimensions", call["dimension_scores"]["dsa"])