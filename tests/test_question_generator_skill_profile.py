from __future__ import annotations

import json
from unittest import TestCase
from unittest.mock import patch

from backend.config import GroqSettings
from backend.nlp.question_generator import (
	_build_skill_allocation_plan,
	_resolve_skill_origin,
	_resolve_skill_tier,
	get_or_generate_questions,
)


class QuestionGeneratorSkillProfileTests(TestCase):
	def setUp(self) -> None:
		self.settings = GroqSettings(
			api_key="test-key",
			api_base_url="https://example.invalid",
			resume_parser_model="llama-test",
			answer_evaluator_model="llama-test",
			feedback_generator_model="llama-test",
			timeout_seconds=30.0,
			max_retries=1,
			backoff_base_seconds=0.2,
		)
		self.context = {
			"skills": ["Python", "FastAPI", "SQL"],
			"technologies": ["Supabase"],
			"selected_role_key": "backend_python_developer",
			"selected_role_title": "Backend Python Developer",
			"skill_gaps": ["git", "postgresql"],
			"skill_profile": {
				"skill_scores": {
					"Python": {"tier": "strong"},
					"FastAPI": {"tier": "strong"},
					"SQL": {"tier": "familiar"},
					"git": {"tier": "absent"},
					"postgresql": {"tier": "absent"},
				},
			},
			"strong_skills": ["Python", "FastAPI"],
			"familiar_skills": ["SQL"],
			"mentioned_skills": [],
			"absent_skills": ["git", "postgresql"],
			"soft_gap_skills": ["postgresql"],
			"priority_focus_areas": ["git", "postgresql", "SQL", "Python", "FastAPI"],
		}

	def test_get_or_generate_questions_includes_skill_allocation_metadata(self) -> None:
		captured_messages: dict[str, object] = {}
		payload = [
			{
				"question": "Explain how Python dependency management affects backend reliability.",
				"ideal_points": ["virtual environments", "reproducibility", "dependency pinning"],
				"follow_up": "Why can transitive dependencies still cause issues?",
				"difficulty": "medium",
			},
			{
				"question": "Describe how FastAPI dependency injection works in request handling.",
				"ideal_points": ["Depends", "request lifecycle", "testability"],
				"follow_up": "Why does that pattern help reuse shared resources?",
				"difficulty": "medium",
			},
			{
				"question": "Explain when SQL indexing improves query performance and when it can hurt writes.",
				"ideal_points": ["read trade-offs", "write overhead", "selectivity"],
				"follow_up": "How would you reason about index choice?",
				"difficulty": "medium",
			},
			{
				"question": "What role does Git play in safe backend collaboration and release history?",
				"ideal_points": ["branching", "history", "rollback"],
				"follow_up": "Why do small commits help debugging?",
				"difficulty": "easy",
			},
			{
				"question": "Explain why PostgreSQL transactions matter for API data consistency.",
				"ideal_points": ["atomicity", "consistency", "rollback"],
				"follow_up": "What problem appears without transaction boundaries?",
				"difficulty": "medium",
			},
			{
				"question": "Describe how authentication state should be protected in a backend API.",
				"ideal_points": ["tokens or sessions", "validation", "least privilege"],
				"follow_up": "Why is token expiry important?",
				"difficulty": "medium",
			},
		]

		class FakeMessage:
			def __init__(self, content: str) -> None:
				self.content = content

		class FakeChoice:
			def __init__(self, content: str) -> None:
				self.message = FakeMessage(content)

		class FakeCompletion:
			def __init__(self, content: str) -> None:
				self.choices = [FakeChoice(content)]

		def fake_chat_completion(*, settings, model, temperature, messages):
			captured_messages["messages"] = messages
			return FakeCompletion(json.dumps(payload))

		with patch("backend.nlp.question_generator.create_chat_completion", side_effect=fake_chat_completion):
			result = get_or_generate_questions(
				"technical",
				self.context,
				settings=self.settings,
				difficulty_signal=0.5,
			)

		user_message = str(captured_messages["messages"][1]["content"])
		system_message = str(captured_messages["messages"][0]["content"])
		self.assertIn("Strong skills    : Python, FastAPI", user_message)
		self.assertIn("question_tier=strong | focus_skill=Python | guidance=verify depth with mechanisms, trade-offs, or failure modes", user_message)
		self.assertIn("Absent-tier questions must stay fundamentals-first", system_message)
		self.assertTrue(result["skill_allocation_used"])
		self.assertEqual(len(result["questions"]), 6)
		self.assertEqual(result["questions"][0]["question_tier"], "strong")
		self.assertEqual(result["questions"][0]["focus_skill"], "Python")
		self.assertEqual(result["questions"][2]["question_tier"], "familiar")
		self.assertEqual(result["questions"][2]["focus_skill"], "SQL")
		self.assertEqual(result["questions"][3]["question_tier"], "absent")
		self.assertEqual(result["questions"][3]["focus_skill"], "git")
		self.assertGreaterEqual(len(result["skill_allocation_plan"]), 5)


