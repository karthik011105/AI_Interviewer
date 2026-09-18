"""WebSocket entry point for streaming voice interview rounds.

Transport lives in backend/api/interview_runtime.py and per-round behavior in
backend/api/interview_engines/. This module wires the two together behind the
route.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from anyio import ClosedResourceError
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.interview_engines import select_engine
from backend.api.interview_runtime import (
	InterviewRuntime,
	WebSocketInterviewError,
	_authenticate_websocket_user,
	_cancel_turn_commit,
	_handle_audio_frame,
	_load_or_create_round_session,
	_load_parent_session,
	_stop_tts,
)
from backend.api.auth import ensure_session_access
from backend.api.routes_interview import _fetch_round_responses, _total_question_count
from backend.metrics import interview_ws_sessions_total

router = APIRouter(prefix="/interview", tags=["interview"])

_LOGGER = logging.getLogger(__name__)

_VALID_ROUNDS = {"hr", "technical", "project_discussion"}

# WebSocket close code for "the server hit an unexpected condition". 1008 is
# "policy violation", which is right for a rejected round type or a session the
# caller may not touch, but wrong for a database outage or a bug.
_WS_INTERNAL_ERROR = 1011


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
			# Counted here rather than left to the `else` clause below: a
			# `return` inside a `try` skips `else` entirely, so reconnecting to
			# an already-finished round would otherwise be missing from the
			# counter despite being a perfectly normal outcome.
			interview_ws_sessions_total.labels(
				round=round_type, outcome="already_complete"
			).inc()
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
		interview_ws_sessions_total.labels(
			round=round_type, outcome="client_disconnect"
		).inc()
		return
	except WebSocketInterviewError as exc:
		interview_ws_sessions_total.labels(round=round_type, outcome="rejected").inc()
		with contextlib.suppress(RuntimeError):
			await websocket.send_json({"type": "error", "code": "session_error", "message": str(exc)})
		with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
			await websocket.close(code=1008, reason=str(exc))
	except Exception as exc:
		# Everything that is not a clean disconnect or a rejection the candidate
		# can act on. Before this existed, such an exception escaped the handler
		# entirely: the socket had been accepted, no frame was sent, close() was
		# never called, and the browser saw an abrupt 1006 with nothing to show
		# the candidate. Verified for DatabaseClientError, ConcurrentUpdateError
		# (two tabs racing one round), and a plain ValueError.
		#
		# It is also invisible: every other middleware in this app is a
		# BaseHTTPMiddleware subclass, and those never see a websocket scope, so
		# neither the catch-all handler nor the request metrics apply here. This
		# handler is the only place that can log or count a failed interview.
		#
		# The message is deliberately generic — the detail goes to the log, not
		# to the candidate — but a frame is sent, because "something broke, this
		# is not your fault" is far better than a socket that simply dies.
		interview_ws_sessions_total.labels(round=round_type, outcome="error").inc()
		_LOGGER.exception(
			"Unhandled error in interview websocket for session %s round %s",
			session_id,
			round_type,
		)
		with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
			await websocket.send_json(
				{
					"type": "error",
					"code": "internal_error",
					"message": "The interview service hit an unexpected error. Please reconnect.",
				}
			)
		with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
			await websocket.close(code=_WS_INTERNAL_ERROR, reason="Internal error.")
	else:
		interview_ws_sessions_total.labels(round=round_type, outcome="completed").inc()
	finally:
		if runtime is not None:
			with contextlib.suppress(RuntimeError, WebSocketDisconnect, asyncio.CancelledError):
				await _cancel_turn_commit(runtime)
			with contextlib.suppress(RuntimeError, WebSocketDisconnect):
				await _stop_tts(runtime)


__all__ = ["router"]
