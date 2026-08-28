"""Speech-to-text using faster-whisper English models, int8 CPU, VAD enabled.

Public API
----------
transcribe_audio(audio_bytes, *, sample_rate=16000) -> TranscribeResult
    Accepts raw 16-bit PCM bytes or WebM/WAV/OGG blob; returns a TranscribeResult.

TranscribeResult fields
-----------------------
text          : str   — cleaned transcript (empty string when no speech detected)
no_speech     : bool  — True when VAD found no voiced segment
duration_sec  : float — wall-clock time of the audio blob
latency_sec   : float — how long transcription took
"""

from __future__ import annotations

import io
import os
import time
import wave
from array import array
from dataclasses import dataclass, field
from typing import ClassVar


class STTUnavailableError(RuntimeError):
    """Raised when faster-whisper is not installed or the model fails to load."""


class STTTranscriptionError(RuntimeError):
    """Raised when transcription itself fails."""


@dataclass
class TranscribeResult:
    text: str
    no_speech: bool
    duration_sec: float
    latency_sec: float


# ---------------------------------------------------------------------------
# Internal model cache — one worker, one model load
# ---------------------------------------------------------------------------

_MODEL_SIZE: str = os.environ.get("WHISPER_MODEL_SIZE", "base.en")
_COMPUTE_TYPE: str = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
_DEVICE: str = "cpu"
_NO_SPEECH_THRESHOLD: float = float(os.environ.get("WHISPER_NO_SPEECH_THRESHOLD", "0.6"))
_BEAM_SIZE: int = max(1, int(os.environ.get("WHISPER_BEAM_SIZE", "5")))
_BEST_OF: int = max(1, int(os.environ.get("WHISPER_BEST_OF", "5")))
_PATIENCE: float = max(0.1, float(os.environ.get("WHISPER_PATIENCE", "1.0")))
_TEMPERATURE: float = max(0.0, float(os.environ.get("WHISPER_TEMPERATURE", "0.0")))
_VAD_THRESHOLD: float = float(os.environ.get("WHISPER_VAD_THRESHOLD", "0.40"))
_VAD_MIN_SPEECH_MS: int = max(50, int(os.environ.get("WHISPER_VAD_MIN_SPEECH_MS", "200")))
_ENABLE_PREPROCESSING: bool = (
    os.environ.get("WHISPER_ENABLE_PREPROCESSING", "false").strip().casefold()
    in {"1", "true", "yes", "on"}
)
_INITIAL_PROMPT: str = os.environ.get("WHISPER_INITIAL_PROMPT", "").strip()
_HOTWORDS: str = os.environ.get("WHISPER_HOTWORDS", "").strip()

_model = None  # lazy-loaded
_model_error: str | None = None
_active_model_size: str | None = None


def _model_candidates() -> tuple[str, ...]:
    raw_candidates = (os.environ.get("WHISPER_MODEL_CANDIDATES") or "").strip()
    if raw_candidates:
        candidates = [item.strip() for item in raw_candidates.split(",") if item.strip()]
    else:
        candidates = [_MODEL_SIZE]

    ordered_unique: list[str] = []
    for candidate in candidates:
        if candidate not in ordered_unique:
            ordered_unique.append(candidate)
    return tuple(ordered_unique)


_MODEL_CANDIDATES: tuple[str, ...] = _model_candidates()


def _get_model():
    """Return the cached WhisperModel, loading it on first call."""
    global _model, _model_error, _active_model_size

    if _model is not None:
        return _model

    if _model_error is not None:
        raise STTUnavailableError(_model_error)

    try:
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]
    except ImportError as exc:
        _model_error = (
            "faster-whisper is not installed. "
            "Run: pip install faster-whisper"
        )
        raise STTUnavailableError(_model_error) from exc

    load_errors: list[str] = []
    for candidate in _MODEL_CANDIDATES:
        try:
            _model = WhisperModel(
                candidate,
                device=_DEVICE,
                compute_type=_COMPUTE_TYPE,
            )
            _active_model_size = candidate
            return _model
        except Exception as exc:
            load_errors.append(f"{candidate}: {exc}")

    _model_error = "Failed to load Whisper model(s): " + "; ".join(load_errors)
    raise STTUnavailableError(_model_error)


def _prepare_audio_array(data):
	import numpy as np

	audio = np.asarray(data, dtype=np.float32)
	if audio.ndim > 1:
		audio = audio.mean(axis=1)

	if audio.size == 0:
		return audio

	dc_offset = float(audio.mean())
	if abs(dc_offset) > 1e-4:
		audio = audio - dc_offset

	peak = float(np.max(np.abs(audio)))
	if peak <= 0:
		return audio

	if peak < 0.35:
		gain = min(4.0, 0.85 / peak)
		audio = np.clip(audio * gain, -1.0, 1.0)

	return audio


