"""'Can you repeat that?' is a request about the question, not an answer.

The technical round used to score it and move on to the next topic; HR only
handled it because its model happened to.
"""

from __future__ import annotations

import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from backend.api.interview_engines import dynamic
from backend.nlp.meta_requests import classify_meta_request

from tests.test_dynamic_engine import _ControlRuntime


class ClassifierTests(TestCase):
	def test_repeat_requests(self) -> None:
		for text in (
			"Can you repeat the question?",
			"can u repeat again",
			"Sorry, could you say that again?",
			"Pardon?",
			"I didn’t catch that",
			"Could you ask it again please",
		):
			self.assertEqual(classify_meta_request(text), "repeat", msg=text)

	def test_clarify_requests(self) -> None:
		for text in ("Can you rephrase it?", "What do you mean by that?", "I did not understand the question"):
			self.assertEqual(classify_meta_request(text), "clarify", msg=text)

	def test_answers_are_left_alone(self) -> None:
		for text in (
			"I don't know",
			"I don't understand recursion fully",
			"A hash map gives constant time lookups",
			"I would repeat the request with exponential backoff and jitter until it succeeds or times out after a few tries",
		):
			self.assertIsNone(classify_meta_request(text), msg=text)


class _Runtime(_ControlRuntime):
	def __init__(self) -> None:
		super().__init__()
		self.round_type = "technical"
		self.parent_session = {"role_selected": "backend_developer"}
		self.clarification_count = 0
		self.questions_json = {
			"mode": "dynamic",
			"questions": [{"question": "What does the GIL protect?", "turn_action": "new_topic", "snippet": None}],
		}


class EngineTests(TestCase):
	def _run(self, runtime: _Runtime, text: str):
		spoken: list[str] = []

		async def fake_speak(_runtime, sentences):
			async for sentence in sentences:
				spoken.append(sentence)

		with patch.object(dynamic, "speak_stream", fake_speak), \
			 patch.object(dynamic, "_record_candidate_turn", AsyncMock()) as record, \
			 patch.object(dynamic, "_ask_next_turn", AsyncMock()) as ask_next, \
			 patch.object(dynamic, "_track_scoring") as scoring, \
			 patch.object(dynamic, "_run_scoring", AsyncMock()), \
			 patch.object(dynamic, "_generate_clarification_reply", return_value="I mean what the lock guards.") as clarify:
			asyncio.run(dynamic.handle_transcript(runtime, text))  # type: ignore[arg-type]
		return spoken, record, ask_next, scoring, clarify

	def test_repeat_says_the_same_question_again_without_scoring(self) -> None:
		runtime = _Runtime()
		spoken, record, ask_next, scoring, _ = self._run(runtime, "Can you repeat the question?")

		self.assertEqual(" ".join(spoken), "What does the GIL protect?")
		record.assert_not_called()
		scoring.assert_not_called()
		ask_next.assert_not_called()
		meta = [m for m in runtime.sent if m["type"] == "meta_request"]
		self.assertEqual(meta[0]["turn_index"], 0)
		ends = [m for m in runtime.sent if m["type"] == "turn_end"]
		self.assertEqual(ends[-1]["turn_index"], 0)

	def test_clarify_explains_the_question(self) -> None:
		spoken, record, _, _, clarify = self._run(_Runtime(), "What do you mean?")
		self.assertEqual(" ".join(spoken), "I mean what the lock guards.")
		clarify.assert_called_once()
		record.assert_not_called()

	def test_a_real_answer_is_scored_and_moves_on(self) -> None:
		_, record, ask_next, scoring, _ = self._run(_Runtime(), "It stops two threads running Python bytecode at once.")
		record.assert_called_once()
		scoring.assert_called_once()
		ask_next.assert_called_once()

	def test_repeats_are_capped_per_question(self) -> None:
		runtime = _Runtime()
		runtime.clarification_count = dynamic._MAX_CLARIFICATIONS_PER_QUESTION
		_, record, _, _, _ = self._run(runtime, "Can you repeat that?")
		record.assert_called_once()  # treated as the answer
