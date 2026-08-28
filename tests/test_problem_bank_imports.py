from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from backend.dsa.problem_selector import CertifiedProblemBankError, load_problem_banks_from_paths


def _build_problem_payload(*, problem_id: str, source: str, source_problem_id: str, source_url: str, role_set: str = "core_dsa") -> dict[str, object]:
	return {
		"problem_id": problem_id,
		"title": f"{problem_id} title",
		"statement": f"Solve {problem_id}.",
		"constraints": ["1 <= n <= 1000"],
		"source": source,
		"source_problem_id": source_problem_id,
		"source_url": source_url,
		"rating": 900,
		"tags": ["arrays"],
		"role_set": role_set,
		"difficulty": "easy",
		"starter_code": "def solve() -> None:\n    value = int(input().strip())\n    # TODO: print value\n\n\nif __name__ == \"__main__\":\n    solve()\n",
		"reference_solution": "def solve() -> None:\n    value = int(input().strip())\n    print(value)\n\n\nif __name__ == \"__main__\":\n    solve()\n",
		"brute_force_solution": None,
		"examples": [
			{
				"input_text": "7\n",
				"output_text": "7\n",
				"explanation": "Echo the input.",
			}
		],
		"hidden_test_cases": [
			{"input_text": "1\n", "output_text": "1\n"},
			{"input_text": "5\n", "output_text": "5\n"},
			{"input_text": "9\n", "output_text": "9\n"},
		],
		"hints": [
			"Read the input.",
			"Keep the same value.",
			"Print it back out.",
		],
		"followup_questions": ["How would you validate malformed input?"],
	}


def _write_bank(path: Path, problems: list[dict[str, object]], role_sets: dict[str, list[str]] | None = None) -> None:
	payload = {
		"schema_version": 1,
		"role_sets": role_sets
		or {
			"core_dsa": [
				"software_engineer",
				"backend_python_developer",
				"backend_java_developer",
				"backend_node_developer",
				"frontend_react_developer",
				"full_stack_developer",
				"mobile_app_developer",
				"data_engineer",
				"machine_learning_engineer",
				"ai_engineer",
				"embedded_systems_engineer",
				"iot_engineer",
				"robotics_software_engineer",
				"site_reliability_engineer",
				"firmware_engineer",
			]
		},
		"problems": problems,
	}
	path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class ProblemBankImportsTests(TestCase):
	def test_multiple_problem_banks_merge_generic_sources(self) -> None:
		with TemporaryDirectory() as temp_dir:
			temp_path = Path(temp_dir)
			primary_bank = temp_path / "certified_bank.json"
			secondary_bank = temp_path / "leetcode_bank.json"
			_write_bank(
				primary_bank,
				[
					_build_problem_payload(
						problem_id="cf_echo",
						source="codeforces",
						source_problem_id="1A",
						source_url="https://codeforces.com/problemset/problem/1/A",
					)
				],
			)
			_write_bank(
				secondary_bank,
				[
					_build_problem_payload(
						problem_id="lc_echo",
						source="leetcode",
						source_problem_id="1",
						source_url="https://example.com/leetcode/1",
					)
				],
			)

			problems = load_problem_banks_from_paths((primary_bank, secondary_bank))

			self.assertEqual({problem.problem_id for problem in problems}, {"cf_echo", "lc_echo"})
			self.assertEqual({problem.source for problem in problems}, {"codeforces", "leetcode"})

	def test_duplicate_problem_ids_across_banks_are_rejected(self) -> None:
		with TemporaryDirectory() as temp_dir:
			temp_path = Path(temp_dir)
			primary_bank = temp_path / "certified_bank.json"
			secondary_bank = temp_path / "external_bank.json"
			problem = _build_problem_payload(
				problem_id="shared_problem",
				source="codeforces",
				source_problem_id="1A",
				source_url="https://codeforces.com/problemset/problem/1/A",
			)
			_write_bank(primary_bank, [problem])
			_write_bank(secondary_bank, [dict(problem)])

			with self.assertRaises(CertifiedProblemBankError) as context:
				load_problem_banks_from_paths((primary_bank, secondary_bank))

			self.assertIn("Duplicate problem_id 'shared_problem'", str(context.exception))