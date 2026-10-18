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

	def test_elevenlabs_failure_falls_back_to_piper(self) -> None:
		pcm_bytes = b"\x01\x00\x02\x00" * 16

		# The provider chain for "elevenlabs" is (elevenlabs, edge, piper), so
		# reaching piper requires edge to fail too. Before edge-tts was installed
		# it failed implicitly with TTSUnavailableError, which is why this test
		# previously passed while asserting a two-provider chain.
		with patch.object(tts, "_TTS_PROVIDER", "elevenlabs"), patch.object(
			tts,
			"_synthesize_with_elevenlabs_audio",
			side_effect=tts.TTSSynthesisError("elevenlabs failed"),
		), patch.object(
			tts,
			"_synthesize_with_edge_audio",
			side_effect=tts.TTSSynthesisError("edge failed"),
		), patch.object(
			tts,
			"_synthesize_with_piper_audio",
			return_value=tts.TTSAudioResult(
				provider="piper",
				sample_rate=22050,
				pcm_bytes=pcm_bytes,
			),
		):
			output = tts.synthesize_detailed("Tell me about yourself.")

		self.assertEqual(output.provider, "piper")
		self.assertEqual(output.sample_rate, 22050)
		# Piper returns PCM that we wrap as WAV. The websocket advertises this
		# value to the client, so a wrong label makes the audio undecodable.
		self.assertEqual(output.encoding, "wav")
		with wave.open(io.BytesIO(output.audio), "rb") as reader:
			self.assertEqual(reader.readframes(reader.getnframes()), pcm_bytes)

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
			# The module-level constants are frozen at import from the ambient
			# environment (including a real .env). Config resolution does
			# `os.environ.get(NAME) or _CONST`, so blanking the env var above is
			# NOT enough to disable ElevenLabs — the falsy empty string falls
			# through to the import-time value. Patch the constant directly so
			# this test behaves the same with or without a populated .env.
			tts,
			"_ELEVENLABS_API_KEY",
			"",
		), patch.object(
			tts,
			"_resolve_model_path",
			return_value=(Path("voice.onnx"), Path("voice.onnx.json")),
		):
			health = tts.tts_health()

		self.assertEqual(health["status"], "ready")
		self.assertEqual(health["selected_provider"], "elevenlabs")
		self.assertEqual(health["active_provider"], "piper")
		self.assertIn("fallback_reason", health)