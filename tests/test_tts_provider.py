from __future__ import annotations

import io
import os
import wave
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backend.voice import tts


class TTSProviderTests(TestCase):
	def test_elevenlabs_pcm_is_wrapped_as_wav_for_rest(self) -> None:
		pcm_bytes = b"\x01\x00\x02\x00" * 8

		with patch.object(tts, "_TTS_PROVIDER", "elevenlabs"), patch.object(
			tts,
			"_generate_tts_audio",
			return_value=tts.TTSAudioResult(
				provider="elevenlabs",
				sample_rate=22050,
				pcm_bytes=pcm_bytes,
			),
		):
			wav_bytes = tts.synthesize("Explain the approach.")

		with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
			self.assertEqual(reader.getframerate(), 22050)
			self.assertEqual(reader.getnchannels(), 1)
			self.assertEqual(reader.getsampwidth(), 2)
			self.assertEqual(reader.readframes(reader.getnframes()), pcm_bytes)

	def test_elevenlabs_failure_falls_back_to_piper_for_streaming_chunks(self) -> None:
		pcm_bytes = b"\x01\x00\x02\x00" * 16

		with patch.object(tts, "_TTS_PROVIDER", "elevenlabs"), patch.object(
			tts,
			"_synthesize_with_elevenlabs_audio",
			side_effect=tts.TTSSynthesisError("elevenlabs failed"),
		), patch.object(
			tts,
			"_synthesize_with_piper_audio",
			return_value=tts.TTSAudioResult(
				provider="piper",
				sample_rate=22050,
				pcm_bytes=pcm_bytes,
			),
		):
			sample_rate, chunks = tts.synthesize_chunks("Tell me about yourself.", chunk_ms=50)

		self.assertEqual(sample_rate, 22050)
		self.assertEqual(b"".join(chunks), pcm_bytes)

	def test_health_reports_piper_fallback_when_elevenlabs_is_not_configured(self) -> None:
		with patch.dict(
			os.environ,
			{
				"TTS_PROVIDER": "elevenlabs",
				"ELEVENLABS_API_KEY": "",
				"ELEVENLABS_VOICE_ID": "",
			},
			clear=False,
		), patch.object(tts, "_PIPER_EXE", "piper.exe"), patch.object(
			tts,
			"_resolve_model_path",
			return_value=(Path("voice.onnx"), Path("voice.onnx.json")),
		):
			health = tts.tts_health()

		self.assertEqual(health["status"], "ready")
		self.assertEqual(health["selected_provider"], "elevenlabs")
		self.assertEqual(health["active_provider"], "piper")
		self.assertIn("fallback_reason", health)