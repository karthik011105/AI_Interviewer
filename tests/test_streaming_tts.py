"""Tests for sentence-level prompt streaming over the interview websocket.

The client schedules each binary frame as an independent container, so the
contract these tests protect is: one `tts_start` per turn carrying the real
encoding, N standalone audio frames, one `tts_end` — and nothing sent after a
barge-in.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from backend.api import interview_runtime
from backend.voice.tts import TTSOutput


class _RecordingRuntime:
	"""Minimal stand-in for InterviewRuntime that records what was sent."""

	def __init__(self) -> None:
		self.state = "PLAYING"
		self.prompt_phase = "preparing"
		self.interrupt_event = asyncio.Event()
		self.tts_task = None
		self.user_id = "user-1"
		self.sent: list[dict] = []
		self.audio: list[bytes] = []

	async def send_json(self, payload: dict) -> None:
		self.sent.append(payload)

	async def send_bytes(self, payload: bytes) -> None:
		self.audio.append(payload)

	async def set_state(self, state: str) -> None:
		self.state = state
		self.sent.append({"type": "state_change", "state": state})

	def types(self) -> list[str]:
		return [item["type"] for item in self.sent]


async def _sentences(*items: str):
	for item in items:
		yield item


def _wav(marker: bytes) -> TTSOutput:
	return TTSOutput(audio=marker, encoding="wav", sample_rate=22050, provider="piper")


class SpeakStreamTests(TestCase):
	def _run(self, runtime, chunks, synth):
		async def _no_block(*_args, **_kwargs):
			return False

		with patch.object(interview_runtime, "_quota_blocked", _no_block), patch.object(
			interview_runtime, "synthesize_detailed", side_effect=synth
		):
			asyncio.run(interview_runtime.speak_stream(runtime, chunks))

	def test_each_sentence_is_sent_as_its_own_frame(self) -> None:
		runtime = _RecordingRuntime()
		self._run(
			runtime,
			_sentences("First sentence.", "Second sentence.", "Third sentence."),
			lambda text: _wav(text.encode()),
		)

		self.assertEqual(
			runtime.audio,
			[b"First sentence.", b"Second sentence.", b"Third sentence."],
		)
		# Exactly one start/end pair wraps the whole turn.
		self.assertEqual(runtime.types().count("tts_start"), 1)
		self.assertEqual(runtime.types().count("tts_end"), 1)

	def test_tts_start_reports_the_real_encoding(self) -> None:
		runtime = _RecordingRuntime()
		self._run(
			runtime,
			_sentences("Only one."),
			lambda _text: TTSOutput(audio=b"mp3", encoding="mp3", sample_rate=24000, provider="edge"),
		)

		start = next(item for item in runtime.sent if item["type"] == "tts_start")
		self.assertEqual(start["encoding"], "mp3")
		self.assertEqual(start["sample_rate"], 24000)
		self.assertEqual(start["provider"], "edge")

	def test_interrupt_stops_further_audio(self) -> None:
		runtime = _RecordingRuntime()

		def _synth(text: str) -> TTSOutput:
			# Barge in while the first sentence is being synthesized.
			if text == "Second.":
				runtime.interrupt_event.set()
			return _wav(text.encode())

		self._run(runtime, _sentences("First.", "Second.", "Third."), _synth)

		self.assertNotIn(b"Third.", runtime.audio)
		self.assertLessEqual(len(runtime.audio), 2)
		# tts_end is only meaningful if a tts_start was sent; the pairing is the
		# invariant the client depends on, not the presence of an end frame.
		self.assertEqual(
			runtime.types().count("tts_end"), runtime.types().count("tts_start")
		)

	def test_synthesis_failure_before_any_audio_reports_an_error(self) -> None:
		runtime = _RecordingRuntime()

		def _synth(_text: str) -> TTSOutput:
			raise interview_runtime.TTSSynthesisError("piper exploded")

		self._run(runtime, _sentences("First."), _synth)

		errors = [item for item in runtime.sent if item["type"] == "error"]
		self.assertEqual(len(errors), 1)
		self.assertEqual(errors[0]["code"], "tts_failed")
		self.assertNotIn("tts_start", runtime.types())

	def test_empty_audio_is_not_sent(self) -> None:
		runtime = _RecordingRuntime()
		self._run(runtime, _sentences("Silent."), lambda _t: _wav(b""))

		self.assertEqual(runtime.audio, [])
		self.assertNotIn("tts_start", runtime.types())

	def test_returns_to_listening_when_done(self) -> None:
		runtime = _RecordingRuntime()
		self._run(runtime, _sentences("Done."), lambda t: _wav(t.encode()))
		self.assertEqual(runtime.state, "LISTENING")


class PromptSentenceIterationTests(TestCase):
	def test_prompt_is_split_into_sentences(self) -> None:
		async def _collect() -> list[str]:
			text = (
				"Welcome. Let's begin. First question. How does dynamic memory "
				"allocation affect reliability in constrained embedded systems, "
				"and what would you use instead?"
			)
			return [chunk async for chunk in interview_runtime._iter_prompt_sentences(text)]

		chunks = asyncio.run(_collect())

		self.assertGreater(len(chunks), 1)
		# Nothing may be lost: the client hears exactly the prompt text.
		joined = " ".join(chunks)
		self.assertIn("Welcome.", joined)
		self.assertIn("what would you use instead?", joined)

	def test_single_short_prompt_yields_one_chunk(self) -> None:
		async def _collect() -> list[str]:
			return [chunk async for chunk in interview_runtime._iter_prompt_sentences("Hi?")]

		self.assertEqual(asyncio.run(_collect()), ["Hi?"])


class BargeInTests(TestCase):
	"""Interrupt handling on the shared runtime, independent of TTS timing."""

	def _runtime(self, *, state: str, prompt_phase: str) -> _RecordingRuntime:
		runtime = _RecordingRuntime()
		runtime.state = state
		runtime.prompt_phase = prompt_phase
		runtime.turn_commit_task = None
		runtime.pending_pcm_frames = []
		return runtime

	def test_speech_start_during_playback_acks_and_stops(self) -> None:
		runtime = self._runtime(state="PLAYING", prompt_phase="streaming")
		asyncio.run(interview_runtime._handle_speech_start(runtime))

		self.assertIn("interrupt_ack", runtime.types())
		self.assertTrue(runtime.interrupt_event.is_set())
		self.assertEqual(runtime.state, "LISTENING")

	def test_speech_start_during_clarification_also_acks(self) -> None:
		runtime = self._runtime(state="CLARIFYING", prompt_phase="streaming")
		asyncio.run(interview_runtime._handle_speech_start(runtime))
		self.assertIn("interrupt_ack", runtime.types())

	def test_speech_start_after_audio_finished_does_not_ack(self) -> None:
		# Nothing is playing, so there is nothing to interrupt. Sending an ack
		# here would tell the client to discard audio it should keep.
		runtime = self._runtime(state="LISTENING", prompt_phase="idle")
		asyncio.run(interview_runtime._handle_speech_start(runtime))

		self.assertNotIn("interrupt_ack", runtime.types())
		self.assertFalse(runtime.interrupt_event.is_set())

	def test_speech_start_is_ignored_while_scoring(self) -> None:
		runtime = self._runtime(state="EVALUATING", prompt_phase="idle")
		asyncio.run(interview_runtime._handle_speech_start(runtime))
		self.assertEqual(runtime.state, "EVALUATING")
		self.assertEqual(runtime.sent, [])

	def test_speech_end_without_audio_does_not_arm_a_commit(self) -> None:
		runtime = self._runtime(state="LISTENING", prompt_phase="idle")
		asyncio.run(interview_runtime._handle_speech_end(runtime))
		self.assertIsNone(runtime.turn_commit_task)
