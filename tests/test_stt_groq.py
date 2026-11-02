"""STT_PROVIDER=groq: hosted Whisper for hosts too small for a local model."""

from __future__ import annotations

import io
import math
import wave
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from backend.voice import stt, tts


def _wav(seconds: float, amplitude: int) -> bytes:
	buf = io.BytesIO()
	with wave.open(buf, "wb") as writer:
		writer.setnchannels(1)
		writer.setsampwidth(2)
		writer.setframerate(16000)
		frames = bytearray()
		for i in range(int(16000 * seconds)):
			value = int(amplitude * math.sin(2 * math.pi * 220 * i / 16000))
			frames += value.to_bytes(2, "little", signed=True)
		writer.writeframes(bytes(frames))
	return buf.getvalue()


class GroqSpeechToTextTests(TestCase):
	def setUp(self) -> None:
		self.client = MagicMock()
		for target, value in (
			("backend.voice.stt._PROVIDER", "groq"),
			("backend.voice.stt._groq_settings", lambda: SimpleNamespace(api_key="k", api_base_url="u")),
			("backend.nlp.groq_client.get_groq_client", lambda *_: self.client),
		):
			patcher = patch(target, value)
			patcher.start()
			self.addCleanup(patcher.stop)

	def _reply(self, *segments):
		self.client.audio.transcriptions.create.return_value = SimpleNamespace(
			text=" ".join(text for text, _ in segments),
			duration=2.0,
			segments=[{"text": text, "no_speech_prob": prob} for text, prob in segments],
		)

	def test_speech_is_transcribed(self) -> None:
		self._reply(("I would add an index.", 0.01))
		result = stt.transcribe_audio(_wav(1.0, 8000))
		self.assertEqual(result.text, "I would add an index.")
		self.assertFalse(result.no_speech)
		_, kwargs = self.client.audio.transcriptions.create.call_args
		self.assertEqual(kwargs["file"][0], "answer.wav")

	def test_silence_never_reaches_groq(self) -> None:
		result = stt.transcribe_audio(_wav(2.0, 0))
		self.assertTrue(result.no_speech)
		self.client.audio.transcriptions.create.assert_not_called()

	def test_whispers_silence_phrase_is_dropped(self) -> None:
		self._reply(("Thank you.", 0.2))
		self.assertTrue(stt.transcribe_audio(_wav(1.0, 8000)).no_speech)

	def test_no_speech_segments_are_dropped(self) -> None:
		self._reply(("Hashing spreads keys evenly.", 0.05), ("Bye.", 0.9))
		self.assertEqual(stt.transcribe_audio(_wav(1.0, 8000)).text, "Hashing spreads keys evenly.")

	def test_health_needs_no_local_model(self) -> None:
		self.assertEqual(stt.stt_health()["status"], "ready")
		self.assertTrue(stt.warmup_stt())


class EdgeVoiceHealthTests(TestCase):
	def test_edge_voice_reports_ready(self) -> None:
		with patch("backend.voice.tts._current_tts_provider", return_value="edge"):
			health = tts.tts_health()
		self.assertEqual(health["status"], "ready")
		self.assertEqual(health["active_provider"], "edge")
