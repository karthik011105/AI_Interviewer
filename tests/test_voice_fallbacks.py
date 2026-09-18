"""Voice subsystem: provider fallback visibility, and VAD frame robustness.

Two findings drove these tests.

The TTS chain degrades silently. ``docker-compose.yml`` sets
``TTS_PROVIDER=piper``, but no stage of ``backend/Dockerfile`` installs the
piper binary, so every containerised synthesis fell through to edge-tts — and
the "provider unavailable" branch logged at DEBUG, which is below the
documented default ``LOG_LEVEL=INFO``. The operator's explicit configuration
was being ignored with no record of it. Worse, when every provider was
unavailable the chain returned ``None`` with no log at all, so "voice is
entirely dead" looked identical to "this turn had nothing to say".

``EnergyVAD`` raised ``ValueError`` on an odd-length PCM frame, because
``np.frombuffer`` with int16 requires a whole number of samples. Audio arrives
from a browser over a network, where a truncated frame is a normal consequence
of a flaky connection rather than a programming error.
"""

from __future__ import annotations

import io
import logging
import os
from unittest import TestCase
from unittest.mock import patch

from backend.voice import tts as tts_module
from backend.voice.vad import EnergyVAD


class _LogCapture:
    """Capture backend.voice.tts records at INFO, the documented default."""

    def __enter__(self) -> "_LogCapture":
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setLevel(logging.INFO)
        self.logger = logging.getLogger("backend.voice.tts")
        self._original_handlers = self.logger.handlers
        self._original_level = self.logger.level
        self._original_propagate = self.logger.propagate
        self.logger.handlers = [self.handler]
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.logger.handlers = self._original_handlers
        self.logger.setLevel(self._original_level)
        self.logger.propagate = self._original_propagate

    @property
    def text(self) -> str:
        return self.stream.getvalue()


class TtsFallbackVisibilityTests(TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {"TTS_PROVIDER": "piper"}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    def _unavailable(self, *_args: object, **_kwargs: object) -> None:
        raise tts_module.TTSUnavailableError("not configured in this test")

    def _working_edge(self, *_args: object, **_kwargs: object):
        return tts_module.TTSAudioResult(
            provider="edge", sample_rate=22050, pcm_bytes=b"\x00\x00"
        )

    def test_the_configured_provider_being_unavailable_is_a_warning(self) -> None:
        """This is the container's actual situation: piper configured, piper
        absent, edge quietly serving every request instead."""

        with patch.object(
            tts_module, "_synthesize_with_piper_audio", side_effect=self._unavailable
        ), patch.object(
            tts_module, "_synthesize_with_edge_audio", side_effect=self._working_edge
        ):
            with _LogCapture() as captured:
                result = tts_module._generate_tts_audio("Hello.")

        self.assertEqual(result.provider, "edge")
        self.assertIn("piper", captured.text)
        self.assertIn("unavailable", captured.text)
        # The log has to name the consequence, not just the fact.
        self.assertIn("configured for", captured.text)

    def test_every_provider_unavailable_is_reported_rather_than_a_silent_none(
        self,
    ) -> None:
        with patch.object(
            tts_module, "_synthesize_with_piper_audio", side_effect=self._unavailable
        ), patch.object(
            tts_module, "_synthesize_with_edge_audio", side_effect=self._unavailable
        ):
            with _LogCapture() as captured:
                result = tts_module._generate_tts_audio("Hello.")

        # Returning None is correct — the round continues without audio because
        # the question text is already on screen. It must not be silent.
        self.assertIsNone(result)
        self.assertIn("No TTS provider is available", captured.text)

    def test_a_working_configured_provider_logs_nothing(self) -> None:
        """The warning must not fire on the happy path, or it becomes noise
        that gets filtered out and stops being read."""

        with patch.object(
            tts_module,
            "_synthesize_with_piper_audio",
            side_effect=lambda *a, **k: tts_module.TTSAudioResult(
                provider="piper", sample_rate=22050, pcm_bytes=b"\x00\x00"
            ),
        ):
            with _LogCapture() as captured:
                result = tts_module._generate_tts_audio("Hello.")

        self.assertEqual(result.provider, "piper")
        self.assertEqual(captured.text.strip(), "")

    def test_a_fallback_provider_being_unavailable_stays_quiet(self) -> None:
        """Only the FIRST entry is the operator's choice. A later one being
        unconfigured is routine and must not warn."""

        with patch.dict(os.environ, {"TTS_PROVIDER": "edge"}, clear=False):
            with patch.object(
                tts_module,
                "_synthesize_with_edge_audio",
                side_effect=lambda *a, **k: tts_module.TTSAudioResult(
                    provider="edge", sample_rate=22050, pcm_bytes=b"\x00\x00"
                ),
            ):
                with _LogCapture() as captured:
                    tts_module._generate_tts_audio("Hello.")

        self.assertEqual(captured.text.strip(), "")


class TtsProviderOrderTests(TestCase):
    def test_piper_does_not_fall_back_to_a_metered_provider(self) -> None:
        """Choosing the local, zero-cost engine implicitly declines paid API
        calls, so ElevenLabs is deliberately absent from that chain. Pinned
        because the asymmetry reads like an oversight and could be 'fixed'."""

        with patch.dict(os.environ, {"TTS_PROVIDER": "piper"}, clear=False):
            order = tts_module._provider_order()

        self.assertEqual(order, ("piper", "edge"))
        self.assertNotIn("elevenlabs", order)

    def test_the_configured_provider_is_always_tried_first(self) -> None:
        for provider in ("piper", "edge", "elevenlabs"):
            with self.subTest(provider=provider):
                with patch.dict(
                    os.environ, {"TTS_PROVIDER": provider}, clear=False
                ):
                    self.assertEqual(tts_module._provider_order()[0], provider)


class EnergyVadFrameRobustnessTests(TestCase):
    """Audio arrives from a browser, so frame length is untrusted input."""

    def test_a_truncated_frame_does_not_raise(self) -> None:
        detector = EnergyVAD()
        odd_frame = (b"\x00\x10" * 160) + b"\x7f"

        event = detector.process(odd_frame)

        self.assertEqual(event.name, "silence")

    def test_a_single_stray_byte_does_not_raise(self) -> None:
        self.assertEqual(EnergyVAD().process(b"\x05").name, "silence")

    def test_an_empty_frame_is_silence(self) -> None:
        self.assertEqual(EnergyVAD().process(b"").name, "silence")

    def test_speech_is_still_detected_after_the_onset_window(self) -> None:
        """The robustness fix must not blunt detection itself."""

        detector = EnergyVAD(threshold=400.0, onset_frames=3)
        loud = b"\xff\x3f" * 160

        events = [detector.process(loud).name for _ in range(4)]

        self.assertEqual(events, ["silence", "silence", "speech_start", "speech"])

    def test_speech_end_fires_after_the_offset_window(self) -> None:
        detector = EnergyVAD(threshold=400.0, onset_frames=1, offset_frames=2)
        loud = b"\xff\x3f" * 160
        quiet = b"\x00\x00" * 160

        self.assertEqual(detector.process(loud).name, "speech_start")
        self.assertEqual(detector.process(quiet).name, "speech")
        self.assertEqual(detector.process(quiet).name, "speech_end")


if __name__ == "__main__":
    import unittest

    unittest.main()
