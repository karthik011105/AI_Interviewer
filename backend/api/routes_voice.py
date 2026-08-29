"""Voice subsystem routes.

Route summary
-------------
    GET  /voice/health              — TTS + STT health status
    POST /voice/tts                 — Synthesize question text to WAV audio
    POST /voice/stt                 — Transcribe a recorded audio blob to text

These routes are intentionally stateless.  All session state lives in
routes_interview.py.  The frontend calls /voice/tts to get question audio
and /voice/stt to transcribe the candidate's recorded answer, then passes
the transcript to /interview/answer for evaluation.
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.auth import AuthenticatedUser
from backend.api.quotas import VOICE, quota_dependency
from backend.voice.stt import STTTranscriptionError, STTUnavailableError, TranscribeResult, stt_health, transcribe_audio
from backend.voice.tts import TTSSynthesisError, synthesize, tts_health

router = APIRouter(prefix="/voice", tags=["voice"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TTSRequest(BaseModel):
	text: str = Field(..., min_length=1, max_length=2000)
	voice: str | None = None


class TTSResponse(BaseModel):
	audio_b64: str		# base64-encoded WAV bytes
	tts_available: bool	# False when no active TTS provider is ready (silent WAV returned)


class STTRequest(BaseModel):
	# Raw audio bytes base64-encoded (WebM from MediaRecorder API)
	audio_b64: str = Field(..., min_length=1)


class STTResponse(BaseModel):
	text: str
	no_speech: bool
	duration_sec: float
	latency_sec: float


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health")
def voice_health() -> dict[str, Any]:
	return {
		"tts": tts_health(),
		"stt": stt_health(),
	}


@router.post("/tts", response_model=TTSResponse)
def text_to_speech(
	request: TTSRequest,
	# Metered: ElevenLabs is billed per character when it is the active provider.
	current_user: AuthenticatedUser = Depends(quota_dependency(VOICE)),
) -> TTSResponse:
	"""Synthesize text to WAV audio.

	Returns base64-encoded WAV bytes. If no active TTS provider is ready the
	endpoint still returns 200 with tts_available=False and a silent WAV stub so
	the frontend can gracefully degrade (text already visible on screen).
	"""
	tts_status = tts_health()
	is_available = tts_status.get("status") == "ready"

	try:
		wav_bytes = synthesize(request.text, voice=request.voice)
	except TTSSynthesisError as exc:
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail=f"TTS synthesis failed: {exc}",
		) from exc

	return TTSResponse(
		audio_b64=base64.b64encode(wav_bytes).decode("ascii"),
		tts_available=is_available,
	)


@router.post("/stt", response_model=STTResponse)
def speech_to_text(
	request: STTRequest,
	# Metered: transcription is CPU-bound work on the server.
	current_user: AuthenticatedUser = Depends(quota_dependency(VOICE)),
) -> STTResponse:
	"""Transcribe an audio blob to text using faster-whisper.

	The caller sends the raw audio bytes as a base64 string (WebM from the
	browser MediaRecorder API).  Returns the transcript and whether any speech
	was detected.

	Raises 503 if faster-whisper is not installed.
	Raises 422 if the audio_b64 payload is not valid base64.
	"""
	try:
		audio_bytes = base64.b64decode(request.audio_b64, validate=True)
	except Exception as exc:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
			detail="audio_b64 is not valid base64.",
		) from exc

	try:
		result: TranscribeResult = transcribe_audio(audio_bytes)
	except STTUnavailableError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=f"STT unavailable: {exc}",
		) from exc
	except STTTranscriptionError as exc:
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail=f"Transcription failed: {exc}",
		) from exc

	return STTResponse(
		text=result.text,
		no_speech=result.no_speech,
		duration_sec=result.duration_sec,
		latency_sec=result.latency_sec,
	)
