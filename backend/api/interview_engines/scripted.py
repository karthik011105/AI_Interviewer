"""The original fixed-question interview flow.

Moved verbatim out of ws_interview.py when the conversational engine was added.
A batch of questions is generated before the round starts and walked in order;
nothing here reacts to what the candidate says beyond scoring it.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from anyio import ClosedResourceError
from fastapi import HTTPException, WebSocketDisconnect

from backend.api.interview_runtime import (
	_CPU_SEMAPHORE,
	_BUSY_STATES,
	_MAX_CLARIFICATIONS_PER_QUESTION,
	InterviewRuntime,
	WebSocketInterviewError,
	_cancel_turn_commit,
	_iter_prompt_sentences,
	_quota_blocked,
	_stop_tts,
	_transcribe_captured_audio,
	speak_stream,
)
from backend.api.quotas import INTERVIEW_TURN, VOICE
from backend.api.routes_interview import (
	_fetch_round_responses,
	_generate_clarification_reply,
	_get_question_at_index,
	_merge_saved_response_history,
	_record_covered_topic,
	_utcnow_iso,
)
from backend.database.db_errors import (
	ConcurrentUpdateError,
	DatabaseClientError,
	RecordNotFoundError,
)
from backend.database.queries import (
	advance_interview_question,
	complete_interview_round,
	save_interview_response,
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
import contextlib
import logging

_LOGGER = logging.getLogger(__name__)


def _build_spoken_prompt(question_text: str, index: int) -> str:
	prefix = "Welcome. Let's begin. First question. " if index == 0 else ""
	return f"{prefix}{question_text}".strip()


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
		speak_stream(runtime, _iter_prompt_sentences(_build_spoken_prompt(question_text, runtime.current_index)))
	)


async def _complete_round(runtime: InterviewRuntime) -> None:
	await _cancel_turn_commit(runtime)
	responses = await asyncio.to_thread(_fetch_round_responses, runtime.session_id, runtime.round_type)
	total_score = 0.0
	if responses:
		total_score = round(sum(float(row.get("score") or 0.0) for row in responses) / len(responses), 4)

	# Coaching feedback is a nice-to-have; the round is already answered and
	# saved. Failing here used to raise *after* the last answer was persisted,
	# tearing down the socket and leaving the round stuck at in_progress.
	round_feedback = None
	feedback_degraded = False
	try:
		round_feedback = await asyncio.to_thread(
			generate_round_feedback, runtime.round_type, responses
		)
	except (FeedbackGeneratorUnavailableError, FeedbackGeneratorLlmError) as exc:
		feedback_degraded = True
		_LOGGER.warning("Round feedback unavailable for %s; completing anyway: %s", runtime.round_type, exc)

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
			"feedback_degraded": feedback_degraded,
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

	# Each evaluation is a Groq call, and a client can send typed_answer frames
	# in a tight loop, so charge the turn quota before spending.
	if await _quota_blocked(runtime, INTERVIEW_TURN):
		return

	question_text = str(question.get("question") or "")
	ideal_points = list(question.get("ideal_points") or [])
	try:
		await runtime.set_state("EVALUATING")
		async with _CPU_SEMAPHORE:
			evaluation = await asyncio.to_thread(
				evaluate_answer,
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
					"concept_coverage": evaluation["concept_coverage"],
					"scoring_profile": evaluation["scoring_profile"],
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
	runtime.tts_task = asyncio.create_task(speak_stream(runtime, _iter_prompt_sentences(reply_text)))


async def _handle_control_message(runtime: InterviewRuntime, payload: dict[str, Any]) -> None:
	message_type = str(payload.get("type") or "").strip()
	if message_type == "ping":
		await runtime.send_json({"type": "pong"})
		return

	if message_type == "speech_start":
		await _handle_speech_start(runtime)
		return

	if message_type == "speech_end":
		await _handle_speech_end(runtime)
		return

	if message_type in {"interrupt", "skip_audio"}:
		await _cancel_turn_commit(runtime)
		await _stop_tts(runtime, notify_interrupt=True)
		await runtime.set_state("LISTENING")
		return

	if message_type == "start_clarification":
		if runtime.state in _BUSY_STATES:
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


async def handle_transcript(runtime: InterviewRuntime, text: str) -> None:
	"""Route a finished transcript to clarification or scoring."""

	if runtime.clarification_mode:
		await _handle_clarification_audio(runtime, text)
		return
	await _evaluate_answer_text(runtime, text)


async def start(runtime: InterviewRuntime) -> None:
	"""Send the first question of the round."""

	await _send_current_question(runtime)


async def handle_control(runtime: InterviewRuntime, payload: dict[str, Any]) -> None:
	await _handle_control_message(runtime, payload)
