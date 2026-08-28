from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from backend.dsa.problem_validator import validate_generated_problem_candidate, write_generated_problem_draft


def _build_generated_problem_payload() -> dict[str, object]:
	return {
		"problem_id": "generated_echo",
		"title": "Generated Echo",
		"statement": "Read integers and print the computed result.",
		"constraints": ["1 <= n <= 1000"],
		"source": "generated",
		"source_problem_id": "draft-1",
		"source_url": "https://example.com/generated/1",
		"rating": 900,
		"tags": ["arrays", "hashmap"],
		"roles": ["software_engineer"],
		"difficulty": "easy",
		"starter_code": "def solve() -> None:\n    pass\n",
		"reference_solution": "# primary\ndef solve() -> None:\n    pass\n",
		"brute_force_solution": None,
		"examples": [
			{"input_text": "4 9\n2 7 11 15\n", "output_text": "YES\n", "explanation": "Two values can hit the target."}
		],
		"hidden_test_cases": [
			{"input_text": "4 9\n2 7 11 15\n", "output_text": "YES\n"},
			{"input_text": "4 10\n2 7 11 15\n", "output_text": "NO\n"},
			{"input_text": "5 6\n1 2 3 4 5\n", "output_text": "YES\n"},
		],
		"hints": [
			"Track prior values while scanning.",
			"Avoid comparing every pair explicitly.",
			"Think in terms of complement lookup.",
		],
		"followup_questions": ["How would you return indices instead of YES/NO?"],
	}


def _run_submission_agree_stub(code: str, hidden_test_cases: list[object], *, language: str = "python") -> dict[str, object]:
	del code, language
	case_results = []
	for index, case in enumerate(hidden_test_cases):
		expected_output = str(case.output_text or "")
		actual_output = expected_output or f"derived:{index}"
		case_results.append(
			{
				"case_index": index,
				"passed": actual_output == expected_output,
				"expected_output": expected_output,
				"actual_output": actual_output,
				"error": None,
			}
		)
	return {
		"status": "Accepted",
		"passed_count": sum(1 for case_result in case_results if case_result["passed"]),
		"total_count": len(case_results),
		"case_results": case_results,
		"stdout": "",
		"stderr": None,
		"compile_output": None,
		"time_seconds": 0.01,
		"memory_kb": 1024,
		"raw_status_id": 3,
		"raw_status_description": "Accepted",
	}


def _run_submission_disagree_stub(code: str, hidden_test_cases: list[object], *, language: str = "python") -> dict[str, object]:
	del language
	is_alternate = "alternate" in code
	case_results = []
	for index, case in enumerate(hidden_test_cases):
		expected_output = str(case.output_text or "")
		if expected_output:
			actual_output = expected_output
		else:
			actual_output = f"generated:{index}" if not is_alternate else f"alternate:{index}"
		case_results.append(
			{
				"case_index": index,
				"passed": actual_output == expected_output,
				"expected_output": expected_output,
				"actual_output": actual_output,
				"error": None,
			}
		)
	return {
		"status": "Accepted",
		"passed_count": sum(1 for case_result in case_results if case_result["passed"]),
		"total_count": len(case_results),
		"case_results": case_results,
		"stdout": "",
		"stderr": None,
		"compile_output": None,
		"time_seconds": 0.01,
		"memory_kb": 1024,
		"raw_status_id": 3,
		"raw_status_description": "Accepted",
	}


class ProblemValidatorTests(TestCase):
	def test_validate_generated_problem_candidate_accepts_matching_solutions(self) -> None:
		with patch("backend.dsa.problem_validator.run_submission", side_effect=_run_submission_agree_stub) as mock_run_submission:
			result = validate_generated_problem_candidate(
				_build_generated_problem_payload(),
				alternate_solution="# alternate\ndef solve() -> None:\n    pass\n",
			)

		self.assertTrue(result["validation_passed"])
		self.assertEqual(result["status"], "draft")
		self.assertGreater(result["generated_case_count"], 0)
		self.assertEqual(mock_run_submission.call_count, 2)
		self.assertEqual(result["draft_payload"]["promotion_state"], "human_review_required")

	def test_validate_generated_problem_candidate_rejects_generated_disagreement(self) -> None:
		with patch("backend.dsa.problem_validator.run_submission", side_effect=_run_submission_disagree_stub):
			result = validate_generated_problem_candidate(
				_build_generated_problem_payload(),
				alternate_solution="# alternate\ndef solve() -> None:\n    pass\n",
			)

		self.assertFalse(result["validation_passed"])
		self.assertEqual(result["status"], "rejected")
		self.assertTrue(any(item["kind"] == "generated" for item in result["mismatches"]))

	def test_write_generated_problem_draft_persists_payload(self) -> None:
		with patch("backend.dsa.problem_validator.run_submission", side_effect=_run_submission_agree_stub):
			result = validate_generated_problem_candidate(
				_build_generated_problem_payload(),
				alternate_solution="# alternate\ndef solve() -> None:\n    pass\n",
			)

		with TemporaryDirectory() as temp_dir:
			output_path = Path(temp_dir) / "generated_echo_draft.json"
			written_path = write_generated_problem_draft(result, output_path=output_path)
			payload = json.loads(written_path.read_text(encoding="utf-8"))

		self.assertEqual(written_path, output_path)
		self.assertEqual(payload["problem"]["problem_id"], "generated_echo")
		self.assertEqual(payload["status"], "draft")