"""Lightweight energy-based voice activity detection for streaming PCM audio."""

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

		samples = np.frombuffer(pcm_bytes, dtype=np.int16)
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