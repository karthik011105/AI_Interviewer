"""Unit tests for the Phase 1 streaming primitives.

These cover the two pieces that sit directly in the speech path, where a bug
is heard by the candidate rather than logged: the sentence chunker (which must
never leak the metadata sentinel into synthesized audio) and the director
output parser (which must never dead-end a live interview).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from backend.nlp.groq_client import GroqCompletionError, GroqRateLimitError, stream_chat_completion
from backend.nlp.interview_director import (
	META_SENTINEL,
	parse_director_output,
	slugify_topic,
	split_meta,
)
from backend.nlp.sentence_chunker import SentenceAccumulator


def _feed_all(accumulator: SentenceAccumulator, deltas: list[str]) -> list[str]:
	chunks: list[str] = []
	for delta in deltas:
		chunks.extend(accumulator.feed(delta))
	remainder = accumulator.flush()
	if remainder:
		chunks.append(remainder)
	return chunks


class SentenceAccumulatorTests(TestCase):
	def test_emits_sentences_once_they_are_complete(self) -> None:
		acc = SentenceAccumulator(min_chars=10)
		chunks = _feed_all(
			acc,
			["Tell me about indexing. ", "Why would a B-tree beat a hash index here? "],
		)
		self.assertEqual(
			chunks,
			["Tell me about indexing.", "Why would a B-tree beat a hash index here?"],
		)

	def test_short_fragment_is_merged_rather_than_spoken_alone(self) -> None:
		# "Right." on its own would be a separate TTS call for one word.
		acc = SentenceAccumulator(min_chars=40)
		chunks = _feed_all(acc, ["Right. ", "So walk me through what happens on commit. "])
		self.assertEqual(chunks, ["Right. So walk me through what happens on commit."])

	def test_sentinel_split_across_deltas_never_leaks_into_speech(self) -> None:
		acc = SentenceAccumulator(min_chars=10)
		deltas = [
			"What does autograd do when you call backward? ",
			"<<<",
			"META",
			">>>",
			'{"action": "new_topic"}',
		]
		chunks = _feed_all(acc, deltas)

		joined = " ".join(chunks)
		self.assertNotIn("<", joined)
		self.assertNotIn("META", joined)
		self.assertNotIn("action", joined)
		self.assertEqual(chunks, ["What does autograd do when you call backward?"])
		self.assertTrue(acc.sentinel_reached)
		self.assertEqual(acc.tail, '{"action": "new_topic"}')

	def test_sentinel_arriving_in_one_delta_flushes_pending_prose(self) -> None:
		acc = SentenceAccumulator(min_chars=200)
		# Prose is shorter than min_chars, so it is still buffered when the
		# sentinel lands; it must be flushed rather than dropped.
		chunks = acc.feed("Short question?" + META_SENTINEL + "{}")
		self.assertEqual(chunks, ["Short question?"])
		self.assertEqual(acc.tail, "{}")

	def test_abbreviation_does_not_end_a_sentence(self) -> None:
		acc = SentenceAccumulator(min_chars=10)
		chunks = _feed_all(acc, ["Compare REST vs. gRPC for this service. "])
		self.assertEqual(chunks, ["Compare REST vs. gRPC for this service."])

	def test_decimal_and_dotted_identifier_are_not_split(self) -> None:
		acc = SentenceAccumulator(min_chars=5)
		chunks = _feed_all(acc, ["Node.js uses 3.14 as a constant here. "])
		self.assertEqual(chunks, ["Node.js uses 3.14 as a constant here."])

	def test_runaway_text_is_force_cut_at_the_cap(self) -> None:
		acc = SentenceAccumulator(min_chars=10, max_chars=60)
		words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi"
		chunks = _feed_all(acc, [words + " "])

		self.assertGreater(len(chunks), 1)
		for chunk in chunks:
			self.assertLessEqual(len(chunk), 60)
		self.assertEqual(" ".join(chunks).split(), words.split())

	def test_newline_is_a_hard_boundary(self) -> None:
		acc = SentenceAccumulator(min_chars=100)
		chunks = _feed_all(acc, ["First line\n", "second line\n"])
		self.assertEqual(chunks, ["First line", "second line"])

	def test_flush_returns_trailing_prose_without_punctuation(self) -> None:
		acc = SentenceAccumulator(min_chars=10)
		self.assertEqual(acc.feed("no terminator here"), [])
		self.assertEqual(acc.flush(), "no terminator here")
		self.assertIsNone(acc.flush())

	def test_no_text_is_lost_across_arbitrary_delta_boundaries(self) -> None:
		source = (
			"Let's start with memory. Why does dynamic allocation hurt a "
			"constrained device? Explain what fragmentation does over time."
		)
		acc = SentenceAccumulator(min_chars=20)
		chunks = _feed_all(acc, list(source))  # one character per delta
		self.assertEqual(" ".join(chunks).split(), source.split())


class DirectorOutputParserTests(TestCase):
	def test_parses_prose_and_metadata(self) -> None:
		raw = (
			"Walk me through what autograd does.\n"
			+ META_SENTINEL
			+ '\n{"action":"new_topic","focus_skill":"PyTorch","question_tier":"strong",'
			'"difficulty":"hard","topic_key":"pytorch-autograd","covers_target":true,'
			'"ideal_points":["builds a graph","reverse traversal"]}'
		)
		turn = parse_director_output(raw)

		self.assertEqual(turn["say"], "Walk me through what autograd does.")
		self.assertEqual(turn["action"], "new_topic")
		self.assertEqual(turn["focus_skill"], "PyTorch")
		self.assertEqual(turn["question_tier"], "strong")
		self.assertEqual(turn["difficulty"], "hard")
		self.assertEqual(turn["ideal_points"], ["builds a graph", "reverse traversal"])
		self.assertTrue(turn["covers_target"])

	def test_missing_sentinel_keeps_prose_and_uses_fallback(self) -> None:
		turn = parse_director_output(
			"So what actually happens on a cache miss?",
			fallback={"focus_skill": "Caching", "question_tier": "absent"},
		)
		self.assertEqual(turn["say"], "So what actually happens on a cache miss?")
		self.assertEqual(turn["focus_skill"], "Caching")
		self.assertEqual(turn["question_tier"], "absent")
		self.assertEqual(turn["action"], "new_topic")

	def test_malformed_metadata_falls_back_without_raising(self) -> None:
		raw = "Explain virtual memory." + META_SENTINEL + "{not valid json"
		turn = parse_director_output(raw, fallback={"focus_skill": "OS", "question_tier": "familiar"})

		self.assertEqual(turn["say"], "Explain virtual memory.")
		self.assertEqual(turn["focus_skill"], "OS")
		self.assertEqual(turn["question_tier"], "familiar")

	def test_follow_up_never_consumes_a_coverage_target(self) -> None:
		raw = (
			"You said B-tree. When would a hash index win?"
			+ META_SENTINEL
			+ '{"action":"follow_up","focus_skill":"Indexing","covers_target":true}'
		)
		turn = parse_director_output(raw)

		self.assertEqual(turn["action"], "follow_up")
		# The model claimed it covered a target; a follow-up cannot.
		self.assertFalse(turn["covers_target"])

	def test_out_of_range_enums_are_clamped(self) -> None:
		raw = (
			"Question?"
			+ META_SENTINEL
			+ '{"action":"interrogate","question_tier":"expert","difficulty":"impossible"}'
		)
		turn = parse_director_output(raw)

		self.assertEqual(turn["action"], "new_topic")
		self.assertEqual(turn["question_tier"], "general")
		self.assertEqual(turn["difficulty"], "medium")

	def test_fenced_metadata_is_tolerated(self) -> None:
		raw = "Question?" + META_SENTINEL + '```json\n{"action":"wrap_up"}\n```'
		self.assertEqual(parse_director_output(raw)["action"], "wrap_up")

	def test_split_meta_and_slugify(self) -> None:
		self.assertEqual(split_meta("hello" + META_SENTINEL + "{}"), ("hello", "{}"))
		self.assertEqual(split_meta("no marker"), ("no marker", ""))
		self.assertEqual(slugify_topic("  PyTorch / Autograd!  "), "pytorch-autograd")


class _FakeStream:
	def __init__(self, deltas: list[str], *, fail_after: int | None = None) -> None:
		self._deltas = deltas
		self._fail_after = fail_after

	def __aiter__(self):
		async def _gen():
			for index, delta in enumerate(self._deltas):
				if self._fail_after is not None and index >= self._fail_after:
					raise RuntimeError("connection dropped")
				yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=delta))])

		return _gen()


class StreamChatCompletionTests(TestCase):
	def setUp(self) -> None:
		self.settings = SimpleNamespace(
			api_key="k",
			api_base_url="https://example.invalid",
			timeout_seconds=5.0,
			max_retries=2,
			backoff_base_seconds=0.0,
		)

	async def _collect(self, **kwargs) -> list[str]:
		return [
			chunk
			async for chunk in stream_chat_completion(
				settings=self.settings,
				model="m",
				temperature=0.5,
				messages=[{"role": "user", "content": "hi"}],
				**kwargs,
			)
		]

	def test_yields_content_deltas(self) -> None:
		async def _create(**_kwargs):
			return _FakeStream(["Hel", "lo ", "there"])

		client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
		with patch("backend.nlp.groq_client.get_async_groq_client", return_value=client):
			self.assertEqual(asyncio.run(self._collect()), ["Hel", "lo ", "there"])

	def test_retries_before_first_token_then_succeeds(self) -> None:
		attempts = {"n": 0}

		async def _create(**_kwargs):
			attempts["n"] += 1
			if attempts["n"] == 1:
				raise RuntimeError("transient")
			return _FakeStream(["ok"])

		client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
		with patch("backend.nlp.groq_client.get_async_groq_client", return_value=client):
			self.assertEqual(asyncio.run(self._collect()), ["ok"])
		self.assertEqual(attempts["n"], 2)

	def test_mid_stream_failure_keeps_partial_output_and_does_not_replay(self) -> None:
		attempts = {"n": 0}

		async def _create(**_kwargs):
			attempts["n"] += 1
			return _FakeStream(["first ", "second "], fail_after=2)

		client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
		with patch("backend.nlp.groq_client.get_async_groq_client", return_value=client):
			chunks = asyncio.run(self._collect())

		# Replaying would make the interviewer repeat itself aloud mid-sentence.
		self.assertEqual(chunks, ["first ", "second "])
		self.assertEqual(attempts["n"], 1)

	def test_rate_limit_is_not_retried(self) -> None:
		attempts = {"n": 0}

		async def _create(**_kwargs):
			attempts["n"] += 1
			raise RuntimeError("429 rate_limit_exceeded")

		client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
		with patch("backend.nlp.groq_client.get_async_groq_client", return_value=client):
			with self.assertRaises(GroqRateLimitError):
				asyncio.run(self._collect())
		self.assertEqual(attempts["n"], 1)

	def test_exhausted_retries_raise_completion_error(self) -> None:
		async def _create(**_kwargs):
			raise RuntimeError("still broken")

		client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
		with patch("backend.nlp.groq_client.get_async_groq_client", return_value=client):
			with self.assertRaises(GroqCompletionError):
				asyncio.run(self._collect())
