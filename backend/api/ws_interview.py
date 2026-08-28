"""WebSocket interview state machine for streaming voice rounds."""

from __future__ import annotations

import asyncio
import contextlib
import json
import struct
import uuid
from dataclasses import dataclass, field
from typing import Any

from anyio import ClosedResourceError
from fastapi import HTTPException
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.auth import AuthenticatedUser, authenticate_access_token, ensure_session_access
from backend.api.routes_interview import (
	_build_round_context,
	_fetch_round_responses,
	_generate_clarification_reply,
	_get_parsed_resume,
	_get_question_at_index,
	_merge_saved_response_history,
	_prepare_questions_json_for_persistence,
	_record_covered_topic,
	_total_question_count,
	_utcnow_iso,
)
from backend.database.queries import (
	advance_interview_question,
	complete_interview_round,
	create_interview_round_session,
	get_interview_round_session,
	get_session,
	save_interview_response,
)
from backend.database.db_errors import (
	ConcurrentUpdateError,
	RecordNotFoundError,
	DatabaseClientError,
)
from backend.nlp.answer_evaluator import (
	EvaluatorLlmError,
	EvaluatorUnavailableError,
	evaluate_answer,
)
from backend.nlp.feedback_generator import (
	FeedbackGeneratorLlmError,
	FeedbackGeneratorUnavailableError,
	generate_round_feedback,
)
from backend.nlp.question_generator import (
	QuestionGeneratorLlmError,
	QuestionGeneratorUnavailableError,
	get_or_generate_questions,
	has_valid_non_technical_hr_questions,
	has_valid_conceptual_technical_questions,
)
from backend.voice.stt import STTTranscriptionError, STTUnavailableError, transcribe_audio
from backend.voice.tts import TTSSynthesisError, synthesize_chunks
from backend.voice.vad import EnergyVAD

router = APIRouter(prefix="/interview", tags=["interview"])

_VALID_ROUNDS = {"hr", "technical", "project_discussion"}
_MIC_SAMPLE_RATE = 16000
_TTS_CHUNK_MS = 100
_VAD_THRESHOLD = 400.0
_VAD_ONSET_FRAMES = 3
_VAD_OFFSET_FRAMES = 25
_END_OF_TURN_GRACE_SEC = 1.25
_MAX_CLARIFICATIONS_PER_QUESTION = 2
_VALID_INTERVIEW_STATES = {
	"IDLE",
	"PLAYING",
	"LISTENING",
	"RECORDING",
	"TRANSCRIBING",
	"EVALUATING",
	"FEEDBACK",
	"CLARIFYING",
	"COMPLETE",
}


class WebSocketInterviewError(RuntimeError):
	"""Raised for recoverable websocket interview flow failures."""


@dataclass(slots=True)
class InterviewRuntime:
	websocket: WebSocket
	session_id: str
	round_type: str
	parent_session: dict[str, Any]
	round_record: dict[str, Any]
	questions_json: dict[str, Any]
	total_questions: int
	current_index: int
	state: str = "IDLE"
	vad: EnergyVAD = field(
		default_factory=lambda: EnergyVAD(
			threshold=_VAD_THRESHOLD,
			onset_frames=_VAD_ONSET_FRAMES,
			offset_frames=_VAD_OFFSET_FRAMES,
		)
	)
	pending_pcm_frames: list[bytes] = field(default_factory=list)
	speech_active: bool = False
	interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
	tts_task: asyncio.Task[None] | None = None
	turn_commit_task: asyncio.Task[None] | None = None
	send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
	last_question_completed: bool = False
	prompt_phase: str = "idle"
	clarification_mode: bool = False
	clarification_count: int = 0

	async def send_json(self, payload: dict[str, Any]) -> None:
		async with self.send_lock:
			await self.websocket.send_json(payload)

	async def send_bytes(self, payload: bytes) -> None:
		async with self.send_lock:
			await self.websocket.send_bytes(payload)

	async def set_state(self, state: str) -> None:
		if state not in _VALID_INTERVIEW_STATES:
			raise WebSocketInterviewError(f"Unsupported interview state transition: {state}")
		self.state = state
		await self.send_json({"type": "state_change", "state": state})

	def reset_audio_capture(self) -> None:
		self.pending_pcm_frames.clear()
		self.speech_active = False
		self.vad.reset()


