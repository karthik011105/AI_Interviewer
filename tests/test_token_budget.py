"""Groq's free tier caps tokens per model per day: spend them where they count.

The day's 200k on the main model ran out in testing - partly because every
role selection started a ~20k-token assessment build in the background - and
every interview question then fell back to a canned one.
"""

from __future__ import annotations

import asyncio
from unittest import TestCase
from unittest.mock import patch

from backend.api import routes_assessment
from backend.assessment import oa_generator
from backend.nlp.groq_client import GroqRateLimitError
from backend.nlp.interview_director import META_SENTINEL, DirectorTurnStream

_USER = routes_assessment.AuthenticatedUser(user_id="u1", email="c@example.com", raw_user={"id": "u1"})


def _collect(stream: DirectorTurnStream) -> list[str]:
	async def run() -> list[str]:
		return [sentence async for sentence in stream.sentences()]

	return asyncio.run(run())


class InterviewerFallbackModelTests(TestCase):
	def _stream(self, behaviour):
		async def fake_stream(*, settings, model, temperature, messages):
			for item in behaviour(model):
				if isinstance(item, Exception):
					raise item
				yield item

		patcher = patch("backend.nlp.interview_director.stream_chat_completion", fake_stream)
		patcher.start()
		self.addCleanup(patcher.stop)
		return DirectorTurnStream(settings=object(), model="main", messages=[], fallback_models=("alt",))

	def test_second_model_asks_the_question_when_the_first_is_out_of_budget(self) -> None:
		def behaviour(model):
			if model == "main":
				return [GroqRateLimitError("tokens per day (TPD): Limit 200000")]
			return ["What does the GIL protect? ", META_SENTINEL, '{"action":"new_topic"}']

		stream = self._stream(behaviour)
		spoken = " ".join(_collect(stream))
		self.assertIn("GIL", spoken)
		self.assertFalse(stream.fallback_used)

	def test_a_failure_mid_sentence_keeps_what_was_said(self) -> None:
		calls = []

		def behaviour(model):
			calls.append(model)
			return ["Tell me about indexing in Postgres and when it hurts. ", GroqRateLimitError("TPM")]

		spoken = " ".join(_collect(self._stream(behaviour)))
		self.assertIn("indexing", spoken)
		self.assertEqual(calls, ["main"])  # no second model talking over the first


class PrepareTests(TestCase):
	def test_prepare_does_nothing_once_the_attempt_exists(self) -> None:
		request = routes_assessment.AssessmentPrepareRequest(session_id="s1")
		with patch("backend.api.routes_assessment._require_parent_session", return_value={"role_selected": "java_developer"}), \
			 patch("backend.api.routes_assessment.fetch_assessment_session", return_value={"status": "in_progress"}), \
			 patch("backend.api.routes_assessment.prefetch") as prefetch:
			result = routes_assessment.prepare_assessment(request, _USER)
		prefetch.assert_not_called()
		self.assertFalse(result["preparing"])

	def test_prepare_starts_the_background_build(self) -> None:
		request = routes_assessment.AssessmentPrepareRequest(session_id="s1")
		with patch("backend.api.routes_assessment._require_parent_session", return_value={"role_selected": "java_developer"}), \
			 patch("backend.api.routes_assessment.fetch_assessment_session", return_value=None), \
			 patch("backend.api.routes_assessment.prefetch") as prefetch:
			self.assertTrue(routes_assessment.prepare_assessment(request, _USER)["preparing"])
		prefetch.assert_called_once()

	def test_preparing_twice_never_starts_a_second_build(self) -> None:
		oa_generator._PREFETCH.pop("s-dup", None)
		self.addCleanup(oa_generator._PREFETCH.pop, "s-dup", None)
		with patch("backend.assessment.oa_generator._prefetch_enabled", return_value=True), \
			 patch.object(oa_generator._BUILD_EXECUTOR, "submit") as submit:
			oa_generator.prefetch("s-dup", "java_developer")
			oa_generator.prefetch("s-dup", "java_developer")
		self.assertEqual(submit.call_count, 1)

	def test_choosing_a_role_no_longer_builds_an_assessment(self) -> None:
		from backend.api import routes_resume

		self.assertFalse(hasattr(routes_resume, "prefetch_assessment"))
