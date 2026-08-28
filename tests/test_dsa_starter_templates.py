from __future__ import annotations

from unittest import TestCase

from backend.dsa.code_executor import build_editor_starter_code
from backend.dsa.problem_selector import get_certified_problem


class DsaStarterTemplateTests(TestCase):
	def test_cpp_starter_template_uses_problem_specific_python_hints(self) -> None:
		problem = get_certified_problem("cf_4a_watermelon")
		starter = build_editor_starter_code(
			"cpp",
			prompt_title=problem.title,
			python_starter_code=problem.starter_code,
			example_input=problem.examples[0].input_text,
			example_output=problem.examples[0].output_text,
		)

		self.assertIn("// Watermelon", starter)
		self.assertIn("int weight = 0;", starter)
		self.assertIn("cin >> weight;", starter)
		self.assertIn("TODO: print YES or NO", starter)

	def test_java_starter_template_uses_problem_specific_python_hints(self) -> None:
		problem = get_certified_problem("cf_263a_beautiful_matrix")
		starter = build_editor_starter_code(
			"java",
			prompt_title=problem.title,
			python_starter_code=problem.starter_code,
			example_input=problem.examples[0].input_text,
			example_output=problem.examples[0].output_text,
		)

		self.assertIn("// Beautiful Matrix", starter)
		self.assertIn("Scanner scanner = new Scanner(System.in);", starter)
		self.assertIn("int[][] matrix = new int[5][5];", starter)
		self.assertIn("matrix[row][col] = scanner.nextInt();", starter)
		self.assertIn("TODO: print the Manhattan distance to the center", starter)