def _pcm16le_to_wav_bytes(pcm_bytes: bytes, *, sample_rate: int = _MIC_SAMPLE_RATE) -> bytes:
	"""Wrap raw PCM bytes in a minimal mono 16-bit WAV container for Whisper."""
	data_size = len(pcm_bytes)
	return b"".join(
		[
			struct.pack(
				"<4sI4s4sIHHIIHH4sI",
				b"RIFF",
				36 + data_size,
				b"WAVE",
				b"fmt ",
				16,
				1,
				1,
				sample_rate,
				sample_rate * 2,
				2,
				16,
				b"data",
				data_size,
			),
			pcm_bytes,
		]
	)


def _extract_websocket_access_token(websocket: WebSocket) -> str:
	query_token = str(websocket.query_params.get("access_token") or "").strip()
	if query_token:
		return query_token

	authorization = str(websocket.headers.get("authorization") or "").strip()
	if authorization:
		scheme, _, token = authorization.partition(" ")
		if scheme.casefold() != "bearer" or not token.strip():
			raise WebSocketInterviewError("Authorization must use a bearer token.")
		return token.strip()

	raise WebSocketInterviewError(
		"Missing access token. Connect with ?access_token=<supabase_access_token>."
	)


def _authenticate_websocket_user(websocket: WebSocket) -> AuthenticatedUser:
	try:
		return authenticate_access_token(_extract_websocket_access_token(websocket))
	except HTTPException as exc:
		raise WebSocketInterviewError(str(exc.detail)) from exc


def _load_parent_session(session_id: str) -> dict[str, Any]:
	try:
		parent_session = get_session(session_id)
	except DatabaseClientError as exc:
		raise WebSocketInterviewError(str(exc)) from exc
	if parent_session is None:
		raise WebSocketInterviewError("Session not found.")
	return parent_session


