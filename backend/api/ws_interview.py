"""WebSocket entry point for streaming voice interview rounds.

Transport lives in backend/api/interview_runtime.py and per-round behavior in
backend/api/interview_engines/. This module wires the two together behind the
route.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from anyio import ClosedResourceError
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.interview_engines import select_engine
from backend.api.interview_runtime import (
	_MIC_SAMPLE_RATE,
	InterviewRuntime,
	WebSocketInterviewError,
	_authenticate_websocket_user,
	_cancel_turn_commit,
	_handle_audio_frame,
	_handle_speech_end,
	_handle_speech_start,
	_iter_prompt_sentences,
	_load_or_create_round_session,
	_load_parent_session,
	_quota_blocked,
	_stop_tts,
	_transcribe_captured_audio,
	speak_stream,
)
from backend.api.auth import ensure_session_access
from backend.api.routes_interview import _fetch_round_responses, _total_question_count
from backend.database.db_errors import DatabaseClientError, RecordNotFoundError
from backend.nlp.question_generator import (
	QuestionGeneratorLlmError,
	QuestionGeneratorUnavailableError,
)
from backend.voice.tts import TTSSynthesisError, synthesize_detailed

router = APIRouter(prefix="/interview", tags=["interview"])

_VALID_ROUNDS = {"hr", "technical", "project_discussion"}


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
		round_record, questions_json = await asyncio.to_thread(
			_load_or_create_round_session,
			session_id=session_id,
			round_type=round_type,
			parent_session=parent_session,
		)
		is_dynamic = str(questions_json.get("mode") or "") == "dynamic"
		if is_dynamic:
			# Nothing has been asked yet, so the plan's turn budget is the only
			# meaningful "total" to show a candidate.
			total_questions = int(
				(questions_json.get("coverage_plan") or {}).get("max_turns") or 0
			)
		else:
			total_questions = _total_question_count(questions_json, round_type)
			if total_questions <= 0:
				raise WebSocketInterviewError("No questions available for this interview round.")

		runtime = InterviewRuntime(
			websocket=websocket,
			session_id=session_id,
			round_type=round_type,
			user_id=current_user.user_id,
			parent_session=parent_session,
			round_record=round_record,
			questions_json=questions_json,
			total_questions=total_questions,
			current_index=int(round_record.get("current_question_index") or 0),
			engine=select_engine(round_type),
		)

		await runtime.send_json(
			{
				"type": "connected",
				"session_id": session_id,
				"round": round_type,
				"current_question_index": runtime.current_index,
				"total_questions": total_questions,
				"mode": runtime.engine.name,
			}
		)
		await runtime.set_state("IDLE")

		# A fresh conversational round has current_index == total_questions == 0,
		# which must not read as "already finished".
		already_finished = str(round_record.get("status") or "") == "complete" or (
			not is_dynamic and runtime.current_index >= total_questions
		)
		if already_finished:
			await runtime.set_state("COMPLETE")
			responses = await asyncio.to_thread(_fetch_round_responses, session_id, round_type)
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

		await runtime.engine.start(runtime)

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
			await runtime.engine.handle_control(runtime, payload)
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