def _normalize_pcm16_wav_bytes(audio_bytes: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
            if reader.getsampwidth() != 2:
                return audio_bytes

            channels = max(1, reader.getnchannels())
            sample_rate = max(1, reader.getframerate())
            frame_bytes = reader.readframes(reader.getnframes())
    except Exception:
        return audio_bytes

    if not frame_bytes:
        return audio_bytes

    samples = array("h")
    samples.frombytes(frame_bytes)
    if not samples:
        return audio_bytes

    if channels > 1:
        mono_samples = array("h")
        for index in range(0, len(samples), channels):
            frame = samples[index:index + channels]
            if not frame:
                continue
            mono_samples.append(int(sum(frame) / len(frame)))
        samples = mono_samples

    mean_value = sum(samples) / len(samples)
    centered = [int(sample - mean_value) for sample in samples]
    peak = max((abs(sample) for sample in centered), default=0)
    if peak <= 0:
        return audio_bytes

    gain = min(4.0, 27852 / peak) if peak < 11468 else 1.0
    normalized = array(
        "h",
        [
            max(-32768, min(32767, int(sample * gain)))
            for sample in centered
        ],
    )

    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(normalized.tobytes())
    return out.getvalue()


# ---------------------------------------------------------------------------
# Audio conversion helper
# ---------------------------------------------------------------------------

def _to_wav_bytes(audio_bytes: bytes) -> bytes:
    """
    Convert incoming audio (WebM, OGG, WAV, etc.) to 16 kHz mono WAV bytes.
    Falls back to pydub → ffmpeg if soundfile cannot handle the format.
    Returns the original bytes if conversion is not possible (faster-whisper
    will attempt to decode itself).
    """
    try:
        import soundfile as sf  # type: ignore[import-untyped]

        with io.BytesIO(audio_bytes) as buf:
            data, sr = sf.read(buf, always_2d=False)

        if _ENABLE_PREPROCESSING:
            data = _prepare_audio_array(data)
        target_sr = int(sr)

        # Resample to 16 kHz if needed
        if sr != 16000:
            try:
                import resampy  # type: ignore[import-untyped]
                data = resampy.resample(data, sr, 16000)
                target_sr = 16000
            except ImportError:
                target_sr = int(sr)

        # Write back as WAV
        out = io.BytesIO()
        sf.write(out, data, target_sr, format="WAV", subtype="PCM_16")
        return out.getvalue()
    except Exception:
        if _ENABLE_PREPROCESSING:
            normalized_wav = _normalize_pcm16_wav_bytes(audio_bytes)
            if normalized_wav != audio_bytes:
                return normalized_wav
        # Let faster-whisper try to decode the original bytes directly
        return audio_bytes


# ---------------------------------------------------------------------------
# Public transcription entry point
# ---------------------------------------------------------------------------

def transcribe_audio(
    audio_bytes: bytes,
    *,
    sample_rate: int = 16000,
) -> TranscribeResult:
    """Transcribe audio bytes using faster-whisper with VAD filtering.

    Parameters
    ----------
    audio_bytes:
        Raw audio blob (WebM from MediaRecorder, WAV, OGG, etc.).
    sample_rate:
        Hint for the raw PCM case; ignored when the container already
        embeds sample rate metadata.

    Returns
    -------
    TranscribeResult
    """
    if not audio_bytes:
        return TranscribeResult(
            text="", no_speech=True, duration_sec=0.0, latency_sec=0.0
        )

    model = _get_model()  # raises STTUnavailableError if unavailable

    wav_bytes = _to_wav_bytes(audio_bytes)

    t0 = time.perf_counter()
    try:
        segments, info = model.transcribe(
            io.BytesIO(wav_bytes),
            language="en",
            vad_filter=True,
            vad_parameters={
                "threshold": _VAD_THRESHOLD,
                "min_speech_duration_ms": _VAD_MIN_SPEECH_MS,
            },
            beam_size=_BEAM_SIZE,
            no_speech_threshold=_NO_SPEECH_THRESHOLD,
            best_of=_BEST_OF,
            patience=_PATIENCE,
            temperature=_TEMPERATURE,
            initial_prompt=_INITIAL_PROMPT or None,
            hotwords=_HOTWORDS or None,
        )
        # segments is a generator — materialise it now
        text_parts: list[str] = []
        for seg in segments:
            part = seg.text.strip()
            if part:
                text_parts.append(part)
    except Exception as exc:
        raise STTTranscriptionError(f"Transcription failed: {exc}") from exc

    latency = time.perf_counter() - t0
    full_text = " ".join(text_parts).strip()

    return TranscribeResult(
        text=full_text,
        no_speech=(len(full_text) == 0),
        duration_sec=float(getattr(info, "duration", 0.0)),
        latency_sec=round(latency, 3),
    )


def stt_health() -> dict[str, object]:
    """Return health/status dict for the STT subsystem."""
    try:
        _get_model()
        return {
            "status": "ready",
            "model": _active_model_size or _MODEL_CANDIDATES[0],
            "model_candidates": list(_MODEL_CANDIDATES),
            "compute": _COMPUTE_TYPE,
            "beam_size": _BEAM_SIZE,
            "best_of": _BEST_OF,
            "preprocessing": _ENABLE_PREPROCESSING,
        }
    except STTUnavailableError as exc:
        return {"status": "unavailable", "error": str(exc)}
