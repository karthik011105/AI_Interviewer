"""Technical round question formats: code output, find the bug, numerical..."""

from __future__ import annotations

import json
from unittest import TestCase

from backend.api.interview_engines.dynamic import _question_item, _with_snippet
from backend.nlp.coverage_director import build_coverage_plan, build_director_block
from backend.nlp.interview_director import META_SENTINEL, build_director_system_prompt, parse_director_output
from backend.nlp.question_formats import (
	CODE_OUTPUT,
	FIND_BUG,
	NUMERICAL,
	SQL_OUTPUT,
	format_instruction,
	format_mix,
	role_family,
	snippet_language,
)


class RoleMixTests(TestCase):
	def test_families(self) -> None:
		cases = {
			("java_backend_developer", "Java Backend Developer"): "software",
			("junior_machine_learning_engineer", ""): "ml",
			("data_scientist", "Data Scientist"): "ml",
			("data_analyst", "Data Analyst"): "data",
			("junior_data_engineer", "Junior Data Engineer"): "software",
			("cloud_devops_engineer", ""): "devops",
			("cybersecurity_analyst", ""): "security",
			("custom_role", "Frontend React Developer"): "software",
		}
		for (key, title), family in cases.items():
			with self.subTest(key=key):
				self.assertEqual(role_family(key, title), family)

	def test_java_round_reads_code_and_finds_a_bug(self) -> None:
		mix = format_mix("java_backend_developer", "Java Backend Developer", 6)
		self.assertEqual(mix.count(CODE_OUTPUT), 2)
		self.assertEqual(mix.count(FIND_BUG), 1)

	def test_ml_round_has_numerical_problems(self) -> None:
		self.assertEqual(format_mix("machine_learning_engineer", "", 6).count(NUMERICAL), 2)

	def test_analyst_round_reads_sql(self) -> None:
		self.assertEqual(format_mix("data_analyst", "", 6).count(SQL_OUTPUT), 2)

	def test_snippets_never_come_back_to_back(self) -> None:
		for role in ("java_developer", "ml_engineer", "data_analyst", "devops_engineer", "security_analyst"):
			mix = format_mix(role, "", 6)
			for first, second in zip(mix, mix[1:]):
				with self.subTest(role=role, pair=(first, second)):
					self.assertFalse(first == second and first != "concept")


class LanguageTests(TestCase):
	def test_role_language_wins(self) -> None:
		self.assertEqual(snippet_language("java_backend_developer", "", ["Python"]), "java")
		self.assertEqual(snippet_language("frontend_react_developer", "", []), "javascript")

	def test_ml_and_data_default_to_python(self) -> None:
		self.assertEqual(snippet_language("data_scientist", "", ["R"]), "python")

	def test_otherwise_the_candidates_skills_decide(self) -> None:
		self.assertEqual(snippet_language("backend_engineer", "", ["Go", "Docker"]), "go")
		self.assertEqual(snippet_language("software_engineer", "", []), "python")

	def test_instruction_names_the_language_and_the_expected_output(self) -> None:
		text = format_instruction(CODE_OUTPUT, "java")
		self.assertIn("Java", text)
		self.assertIn("Output:", text)
		self.assertIn('"snippet"', text)


class PlanTests(TestCase):
	def test_targets_carry_their_format_into_the_director_steering(self) -> None:
		plan = build_coverage_plan([
			{"focus_skill": "Java collections", "question_tier": "strong", "question_format": CODE_OUTPUT},
		])
		plan["snippet_language"] = "java"
		block = build_director_block(plan, turns_used=0)
		self.assertIn("Question format   : code_output", block)
		self.assertIn("Write it in Java", block)

	def test_targets_without_a_format_are_unchanged(self) -> None:
		plan = build_coverage_plan([{"focus_skill": "Teamwork", "question_tier": "behavioural"}])
		self.assertNotIn("question_format", plan["targets"][0])
		self.assertNotIn("Question format", build_director_block(plan, turns_used=0))


class SnippetTests(TestCase):
	def _turn(self, snippet):
		meta = {"action": "new_topic", "focus_skill": "Java", "ideal_points": ["Output: false"], "snippet": snippet}
		return parse_director_output("Have a look at the code on your screen.\n" + META_SENTINEL + "\n" + json.dumps(meta))

	def test_snippet_is_parsed(self) -> None:
		turn = self._turn({"language": "Java", "code": 'String a = "hi";\nSystem.out.println(a == "hi");'})
		self.assertEqual(turn["snippet"]["language"], "java")
		self.assertIn("println", turn["snippet"]["code"])

	def test_missing_empty_or_huge_snippets_are_dropped(self) -> None:
		self.assertIsNone(self._turn(None)["snippet"])
		self.assertIsNone(self._turn({"language": "java", "code": "   "})["snippet"])
		self.assertIsNone(self._turn({"language": "java", "code": "x\n" * 60})["snippet"])

	def test_the_evaluator_sees_the_code_with_the_question(self) -> None:
		turn = self._turn({"language": "java", "code": "System.out.println(1 + 2 + \"3\");"})
		item = _question_item(turn)
		judged = _with_snippet(item["question"], item["snippet"])
		self.assertIn("Shown on screen (java)", judged)
		self.assertIn('println(1 + 2 + "3")', judged)
		self.assertEqual(_with_snippet("Explain GC.", None), "Explain GC.")

	def test_prompt_keeps_code_out_of_the_spoken_words(self) -> None:
		prompt = build_director_system_prompt({"selected_role_key": "java_developer"}, role_title="Java Developer")
		self.assertIn("Never read code", prompt)
		self.assertIn('"snippet":null', prompt)
		self.assertNotIn("Every question must be conceptual", prompt)