def _load_or_create_round_session(
	*,
	session_id: str,
	round_type: str,
	parent_session: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
	try:
		existing = get_interview_round_session(session_id=session_id, round=round_type)
	except DatabaseClientError as exc:
		raise WebSocketInterviewError(str(exc)) from exc

	if existing is not None:
		existing_questions_json = dict(existing.get("questions_json") or {})
		current_index = int(existing.get("current_question_index") or 0)
		round_status = str(existing.get("status") or "")
		if (
			round_status == "complete"
			or (round_type == "technical" and (current_index > 0 or has_valid_conceptual_technical_questions(existing_questions_json)))
			or (round_type == "hr" and (current_index > 0 or has_valid_non_technical_hr_questions(existing_questions_json)))
			or round_type == "project_discussion"
		):
			return existing, existing_questions_json


	parsed_resume = _get_parsed_resume(session_id)
	context = _build_round_context(round_type, parsed_resume, parent_session)
	difficulty_signal = float((existing or {}).get("difficulty_signal") or 0.5)
	try:
		questions_json = get_or_generate_questions(
			round_type,
			context,
			cached_questions_json=None,
			difficulty_signal=difficulty_signal,
		)
	except (QuestionGeneratorUnavailableError, QuestionGeneratorLlmError) as exc:
		raise WebSocketInterviewError(str(exc)) from exc
	questions_json = _prepare_questions_json_for_persistence(round_type, questions_json, context)

	role_key = str(parent_session.get("role_selected") or "").strip() or "unknown"
	try:
		round_record = create_interview_round_session(
			session_id=session_id,
			round=round_type,
			role_key=role_key,
			questions_json=questions_json,
			started_at=_utcnow_iso(),
		)
	except DatabaseClientError as exc:
		raise WebSocketInterviewError(str(exc)) from exc

	return round_record, dict(questions_json)


def _build_spoken_prompt(question_text: str, index: int) -> str:
	prefix = "Welcome. Let's begin. First question. " if index == 0 else ""
	return f"{prefix}{question_text}".strip()


async def _cancel_turn_commit(runtime: InterviewRuntime) -> None:
	task = runtime.turn_commit_task
	if task is None:
		return
	if task is asyncio.current_task():
		runtime.turn_commit_task = None
		return
	task.cancel()
	with contextlib.suppress(asyncio.CancelledError):
		await task
	runtime.turn_commit_task = None


async def _stop_tts(runtime: InterviewRuntime, *, notify_interrupt: bool = False) -> None:
	runtime.interrupt_event.set()
	runtime.prompt_phase = "idle"
	if notify_interrupt:
		await runtime.send_json({"type": "interrupt_ack"})
	tts_task = runtime.tts_task
	if tts_task is None:
		return
	with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
		await asyncio.wait_for(asyncio.shield(tts_task), timeout=0.2)
	runtime.tts_task = None


def _schedule_turn_commit(runtime: InterviewRuntime) -> None:
	async def _commit_after_pause() -> None:
		try:
			await asyncio.sleep(_END_OF_TURN_GRACE_SEC)
			if runtime.state in {"TRANSCRIBING", "EVALUATING", "FEEDBACK", "COMPLETE"}:
				return
			if not runtime.pending_pcm_frames:
				return
			await _transcribe_captured_audio(runtime)
		except asyncio.CancelledError:
			raise
		finally:
			if runtime.turn_commit_task is asyncio.current_task():
				runtime.turn_commit_task = None

	runtime.turn_commit_task = asyncio.create_task(_commit_after_pause())


async def _stream_tts(runtime: InterviewRuntime, spoken_text: str) -> None:
	stream_started = False
	try:
		audio_bytes = await asyncio.to_thread(
			synthesize,
			spoken_text,
		)
	except TTSSynthesisError as exc:
		runtime.prompt_phase = "idle"
		await runtime.send_json({"type": "error", "code": "tts_failed", "message": str(exc)})
		await runtime.set_state("LISTENING")
		return

	if runtime.interrupt_event.is_set() or runtime.prompt_phase != "preparing":
		runtime.prompt_phase = "idle"
		if runtime.state not in {"TRANSCRIBING", "EVALUATING", "FEEDBACK", "COMPLETE"}:
			await runtime.set_state("LISTENING")
		return

	runtime.prompt_phase = "streaming"

	await runtime.send_json(
		{
			"type": "tts_start",
			"sample_rate": 22050,
			"channels": 1,
			"encoding": "mp3", # The client now decodes natively
		}
	)
	stream_started = True

	try:
		if not runtime.interrupt_event.is_set() and audio_bytes:
			await runtime.send_bytes(audio_bytes)
	finally:
		runtime.prompt_phase = "idle"
		if stream_started:
			with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
				await runtime.send_json({"type": "tts_end"})
		runtime.tts_task = None
		if runtime.state not in {"TRANSCRIBING", "EVALUATING", "FEEDBACK", "COMPLETE"}:
			with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
				await runtime.set_state("LISTENING")


async def _send_current_question(runtime: InterviewRuntime) -> None:
	question = _get_question_at_index(runtime.questions_json, runtime.round_type, runtime.current_index)
	if question is None:
		raise WebSocketInterviewError(f"No question at index {runtime.current_index}.")

	await _cancel_turn_commit(runtime)
	runtime.clarification_mode = False
	runtime.clarification_count = 0
	runtime.reset_audio_capture()
	runtime.interrupt_event = asyncio.Event()
	runtime.prompt_phase = "preparing"
	question_text = str(question.get("question") or "").strip()
	await runtime.send_json(
		{
			"type": "question",
			"index": runtime.current_index,
			"total": runtime.total_questions,
			"text": question_text,
		}
	)
	await runtime.set_state("PLAYING")
	runtime.tts_task = asyncio.create_task(
		_stream_tts(runtime, _build_spoken_prompt(question_text, runtime.current_index))
	)


async def _complete_round(runtime: InterviewRuntime) -> None:
	await _cancel_turn_commit(runtime)
	responses = _fetch_round_responses(runtime.session_id, runtime.round_type)
	total_score = 0.0
	if responses:
		total_score = round(sum(float(row.get("score") or 0.0) for row in responses) / len(responses), 4)

	try:
		round_feedback = generate_round_feedback(runtime.round_type, responses)
	except (FeedbackGeneratorUnavailableError, FeedbackGeneratorLlmError) as exc:
		raise WebSocketInterviewError(str(exc)) from exc

	try:
		completed_record = complete_interview_round(
			session_id=runtime.session_id,
			round=runtime.round_type,
			total_score=total_score,
		)
	except (RecordNotFoundError, DatabaseClientError) as exc:
		raise WebSocketInterviewError(str(exc)) from exc

	runtime.round_record = completed_record
	await runtime.set_state("COMPLETE")
	await runtime.send_json(
		{
			"type": "round_complete",
			"round": runtime.round_type,
			"total_score": total_score,
			"response_count": len(responses),
			"round_feedback": round_feedback,
			"round_session": completed_record,
		}
	)


async def _evaluate_answer_text(runtime: InterviewRuntime, answer_text: str) -> None:
	clean_answer = str(answer_text or "").strip()
	if not clean_answer:
		await runtime.send_json(
			{
				"type": "transcript",
				"text": "",
				"no_speech": True,
				"duration_sec": 0.0,
				"latency_sec": 0.0,
			}
		)
		await runtime.set_state("LISTENING")
		return

	question = _get_question_at_index(runtime.questions_json, runtime.round_type, runtime.current_index)
	if question is None:
		raise WebSocketInterviewError(f"No question at index {runtime.current_index}.")

	question_text = str(question.get("question") or "")
	ideal_points = list(question.get("ideal_points") or [])
	try:
		await runtime.set_state("EVALUATING")
		evaluation = evaluate_answer(
			question=question_text,
			ideal_points=ideal_points,
			follow_up="",
			answer_text=clean_answer,
			round_type=runtime.round_type,
		)
	except (EvaluatorUnavailableError, EvaluatorLlmError) as exc:
		raise WebSocketInterviewError(str(exc)) from exc

	response_id = str(uuid.uuid4())
	now = _utcnow_iso()
	try:
		saved_response = save_interview_response(
			{
				"id": response_id,
				"session_id": runtime.session_id,
				"round": runtime.round_type,
				"question_id": f"{runtime.round_type}_{runtime.current_index}",
				"question_text": question_text,
				"user_answer_text": clean_answer,
				"audio_file_path": None,
				"score": evaluation["final_score"],
				"dimension_scores": {
					"groq": evaluation["groq_score"],
					"sbert": evaluation["sbert_score"],
					"communication": evaluation["communication_score"],
					"targeting_detail": {
						"question_index": runtime.current_index,
						"question_tier": str(question.get("question_tier") or "general").strip().lower(),
						"focus_skill": str(question.get("focus_skill") or "").strip() or None,
					},
					"rubric": evaluation["rubric"],
					"communication_detail": evaluation["communication"],
					"evaluation_mode": evaluation.get("evaluation_mode"),
					"fallback_reason": evaluation.get("fallback_reason"),
					"local_score": evaluation.get("local_score"),
				},
				"feedback": evaluation["feedback"],
				"created_at": now,
			}
		)
	except DatabaseClientError as exc:
		raise WebSocketInterviewError(str(exc)) from exc

	current_signal = float(runtime.round_record.get("difficulty_signal") or 0.5)
	new_signal = round((current_signal * 0.6 + evaluation["final_score"] * 0.4), 4)
	next_index = runtime.current_index + 1
	is_last_question = next_index >= runtime.total_questions
	state_version = int(runtime.round_record.get("state_version") or 1)
	updated_questions_json = _record_covered_topic(
		_merge_saved_response_history(runtime.questions_json, saved_response),
		runtime.round_type,
		runtime.current_index,
		question,
		answered_at=now,
	)

	try:
		updated_round = advance_interview_question(
			session_id=runtime.session_id,
			round=runtime.round_type,
			new_index=next_index,
			difficulty_signal=new_signal,
			questions_json=updated_questions_json,
			expected_state_version=state_version,
		)
	except ConcurrentUpdateError as exc:
		raise WebSocketInterviewError(str(exc)) from exc
	except DatabaseClientError as exc:
		raise WebSocketInterviewError(str(exc)) from exc

	runtime.round_record = updated_round
	runtime.questions_json = dict(updated_questions_json)
	runtime.current_index = next_index
	runtime.last_question_completed = is_last_question
	runtime.reset_audio_capture()

	await runtime.set_state("FEEDBACK")
	await runtime.send_json(
		{
			"type": "feedback",
			"saved_response_id": response_id,
			"question_index": next_index - 1,
			"evaluation": evaluation,
			"next_question_index": next_index,
			"is_last_question": is_last_question,
			"total_questions": runtime.total_questions,
			"round_session": updated_round,
		}
	)

	if is_last_question:
		await _complete_round(runtime)


async def _handle_clarification_audio(runtime: InterviewRuntime, clarification_text: str) -> None:
	clean_text = str(clarification_text or "").strip()
	if not clean_text:
		await runtime.send_json(
			{
				"type": "transcript",
				"text": "",
				"no_speech": True,
				"duration_sec": 0.0,
				"latency_sec": 0.0,
			}
		)
		await runtime.set_state("LISTENING")
		return

	question = _get_question_at_index(runtime.questions_json, runtime.round_type, runtime.current_index)
	if question is None:
		raise WebSocketInterviewError(f"No question at index {runtime.current_index}.")

	question_text = str(question.get("question") or "").strip()
	role_key = str(runtime.parent_session.get("role_selected") or "").strip() or None

	try:
		reply_text = await asyncio.to_thread(
			_generate_clarification_reply,
			round_type=runtime.round_type,
			role_key=role_key,
			question_text=question_text,
			clarification_text=clean_text,
		)
	except HTTPException as exc:
		await runtime.send_json(
			{
				"type": "error",
				"code": "clarification_failed",
				"message": str(exc.detail),
			}
		)
		await runtime.set_state("LISTENING")
		return

	runtime.clarification_count += 1
	remaining = max(0, _MAX_CLARIFICATIONS_PER_QUESTION - runtime.clarification_count)
	await runtime.send_json(
		{
			"type": "clarification_reply",
			"text": reply_text,
			"clarification_index": runtime.clarification_count,
			"remaining": remaining,
		}
	)
	if remaining <= 0:
		runtime.clarification_mode = False
		await runtime.send_json(
			{
				"type": "clarification_limit_reached",
				"clarification_index": runtime.clarification_count,
				"remaining": remaining,
			}
		)

	runtime.reset_audio_capture()
	runtime.interrupt_event = asyncio.Event()
	runtime.prompt_phase = "preparing"
	await runtime.set_state("CLARIFYING")
	runtime.tts_task = asyncio.create_task(_stream_tts(runtime, reply_text))


async def _transcribe_captured_audio(runtime: InterviewRuntime) -> None:
	pcm_blob = b"".join(runtime.pending_pcm_frames)
	runtime.reset_audio_capture()
	if not pcm_blob:
		await runtime.send_json(
			{
				"type": "transcript",
				"text": "",
				"no_speech": True,
				"duration_sec": 0.0,
				"latency_sec": 0.0,
			}
		)
		await runtime.set_state("LISTENING")
		return

	await runtime.set_state("TRANSCRIBING")
	try:
		result = transcribe_audio(_pcm16le_to_wav_bytes(pcm_blob), sample_rate=_MIC_SAMPLE_RATE)
	except STTUnavailableError as exc:
		await runtime.send_json(
			{
				"type": "error",
				"code": "stt_unavailable",
				"message": str(exc),
				"fallback": "typed",
			}
		)
		await runtime.set_state("LISTENING")
		return
	except STTTranscriptionError as exc:
		await runtime.send_json(
			{
				"type": "error",
				"code": "stt_failed",
				"message": str(exc),
				"fallback": "typed",
			}
		)
		await runtime.set_state("LISTENING")
		return

	await runtime.send_json(
		{
			"type": "transcript",
			"text": result.text,
			"no_speech": result.no_speech,
			"duration_sec": result.duration_sec,
			"latency_sec": result.latency_sec,
		}
	)

	if result.no_speech:
		await runtime.set_state("LISTENING")
		return

	if runtime.clarification_mode:
		await _handle_clarification_audio(runtime, result.text)
		return

	await _evaluate_answer_text(runtime, result.text)


async def _handle_audio_frame(runtime: InterviewRuntime, pcm_bytes: bytes) -> None:
	if runtime.state in {"TRANSCRIBING", "EVALUATING", "FEEDBACK", "COMPLETE"}:
		return
	if runtime.prompt_phase == "preparing":
		return

	event = runtime.vad.process(pcm_bytes)
	if runtime.state in {"PLAYING", "CLARIFYING"} and runtime.prompt_phase == "streaming" and event.name == "speech_start":
		await _cancel_turn_commit(runtime)
		await _stop_tts(runtime, notify_interrupt=True)
		await runtime.set_state("LISTENING")

	if event.name in {"speech_start", "speech"}:
		await _cancel_turn_commit(runtime)
		runtime.pending_pcm_frames.append(pcm_bytes)
		runtime.speech_active = True
		if runtime.state != "LISTENING":
			await runtime.set_state("LISTENING")
		return

	if event.name == "speech_end":
		if runtime.speech_active:
			if runtime.state != "LISTENING":
				await runtime.set_state("LISTENING")
			await _cancel_turn_commit(runtime)
			_schedule_turn_commit(runtime)
		return

	if runtime.speech_active:
		runtime.pending_pcm_frames.append(pcm_bytes)


async def _handle_control_message(runtime: InterviewRuntime, payload: dict[str, Any]) -> None:
	message_type = str(payload.get("type") or "").strip()
	if message_type == "ping":
		await runtime.send_json({"type": "pong"})
		return

	if message_type in {"interrupt", "skip_audio"}:
		await _cancel_turn_commit(runtime)
		await _stop_tts(runtime, notify_interrupt=True)
		await runtime.set_state("LISTENING")
		return

	if message_type == "start_clarification":
		if runtime.state in {"TRANSCRIBING", "EVALUATING", "FEEDBACK", "COMPLETE"}:
			await runtime.send_json(
				{
					"type": "error",
					"code": "invalid_state",
					"message": "start_clarification is not available right now.",
				}
			)
			return
		if runtime.clarification_count >= _MAX_CLARIFICATIONS_PER_QUESTION:
			await runtime.send_json(
				{
					"type": "clarification_limit_reached",
					"clarification_index": runtime.clarification_count,
					"remaining": 0,
				}
			)
			return
		await _cancel_turn_commit(runtime)
		await _stop_tts(runtime)
		runtime.reset_audio_capture()
		runtime.clarification_mode = True
		await runtime.send_json(
			{
				"type": "clarification_mode_active",
				"remaining": _MAX_CLARIFICATIONS_PER_QUESTION - runtime.clarification_count,
			}
		)
		await runtime.set_state("LISTENING")
		return

	if message_type == "end_clarification":
		await _cancel_turn_commit(runtime)
		await _stop_tts(runtime)
		runtime.clarification_mode = False
		runtime.reset_audio_capture()
		await runtime.send_json(
			{
				"type": "clarification_mode_inactive",
				"remaining": max(0, _MAX_CLARIFICATIONS_PER_QUESTION - runtime.clarification_count),
			}
		)
		await runtime.set_state("LISTENING")
		return

	if message_type == "typed_clarification":
		if not runtime.clarification_mode:
			await runtime.send_json(
				{
					"type": "error",
					"code": "invalid_state",
					"message": "typed_clarification is only available during clarification mode.",
				}
			)
			return
		await _cancel_turn_commit(runtime)
		await _stop_tts(runtime)
		await _handle_clarification_audio(runtime, str(payload.get("text") or ""))
		return

	if message_type == "end_answer":
		await _cancel_turn_commit(runtime)
		await _stop_tts(runtime)
		if not runtime.pending_pcm_frames:
			await runtime.send_json(
				{
					"type": "error",
					"code": "no_answer_audio",
					"message": "Speak first, then finish your answer or switch to typed input.",
				}
			)
			await runtime.set_state("LISTENING")
			return
		await _transcribe_captured_audio(runtime)
		return

	if message_type == "typed_answer":
		await _cancel_turn_commit(runtime)
		await _stop_tts(runtime)
		await _evaluate_answer_text(runtime, str(payload.get("text") or ""))
		return

	if message_type == "request_next":
		await _cancel_turn_commit(runtime)
		if runtime.state != "FEEDBACK":
			await runtime.send_json(
				{
					"type": "error",
					"code": "invalid_state",
					"message": "request_next is only valid after feedback.",
				}
			)
			return
		if runtime.last_question_completed:
			await runtime.send_json(
				{
					"type": "error",
					"code": "round_complete",
					"message": "The round is already complete.",
				}
			)
			return
		await _send_current_question(runtime)
		return

	await runtime.send_json(
		{
			"type": "error",
			"code": "unknown_message",
			"message": f"Unsupported client message type: {message_type or 'missing'}.",
		}
	)


@router.websocket("/ws/{session_id}/{round_type}")
async def interview_websocket(
	websocket: WebSocket,
	session_id: str,
	round_type: str,
) -> None:
	if round_type not in _VALID_ROUNDS:
		await websocket.close(code=1008, reason="Unsupported round type.")
		return

	await websocket.accept()
	runtime: InterviewRuntime | None = None

	try:
		current_user = _authenticate_websocket_user(websocket)
		parent_session = _load_parent_session(session_id)
		ensure_session_access(parent_session, current_user)
		round_record, questions_json = _load_or_create_round_session(
			session_id=session_id,
			round_type=round_type,
			parent_session=parent_session,
		)
		total_questions = _total_question_count(questions_json, round_type)
		if total_questions <= 0:
			raise WebSocketInterviewError("No questions available for this interview round.")

		runtime = InterviewRuntime(
			websocket=websocket,
			session_id=session_id,
			round_type=round_type,
			parent_session=parent_session,
			round_record=round_record,
			questions_json=questions_json,
			total_questions=total_questions,
			current_index=int(round_record.get("current_question_index") or 0),
		)

		await runtime.send_json(
			{
				"type": "connected",
				"session_id": session_id,
				"round": round_type,
				"current_question_index": runtime.current_index,
				"total_questions": total_questions,
			}
		)
		await runtime.set_state("IDLE")

		if str(round_record.get("status") or "") == "complete" or runtime.current_index >= total_questions:
			await runtime.set_state("COMPLETE")
			responses = _fetch_round_responses(session_id, round_type)
			await runtime.send_json(
				{
					"type": "round_complete",
					"round": round_type,
					"total_score": float(round_record.get("total_score") or 0.0),
					"response_count": len(responses),
					"round_session": round_record,
				}
			)
			return

		await _send_current_question(runtime)

		while True:
			message = await websocket.receive()
			message_type = message.get("type")
			if message_type == "websocket.disconnect":
				break
			if message.get("bytes") is not None:
				await _handle_audio_frame(runtime, message["bytes"])
				continue
			text_payload = message.get("text")
			if not text_payload:
				continue
			try:
				payload = json.loads(text_payload)
			except json.JSONDecodeError:
				payload = {"type": text_payload}
			await _handle_control_message(runtime, payload)
	except WebSocketDisconnect:
		return
	except WebSocketInterviewError as exc:
		with contextlib.suppress(RuntimeError):
			await websocket.send_json({"type": "error", "code": "session_error", "message": str(exc)})
		with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
			await websocket.close(code=1008, reason=str(exc))
	finally:
		if runtime is not None:
			with contextlib.suppress(RuntimeError, WebSocketDisconnect, asyncio.CancelledError):
				await _cancel_turn_commit(runtime)
			with contextlib.suppress(RuntimeError, WebSocketDisconnect):
				await _stop_tts(runtime)


__all__ = ["router"]