"""Lightweight energy-based voice activity detection for streaming PCM audio.

STATUS: currently unused. Nothing in the application imports ``EnergyVAD`` —
verified by a repository-wide search including the tests. It was superseded by
client-side detection: the browser runs Silero VAD and sends each *whole
utterance* as one binary frame, with turn boundaries arriving as explicit
``speech_start`` / ``speech_end`` control messages. See the docstring on
``_handle_audio_frame`` in ``backend/api/interview_runtime.py``, which says
outright that there is nothing for the server to detect.

It is kept rather than deleted because a server-side VAD is the natural
fallback if a client ever cannot run Silero, and the module is small and
self-contained. Treat it as unproven against live audio: it has no callers, so
it has never processed a real stream.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class SpeechEvent:
	name: str
	is_speech: bool


class EnergyVAD:
	"""Detect speech onset/offset from 16-bit PCM frames using RMS energy."""

	def __init__(
		self,
		*,
		threshold: float = 400.0,
		onset_frames: int = 3,
		offset_frames: int = 15,
	) -> None:
		self.threshold = float(threshold)
		self.onset_frames = max(1, int(onset_frames))
		self.offset_frames = max(1, int(offset_frames))
		self.reset()

	def reset(self) -> None:
		self._above_threshold_frames = 0
		self._below_threshold_frames = 0
		self._in_speech = False

	def process(self, pcm_bytes: bytes) -> SpeechEvent:
		"""Classify a PCM frame as silence, speech start, speech, or speech end."""
		if not pcm_bytes:
			return SpeechEvent(name="silence", is_speech=False)

		# np.frombuffer with int16 raises ValueError on an odd byte count, and
		# PCM frames arrive from a browser over a network — a truncated frame is
		# a normal consequence of a flaky connection, not a programming error.
		# A trailing half-sample carries no information, so drop it rather than
		# letting a malformed frame raise into the caller.
		usable_length = len(pcm_bytes) - (len(pcm_bytes) % 2)
		if usable_length == 0:
			return SpeechEvent(name="silence", is_speech=False)

		samples = np.frombuffer(pcm_bytes[:usable_length], dtype=np.int16)
		if samples.size == 0:
			return SpeechEvent(name="silence", is_speech=False)

		rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
		frame_has_speech = rms >= self.threshold

		if frame_has_speech:
			self._above_threshold_frames += 1
			self._below_threshold_frames = 0
			if not self._in_speech and self._above_threshold_frames >= self.onset_frames:
				self._in_speech = True
				return SpeechEvent(name="speech_start", is_speech=True)
			if self._in_speech:
				return SpeechEvent(name="speech", is_speech=True)
			return SpeechEvent(name="silence", is_speech=False)

		self._above_threshold_frames = 0
		self._below_threshold_frames += 1
		if self._in_speech and self._below_threshold_frames >= self.offset_frames:
			self._in_speech = False
			self._below_threshold_frames = 0
			return SpeechEvent(name="speech_end", is_speech=False)
		if self._in_speech:
			return SpeechEvent(name="speech", is_speech=True)
		return SpeechEvent(name="silence", is_speech=False)


__all__ = ["EnergyVAD", "SpeechEvent"]