from __future__ import annotations

import json
from unittest import TestCase
from unittest.mock import patch

from backend.config import GroqSettings
from backend.nlp.question_generator import (
	generate_hr_questions,
	get_or_generate_questions,
	is_non_technical_hr_question,
)


class HrQuestionGeneratorTests(TestCase):
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
			"candidate_name": "Karthik",
			"summary": "Built Python and FastAPI projects, deployed APIs, and worked on SQL-backed apps during internships.",
			"interests": ["mentoring juniors", "machine learning"],
			"selected_role_key": "software_engineer",
			"selected_role_title": "Software Engineer",
		}

	def test_generate_hr_questions_filters_technical_output_and_uses_non_technical_prompting(self) -> None:
		captured_messages: dict[str, object] = {}
		payload = [
			{
				"question": "Explain how FastAPI dependency injection works in your backend project.",
				"ideal_points": ["Depends", "request lifecycle", "shared resources"],
				"follow_up": "Why is that useful in production APIs?",
				"difficulty": "medium",
			},
			{
				"question": "Tell me about a time you had to adapt quickly when expectations changed.",
				"ideal_points": ["context", "adaptation", "result"],
				"follow_up": "What did you learn from that experience?",
				"difficulty": "medium",
			},
			{
				"question": "How do you respond when you receive constructive feedback?",
				"ideal_points": ["openness", "action", "growth"],
				"follow_up": "Can you share one example?",
				"difficulty": "easy",
			},
			{
				"question": "Why are you interested in this role at this stage of your career?",
				"ideal_points": ["motivation", "alignment", "growth"],
				"follow_up": "What would you want to learn first?",
				"difficulty": "easy",
			},
			{
				"question": "Describe a time you worked with others to deliver something important.",
				"ideal_points": ["teamwork", "ownership", "outcome"],
				"follow_up": "What role did you naturally take?",
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
			result = generate_hr_questions(self.context, settings=self.settings, count=5)

		system_message = str(captured_messages["messages"][0]["content"])
		self.assertIn("Questions must stay non-technical and HR-style", system_message)
		self.assertIn("Do not ask about programming languages, frameworks, APIs, databases", system_message)
		self.assertEqual(len(result), 5)
		self.assertTrue(all(is_non_technical_hr_question(item["question"]) for item in result))
		self.assertFalse(any("FastAPI" in item["question"] for item in result))
		self.assertTrue(any("first year" in item["question"].casefold() for item in result))

	def test_get_or_generate_questions_regenerates_invalid_cached_hr_batch(self) -> None:
		bad_cache = {
			"questions": [
				{
					"question": "What is the difference between SQL and NoSQL databases?",
					"ideal_points": ["structure", "scalability", "trade-offs"],
					"follow_up": "When would you choose each?",
					"difficulty": "medium",
				}
			],
			"generated": False,
		}
		fresh_payload = [
			{
				"question": "What attracted you to this role at this stage of your career?",
				"ideal_points": ["motivation", "fit", "growth"],
				"follow_up": "What would you want to learn first?",
				"difficulty": "easy",
			},
			{
				"question": "How do you respond when you receive constructive feedback?",
				"ideal_points": ["openness", "reflection", "improvement"],
				"follow_up": "Can you share one example?",
				"difficulty": "easy",
			},
			{
				"question": "Tell me about a time you had to adapt quickly when expectations changed.",
				"ideal_points": ["context", "adaptation", "result"],
				"follow_up": "What did you learn from that experience?",
				"difficulty": "medium",
			},
			{
				"question": "Describe a time you worked with others to complete something important.",
				"ideal_points": ["teamwork", "role", "outcome"],
				"follow_up": "How did you handle differences in opinion?",
				"difficulty": "medium",
			},
			{
				"question": "When you have multiple deadlines, how do you decide what to do first?",
				"ideal_points": ["prioritization", "communication", "trade-offs"],
				"follow_up": "How do you signal risk early?",
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

		with patch(
			"backend.nlp.question_generator.create_chat_completion",
			return_value=FakeCompletion(json.dumps(fresh_payload)),
		) as mock_completion:
			result = get_or_generate_questions(
				"hr",
				self.context,
				settings=self.settings,
				cached_questions_json=bad_cache,
			)

		self.assertTrue(result["generated"])
		self.assertTrue(mock_completion.called)
		self.assertEqual(len(result["questions"]), 5)
		self.assertTrue(all(is_non_technical_hr_question(item["question"]) for item in result["questions"]))