class TargetLanguageTests(TestCase):
	def test_sql_topics_show_sql_even_in_a_java_round(self) -> None:
		from backend.nlp.question_formats import target_format_and_language

		self.assertEqual(target_format_and_language(CODE_OUTPUT, "Database modelling and SQL", "java"), (SQL_OUTPUT, "sql"))
		self.assertEqual(target_format_and_language(FIND_BUG, "SQL joins", "java"), (FIND_BUG, "sql"))

	def test_a_language_topic_uses_that_language(self) -> None:
		from backend.nlp.question_formats import target_format_and_language

		self.assertEqual(target_format_and_language(CODE_OUTPUT, "Python", "java"), (CODE_OUTPUT, "python"))
		self.assertEqual(target_format_and_language(CODE_OUTPUT, "Caching strategies", "java"), (CODE_OUTPUT, "java"))


class FallbackTests(TestCase):
	def test_fallback_never_asks_for_a_definition(self) -> None:
		from backend.nlp.coverage_director import fallback_question

		for question_format in ("concept", "code_output", "scenario"):
			plan = build_coverage_plan([{"focus_skill": "Python", "question_tier": "strong", "question_format": question_format}])
			with self.subTest(question_format=question_format):
				self.assertNotIn("explain what", fallback_question(plan).casefold())


class RateLimitWaitTests(TestCase):
	def test_reads_groqs_wait_hint(self) -> None:
		from backend.nlp.groq_client import rate_limit_wait_seconds

		self.assertAlmostEqual(rate_limit_wait_seconds(Exception("Please try again in 2.8125s.")), 2.8125)
		self.assertAlmostEqual(rate_limit_wait_seconds(Exception("try again in 450ms")), 0.45)
		self.assertAlmostEqual(rate_limit_wait_seconds(Exception("try again in 7m12s")), 432)
		self.assertIsNone(rate_limit_wait_seconds(Exception("rate_limit_exceeded")))


class SnippetShapeTests(TestCase):
	def test_bare_string_and_other_key_names_are_accepted(self) -> None:
		from backend.nlp.interview_director import _coerce_snippet

		self.assertEqual(_coerce_snippet("print(1)")["code"], "print(1)")
		self.assertEqual(_coerce_snippet({"lang": "Python", "content": "print(1)"}), {"language": "python", "code": "print(1)"})


class NumericalPlacementTests(TestCase):
	def test_calculations_go_to_maths_friendly_topics(self) -> None:
		from backend.nlp.question_formats import place_numerical

		skills = ["python", "aws", "pytorch", "numpy", "Django", "FastAPI"]
		formats = place_numerical(format_mix("machine_learning_engineer", "", 6), skills)
		numerical_topics = {skills[i] for i, f in enumerate(formats) if f == NUMERICAL}
		self.assertEqual(numerical_topics, {"pytorch", "numpy"})
		self.assertEqual(formats.count(NUMERICAL), 2)

	def test_nothing_moves_without_a_better_topic(self) -> None:
		from backend.nlp.question_formats import place_numerical

		mix = format_mix("machine_learning_engineer", "", 6)
		self.assertEqual(place_numerical(mix, ["a", "b", "c", "d", "e", "f"]), mix)


class FollowUpTests(TestCase):
	"""Follow-ups only when an answer leaves a doubt, and not every time."""

	def _plan(self, targets=6):
		# Room to spare, so "time is short" doesn't override the follow-up rules.
		return build_coverage_plan(
			[{"focus_skill": f"Skill {i}", "question_tier": "strong"} for i in range(targets)], max_turns=16
		)

	def test_budget_is_about_two_in_five_questions(self) -> None:
		from backend.nlp.coverage_director import follow_up_budget

		self.assertEqual(follow_up_budget(self._plan(6)), 2)
		self.assertEqual(follow_up_budget(self._plan(1)), 1)

	def test_a_follow_up_is_offered_only_for_unclear_or_wrong_answers(self) -> None:
		block = build_director_block(self._plan(), turns_used=1)
		self.assertIn("only if their last answer was unclear", block)
		self.assertIn("Follow-ups left this round: 2", block)

	def test_never_twice_in_a_row_and_never_past_the_budget(self) -> None:
		self.assertIn("you just asked one", build_director_block(self._plan(), turns_used=2, last_action="follow_up"))
		self.assertIn("used up", build_director_block(self._plan(), turns_used=4, follow_ups_used=2, last_action="new_topic"))

	def test_the_opening_question_is_never_a_follow_up(self) -> None:
		self.assertIn("Open the interview", build_director_block(self._plan(), turns_used=0))

	def test_messages_count_follow_ups_from_the_dialogue(self) -> None:
		from backend.nlp.interview_director import build_director_messages

		dialogue = [
			{"i": 0, "role": "interviewer", "text": "Q1", "action": "new_topic"},
			{"i": 0, "role": "candidate", "text": "A1"},
			{"i": 1, "role": "interviewer", "text": "Q1b", "action": "follow_up"},
			{"i": 1, "role": "candidate", "text": "A1b"},
		]
		messages = build_director_messages(system="s", dialogue=dialogue, coverage_plan=self._plan(), turns_used=2)
		self.assertIn("you just asked one", messages[-1]["content"])

	def test_prompts_explain_when_to_follow_up(self) -> None:
		for round_type in ("technical", "hr", "project_discussion"):
			prompt = build_director_system_prompt({}, role_title="Java Developer", round_type=round_type)
			with self.subTest(round_type=round_type):
				self.assertIn("Most good answers get no follow-up", prompt)
				self.assertNotIn("unusually strong", prompt)
