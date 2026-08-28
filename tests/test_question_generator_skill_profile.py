from __future__ import annotations

import json
from unittest import TestCase
from unittest.mock import patch

from backend.config import GroqSettings
from backend.nlp.question_generator import get_or_generate_questions


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