class SkillAllocationOriginSplitTests(TestCase):
	"""A candidate tailors their resume to the role they apply for, so the role
	leads: the allocation plan should hold roughly a 70/30 role-to-resume split,
	with role-subject skills the resume evidences (higher tiers) spent first.
	"""

	def test_allocation_plan_splits_roughly_70_30_role_to_resume(self) -> None:
		role_skills = [f"role_skill_{i}" for i in range(1, 8)]  # 4 strong, 2 familiar, 1 absent
		resume_skills = [f"resume_skill_{i}" for i in range(1, 5)]  # all strong, role-unrelated

		skill_scores = {
			**{skill: {"tier": "strong", "origin": "role"} for skill in role_skills[:4]},
			**{skill: {"tier": "familiar", "origin": "role"} for skill in role_skills[4:6]},
			role_skills[6]: {"tier": "absent", "origin": "role"},
			**{skill: {"tier": "strong", "origin": "resume"} for skill in resume_skills},
		}
		context = {
			"skill_profile": {"skill_scores": skill_scores},
			"strong_skills": [*role_skills[:4], *resume_skills],
			"familiar_skills": role_skills[4:6],
			"mentioned_skills": [],
			"absent_skills": [role_skills[6:7][0]],
			"soft_gap_skills": [],
			"priority_focus_areas": [],
		}

		plan = _build_skill_allocation_plan(context, 10)

		role_count = sum(1 for slot in plan if skill_scores[slot["focus_skill"]]["origin"] == "role")
		resume_count = sum(1 for slot in plan if skill_scores[slot["focus_skill"]]["origin"] == "resume")

		self.assertEqual(len(plan), 10)
		self.assertEqual(role_count, 7)
		self.assertEqual(resume_count, 3)
		# Evidenced role skills (strong/familiar) are spent before the role's
		# one absent-tier skill, so depth is prioritized over raw coverage.
		absent_index = next(i for i, slot in enumerate(plan) if slot["question_tier"] == "absent")
		self.assertTrue(all(plan[i]["question_tier"] != "absent" for i in range(absent_index)))

class SkillScoreLookupTests(TestCase):
	"""The origin and tier lookups must tolerate case and whitespace.

	Both used ``skill_scores.get(skill)`` — an exact dict lookup — while every
	other comparison in question_generator casefolds. The mismatch is
	reachable, not theoretical: the profiler writes ``skill_scores[skill]`` and
	appends the same ``skill`` to its tier lists, but
	``_normalise_string_list`` strips those lists on the way into the
	allocator. A skill name arriving from the LLM with a stray leading space
	is therefore stored under one key and looked up under another.

	A miss was not loud. ``_resolve_skill_origin`` had no fallback and returned
	"role", which quietly collapses the 70/30 role-to-resume split toward 100%
	role — losing the exact guarantee INTERVIEW_QUESTION_TARGETING.md exists to
	provide, with no error anywhere.
	"""

	def _context(self, stored_key: str) -> dict:
		return {
			"skill_profile": {
				"skill_scores": {stored_key: {"tier": "strong", "origin": "resume"}}
			}
		}

	def test_origin_and_tier_survive_key_whitespace_and_case(self) -> None:
		variants = [
			("exact", "Verilog", "Verilog"),
			("stored padded", " Verilog ", "Verilog"),
			("stored lowercase", "verilog", "Verilog"),
			("queried padded", "Verilog", "  Verilog  "),
			("both differ", " verilog ", "VERILOG"),
		]
		for label, stored, queried in variants:
			with self.subTest(case=label):
				context = self._context(stored)
				self.assertEqual(_resolve_skill_origin(context, queried), "resume")
				self.assertEqual(_resolve_skill_tier(context, queried), "strong")

	def test_a_genuinely_absent_skill_still_defaults_to_role(self) -> None:
		"""The permissive default is deliberate — an untagged profile must
		degrade to tier-only allocation rather than starve the role bucket. The
		fix must not turn a real miss into a match."""

		context = self._context("Verilog")

		self.assertEqual(_resolve_skill_origin(context, "Kubernetes"), "role")
		self.assertEqual(_resolve_skill_tier(context, "Kubernetes"), "general")

	def test_an_empty_skill_name_does_not_match_anything(self) -> None:
		"""Otherwise a blank focus_skill would fuzzy-match the first entry."""

		context = self._context("Verilog")

		self.assertEqual(_resolve_skill_origin(context, ""), "role")
		self.assertEqual(_resolve_skill_origin(context, "   "), "role")

	def test_a_malformed_profile_is_tolerated(self) -> None:
		for label, profile in (
			("no profile", {}),
			("profile is not a mapping", {"skill_profile": "nope"}),
			("scores is not a mapping", {"skill_profile": {"skill_scores": []}}),
			("entry is not a mapping", {"skill_profile": {"skill_scores": {"X": "nope"}}}),
		):
			with self.subTest(case=label):
				self.assertEqual(_resolve_skill_origin(profile, "X"), "role")

	def test_the_split_holds_when_keys_are_padded(self) -> None:
		"""End to end: with padded keys the allocator used to see every skill as
		role-origin. Ten slots against six role and six resume skills gives 6/4,
		because the role bucket runs dry at six and the leftover budget is spent
		from whichever bucket still has candidates."""

		role_skills = [f"role_skill_{index}" for index in range(6)]
		resume_skills = [f"resume_skill_{index}" for index in range(6)]
		skill_scores = {}
		for skill in role_skills:
			skill_scores[f" {skill} "] = {"tier": "strong", "origin": "role"}
		for skill in resume_skills:
			skill_scores[f" {skill} "] = {"tier": "strong", "origin": "resume"}

		context = {
			"skill_profile": {"skill_scores": skill_scores},
			"strong_skills": [f" {skill} " for skill in role_skills + resume_skills],
			"familiar_skills": [],
			"mentioned_skills": [],
			"absent_skills": [],
			"soft_gap_skills": [],
			"priority_focus_areas": [],
		}

		plan = _build_skill_allocation_plan(context, 10)
		resume_count = sum(
			1
			for slot in plan
			if _resolve_skill_origin(context, slot["focus_skill"]) == "resume"
		)

		self.assertEqual(len(plan), 10)
		# The point of the test: resume_count was 0 before the fix.
		self.assertEqual(resume_count, 4)
