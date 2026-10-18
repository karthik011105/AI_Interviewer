"""HR and project discussion on the conversational engine.

The engine was built for the technical round: its plan came from the skill
profile and its interviewer prompt forbade behavioural questions. Turning it on
for HR without per-round plans and prompts would have run a technical screen
labelled "HR". These pin what each round is now steered towards.
"""

from __future__ import annotations

import json
from unittest import TestCase

from backend.api.routes_interview import _get_question_at_index, _total_question_count
from backend.api.routes_report import _count_round_questions
from backend.nlp.coverage_director import fallback_question
from backend.nlp.interview_director import META_SENTINEL, build_director_system_prompt
from backend.nlp.question_generator import is_non_technical_hr_question
from backend.nlp.round_plans import HR_COMPETENCIES, build_round_coverage_plan

_PROJECTS = [
	{"title": "Acme Microservices", "description": "FastAPI services", "tech_stack": ["FastAPI"], "role": "Engineer", "outcomes": ["40% faster"]},
	{"title": "Chat App", "description": "Realtime chat"},
]


class PlanTests(TestCase):
	def test_hr_plan_covers_behavioural_competencies_not_skills(self) -> None:
		plan = build_round_coverage_plan("hr", {"candidate_name": "Alex"})
		self.assertEqual([t["focus_skill"] for t in plan["targets"]], list(HR_COMPETENCIES))
		self.assertEqual({t["question_tier"] for t in plan["targets"]}, {"behavioural"})
		self.assertEqual(plan["round"], "hr")

	def test_project_plan_is_built_from_the_resume_projects(self) -> None:
		plan = build_round_coverage_plan("project_discussion", {"projects": _PROJECTS})
		targets = [t["focus_skill"] for t in plan["targets"]]
		self.assertTrue(all(t.startswith(("Acme Microservices", "Chat App")) for t in targets), targets)
		self.assertEqual({t["question_tier"] for t in plan["targets"]}, {"project"})

	def test_a_resume_without_projects_still_gets_a_project_round(self) -> None:
		plan = build_round_coverage_plan("project_discussion", {"projects": []})
		self.assertGreaterEqual(len(plan["targets"]), 3)

	def test_a_single_project_is_split_into_several_angles(self) -> None:
		plan = build_round_coverage_plan("project_discussion", {"projects": _PROJECTS[:1]})
		self.assertEqual(len(plan["targets"]), 4)

	def test_the_turn_budget_fits_every_target(self) -> None:
		for round_type, context in (("hr", {}), ("project_discussion", {"projects": _PROJECTS})):
			plan = build_round_coverage_plan(round_type, context)
			self.assertGreaterEqual(plan["max_turns"], len(plan["targets"]))


class PromptTests(TestCase):
	def _example_json(self, prompt: str) -> dict:
		line = next(l for l in prompt.splitlines() if l.startswith('{"action"'))
		return json.loads(line)

	def test_hr_prompt_is_behavioural_and_forbids_technical_questions(self) -> None:
		prompt = build_director_system_prompt({"candidate_name": "Alex"}, round_type="hr")
		self.assertIn("behavioural", prompt)
		self.assertIn("Keep it non-technical", prompt)
		self.assertNotIn("Never ask HR-style behavioural questions", prompt)
		self.assertIn(META_SENTINEL, prompt)
		self.assertEqual(self._example_json(prompt)["question_tier"], "behavioural")

	def test_project_prompt_lists_the_candidates_projects(self) -> None:
		prompt = build_director_system_prompt({"projects": _PROJECTS}, round_type="project_discussion")
		self.assertIn("Acme Microservices", prompt)
		self.assertIn("40% faster", prompt)
		self.assertEqual(self._example_json(prompt)["question_tier"], "project")

	def test_technical_prompt_is_unchanged(self) -> None:
		prompt = build_director_system_prompt({}, round_type="technical")
		self.assertIn("Never ask HR-style behavioural questions", prompt)


class FallbackTests(TestCase):
	def test_hr_fallback_stays_behavioural(self) -> None:
		question = fallback_question(build_round_coverage_plan("hr", {}))
		self.assertTrue(is_non_technical_hr_question(question), question)
		self.assertNotIn("explain what", question.lower())

	def test_project_fallback_names_the_project(self) -> None:
		question = fallback_question(build_round_coverage_plan("project_discussion", {"projects": _PROJECTS}))
		self.assertIn("Acme Microservices", question)


class ProjectRoundShapeTests(TestCase):
	"""A conversational project round stores a flat question list; the
	scripted one groups questions by project. Both must be read correctly."""

	dynamic = {"mode": "dynamic", "questions": [{"question": "q1"}, {"question": "q2"}]}
	scripted = {"projects": [{"questions": [{"question": "a"}]}, {"questions": [{"question": "b"}, {"question": "c"}]}]}

	def test_counts(self) -> None:
		self.assertEqual(_total_question_count(self.dynamic, "project_discussion"), 2)
		self.assertEqual(_count_round_questions(self.dynamic, "project_discussion"), 2)
		self.assertEqual(_total_question_count(self.scripted, "project_discussion"), 3)
		self.assertEqual(_count_round_questions(self.scripted, "project_discussion"), 3)

	def test_lookup(self) -> None:
		self.assertEqual(_get_question_at_index(self.dynamic, "project_discussion", 1)["question"], "q2")
		self.assertEqual(_get_question_at_index(self.scripted, "project_discussion", 2)["question"], "c")


class TargetMatchingTests(TestCase):
	def test_a_name_echoed_with_its_tier_label_still_matches_the_plan(self) -> None:
		"""Seen live: the model returned "Motivation for this role (behavioural)",
		the target was never marked covered, and a phantom tier appeared."""

		from backend.nlp.coverage_director import mark_covered, resolve_turn_target, tier_progress

		plan = build_round_coverage_plan("hr", {})
		skill, tier = resolve_turn_target(
			plan, action="new_topic", focus_skill="Motivation for this role (behavioural)", question_tier="general"
		)
		self.assertEqual((skill, tier), ("Motivation for this role", "behavioural"))

		plan = mark_covered(plan, focus_skill=skill, question_tier=tier, consumes_target=True)
		self.assertEqual(tier_progress(plan), {"behavioural": {"covered": 1, "target": 6}})
		self.assertEqual(plan["round"], "hr")
