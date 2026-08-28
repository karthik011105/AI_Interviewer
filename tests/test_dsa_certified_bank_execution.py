from __future__ import annotations

from unittest import TestCase

from backend.dsa.code_executor import judge0_health_check, run_sample, run_submission
from backend.dsa.problem_selector import list_certified_problems


class CertifiedProblemBankExecutionTests(TestCase):
	def setUp(self) -> None:
		health = judge0_health_check()
		if not health.get("available"):
			self.skipTest(str(health.get("detail") or "Judge0 is unavailable"))

	def test_reference_solutions_pass_examples_and_hidden_tests(self) -> None:
		problems = list_certified_problems()
		self.assertGreater(len(problems), 0)

		for problem in problems:
			with self.subTest(problem_id=problem.problem_id, phase="sample"):
				sample_result = run_sample(problem.reference_solution, problem.examples)
				self.assertEqual(
					sample_result["passed_count"],
					sample_result["total_count"],
					msg=f"Sample verification failed for {problem.problem_id}: {sample_result}",
				)

			with self.subTest(problem_id=problem.problem_id, phase="hidden"):
				submission_result = run_submission(problem.reference_solution, problem.hidden_test_cases)
				self.assertEqual(
					submission_result["passed_count"],
					submission_result["total_count"],
					msg=f"Hidden verification failed for {problem.problem_id}: {submission_result}",
				)