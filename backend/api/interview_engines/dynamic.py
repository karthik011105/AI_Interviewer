"""The conversational interview engine.

Instead of walking a batch of questions generated before the round starts, the
interviewer decides each turn live from the running dialogue: it follows up on
a vague answer, drills into a strong one, and moves on when a topic is done.

Two things keep that freedom from breaking the report:

* a coverage plan (see backend/nlp/coverage_director) steers the interviewer
  toward skills that still need probing, and guarantees absent-tier gaps are
  reached before the round can wrap up;
* `questions_json["questions"]` accretes one QuestionItem-shaped entry per turn,
  so `_get_question_at_index`, `_total_question_count`, and the report's
  covered-topic derivation keep working with no schema migration.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from anyio import ClosedResourceError
from fastapi import WebSocketDisconnect

from backend.api.interview_runtime import (
	_BUSY_STATES,
	_CPU_SEMAPHORE,
	_MAX_CLARIFICATIONS_PER_QUESTION,
	InterviewRuntime,
	WebSocketInterviewError,
	_cancel_turn_commit,
	_handle_speech_end,
	_handle_speech_start,
	_quota_blocked,
	_stop_tts,
	_transcribe_captured_audio,
	speak_stream,
)
from backend.api.interview_engines import scripted
from backend.api.quotas import INTERVIEW_TURN
from backend.api.routes_interview import (
	_build_round_context,
	_get_parsed_resume,
	_merge_saved_response_history,
	_record_covered_topic,
	_utcnow_iso,
)
from backend.config import get_settings
from backend.database.db_errors import ConcurrentUpdateError, DatabaseClientError
from backend.database.queries import advance_interview_question, save_interview_response
from backend.nlp.answer_evaluator import (
	EvaluatorLlmError,
	EvaluatorUnavailableError,
	evaluate_answer,
)
from backend.nlp.conversation_memory import (
	append_turn,
	make_candidate_turn,
	make_interviewer_turn,
)
from backend.nlp.coverage_director import (
	build_coverage_plan,
	enforce_absent_coverage,
	mark_covered,
	pending_targets,
	resolve_budget_pressure,
	resolve_turn_target,
	tier_progress,
)
from backend.nlp.interview_director import (
	DirectorTurn,
	DirectorTurnStream,
	build_director_messages,
	build_director_system_prompt,
)
from backend.nlp.question_generator import _build_skill_allocation_plan

_LOGGER = logging.getLogger(__name__)

MODE = "dynamic"
_TARGET_QUESTION_COUNT = 6


# ---------------------------------------------------------------------------
# Round state, persisted inside questions_json
# ---------------------------------------------------------------------------


def is_dynamic_round(questions_json: dict[str, Any]) -> bool:
	return str((questions_json or {}).get("mode") or "") == MODE


def _plan(runtime: InterviewRuntime) -> dict[str, Any]:
	return runtime.questions_json.setdefault("coverage_plan", build_coverage_plan([]))


def _dialogue(runtime: InterviewRuntime) -> list[dict[str, Any]]:
	return list(runtime.questions_json.get("_dialogue") or [])


def _digest(runtime: InterviewRuntime) -> list[str]:
	return list(runtime.questions_json.get("_dialogue_digest") or [])


def _turns_used(runtime: InterviewRuntime) -> int:
	return len(runtime.questions_json.get("questions") or [])


def _ensure_round_state(runtime: InterviewRuntime) -> None:
	"""Build the coverage plan and system prompt once per connection."""

	questions_json = runtime.questions_json
	questions_json["mode"] = MODE
	questions_json.setdefault("questions", [])
	questions_json.setdefault("_dialogue", [])
	questions_json.setdefault("_dialogue_digest", [])

	if "context" not in runtime.engine_state:
		parsed_resume = _get_parsed_resume(runtime.session_id)
		runtime.engine_state["context"] = _build_round_context(
			runtime.round_type, parsed_resume, runtime.parent_session
		)

	context = runtime.engine_state["context"]

	if not questions_json.get("coverage_plan"):
		questions_json["coverage_plan"] = build_coverage_plan(
			_build_skill_allocation_plan(context, _TARGET_QUESTION_COUNT)
		)

	if "system" not in runtime.engine_state:
		role_title = (
			str(context.get("selected_role_title") or "").strip()
			or str((context.get("skill_profile") or {}).get("role_title") or "").strip()
			or "software engineering"
		)
		runtime.engine_state["system"] = build_director_system_prompt(
			context, role_title=role_title
		)


async def _persist(
	runtime: InterviewRuntime,
	mutate: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
	"""Apply a mutation to questions_json and write it back.

	The mutation runs *inside* the lock, against whatever is current, rather
	than against a snapshot the caller took earlier. With the director and the
	evaluator running concurrently that matters: a scorer holding a copy from
	before the next question was appended would silently erase it.
	"""

	async with runtime.persist_lock:
		questions_json = mutate(dict(runtime.questions_json))
		state_version = int(runtime.round_record.get("state_version") or 1)
		try:
			updated = await asyncio.to_thread(
				advance_interview_question,
				session_id=runtime.session_id,
				round=runtime.round_type,
				new_index=len(questions_json.get("questions") or []),
				difficulty_signal=float(runtime.round_record.get("difficulty_signal") or 0.5),
				questions_json=questions_json,
				expected_state_version=state_version,
			)
		except ConcurrentUpdateError as exc:
			raise WebSocketInterviewError(str(exc)) from exc
		except DatabaseClientError as exc:
			raise WebSocketInterviewError(str(exc)) from exc

		runtime.round_record = updated
		runtime.questions_json = dict(questions_json)
		runtime.current_index = int(updated.get("current_question_index") or 0)


async def _emit_deltas(
	runtime: InterviewRuntime,
	stream: DirectorTurnStream,
	turn_index: int,
) -> AsyncIterator[str]:
	"""Pass sentences to speech while mirroring them to the client as text."""

	async for sentence in stream.sentences():
		with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
			await runtime.send_json(
				{"type": "turn_delta", "turn_index": turn_index, "text": sentence}
			)
		yield sentence


def _question_item(turn: DirectorTurn) -> dict[str, Any]:
	"""Shape a director turn like a generated QuestionItem.

	Everything downstream - the report, `_get_question_at_index`, covered-topic
	derivation - already understands this shape, which is why the dynamic round
	needs no schema migration.
	"""

	return {
		"question": turn["say"],
		"ideal_points": list(turn["ideal_points"]),
		"follow_up": "",
		"difficulty": turn["difficulty"],
		"question_tier": turn["question_tier"],
		"focus_skill": turn["focus_skill"],
		"turn_action": turn["action"],
		"topic_key": turn["topic_key"],
	}


async def _ask_next_turn(runtime: InterviewRuntime) -> None:
	"""Generate, speak, and persist one interviewer turn."""

	_ensure_round_state(runtime)
	questions_json = runtime.questions_json
	turn_index = _turns_used(runtime)

	plan = enforce_absent_coverage(_plan(runtime), turns_used=turn_index)
	questions_json["coverage_plan"] = plan

	if resolve_budget_pressure(plan, turns_used=turn_index) == "wrap_up_now" and turn_index > 0:
		await _complete_round(runtime)
		return

	# The director call is a Groq request like any other turn, so it draws on
	# the same per-account allowance as evaluation.
	if await _quota_blocked(runtime, INTERVIEW_TURN):
		return

	settings = get_settings().groq
	if settings is None:
		raise WebSocketInterviewError("Groq is not configured; the conversational round cannot run.")

	await _cancel_turn_commit(runtime)
	runtime.clarification_mode = False
	runtime.clarification_count = 0
	runtime.reset_audio_capture()
	runtime.interrupt_event = asyncio.Event()
	runtime.prompt_phase = "preparing"

	messages = build_director_messages(
		system=runtime.engine_state["system"],
		dialogue=_dialogue(runtime),
		coverage_plan=plan,
		turns_used=turn_index,
		digest=_digest(runtime),
		covered_topics=list(questions_json.get("covered_topics") or []),
	)

	stream = DirectorTurnStream(
		settings=settings,
		model=settings.resume_parser_model,
		messages=messages,
		coverage_plan=plan,
	)

	await runtime.send_json({"type": "turn_start", "turn_index": turn_index})
	await runtime.set_state("PLAYING")

	await speak_stream(runtime, _emit_deltas(runtime, stream, turn_index))

	turn = stream.turn
	if turn is None:  # pragma: no cover - sentences() always sets one
		raise WebSocketInterviewError("The interviewer produced no question.")

	# The model labels its own turns and those labels drift; the plan is the
	# authority for tier, and a follow-up inherits whatever it is drilling into.
	dialogue = _dialogue(runtime)
	parent = next(
		(t for t in reversed(dialogue) if t.get("role") == "interviewer"), None
	)
	turn["focus_skill"], turn["question_tier"] = resolve_turn_target(
		plan,
		action=turn["action"],
		focus_skill=turn["focus_skill"],
		question_tier=turn["question_tier"],
		parent_focus_skill=(parent or {}).get("focus_skill"),
		parent_question_tier=(parent or {}).get("tier"),
	)

	asked_at = _utcnow_iso()

	def _apply(current: dict[str, Any]) -> dict[str, Any]:
		current["questions"] = [*(current.get("questions") or []), _question_item(turn)]
		current["coverage_plan"] = mark_covered(
			current.get("coverage_plan") or plan,
			focus_skill=turn["focus_skill"],
			question_tier=turn["question_tier"],
			consumes_target=turn["covers_target"],
		)
		next_dialogue, next_digest = append_turn(
			list(current.get("_dialogue") or []),
			make_interviewer_turn(
				index=turn_index,
				text=turn["say"],
				action=turn["action"],
				focus_skill=turn["focus_skill"],
				tier=turn["question_tier"],
				at=asked_at,
			),
			digest=list(current.get("_dialogue_digest") or []),
		)
		current["_dialogue"] = next_dialogue
		current["_dialogue_digest"] = next_digest
		return current

	await _persist(runtime, _apply)

	await runtime.send_json(
		{
			"type": "turn_end",
			"turn_index": turn_index,
			"text": turn["say"],
			"action": turn["action"],
			"focus_skill": turn["focus_skill"],
			"question_tier": turn["question_tier"],
			"difficulty": turn["difficulty"],
			"topic_key": turn["topic_key"],
			"fallback_used": stream.fallback_used,
		}
	)
	# Legacy frame so a client written for the scripted engine still renders the
	# question text. It arrives after the audio starts rather than before.
	await runtime.send_json(
		{
			"type": "question",
			"index": turn_index,
			"total": int(_plan(runtime).get("max_turns") or 0),
			"text": turn["say"],
		}
	)
	await _send_coverage_update(runtime)


async def _send_coverage_update(runtime: InterviewRuntime) -> None:
	plan = _plan(runtime)
	await runtime.send_json(
		{
			"type": "coverage_update",
			"tier_progress": tier_progress(plan),
			"remaining_targets": [
				{"focus_skill": t["focus_skill"], "question_tier": t["question_tier"]}
				for t in pending_targets(plan)
			],
			"turns_used": _turns_used(runtime),
			"min_turns": int(plan.get("min_turns") or 0),
			"max_turns": int(plan.get("max_turns") or 0),
		}
	)


# ---------------------------------------------------------------------------
# Scoring an answer
# ---------------------------------------------------------------------------


async def _record_candidate_turn(
	runtime: InterviewRuntime, answer_text: str, *, turn_index: int
) -> None:
	"""Put the answer into the dialogue before scoring has finished.

	The director reacts to what was said, not to the score, so the transcript
	has to be visible to it immediately. The score is attached to the same entry
	later, once evaluation returns.
	"""

	answered_at = _utcnow_iso()

	def _apply(current: dict[str, Any]) -> dict[str, Any]:
		next_dialogue, next_digest = append_turn(
			list(current.get("_dialogue") or []),
			make_candidate_turn(index=turn_index, text=answer_text, score=None, at=answered_at),
			digest=list(current.get("_dialogue_digest") or []),
		)
		current["_dialogue"] = next_dialogue
		current["_dialogue_digest"] = next_digest
		return current

	await _persist(runtime, _apply)


async def _score_answer(
	runtime: InterviewRuntime,
	answer_text: str,
	*,
	turn_index: int,
	question: dict[str, Any],
) -> None:
	"""Evaluate one answer. Runs concurrently with the next director turn.

	This deliberately does not touch the interview state machine: while it runs,
	the interviewer is already asking the next question, and flipping the state
	to EVALUATING would fight the director for it. Progress is reported with
	`scoring_state` frames instead, and the result is addressed by turn_index so
	the client can attach it to the right bubble whenever it lands.
	"""

	if await _quota_blocked(runtime, INTERVIEW_TURN):
		return

	await runtime.send_json(
		{"type": "scoring_state", "turn_index": turn_index, "status": "running"}
	)

	try:
		async with _CPU_SEMAPHORE:
			evaluation = await asyncio.to_thread(
				evaluate_answer,
				question=str(question.get("question") or ""),
				ideal_points=list(question.get("ideal_points") or []),
				follow_up="",
				answer_text=answer_text,
				round_type=runtime.round_type,
			)
	except (EvaluatorUnavailableError, EvaluatorLlmError) as exc:
		raise WebSocketInterviewError(str(exc)) from exc

	response_id = str(uuid.uuid4())
	now = _utcnow_iso()
	try:
		await asyncio.to_thread(
			save_interview_response,
			{
				"id": response_id,
				"session_id": runtime.session_id,
				"round": runtime.round_type,
				"question_id": f"{runtime.round_type}_{turn_index}",
				"question_text": str(question.get("question") or ""),
				"user_answer_text": answer_text,
				"audio_file_path": None,
				"score": evaluation["final_score"],
				"dimension_scores": {
					"groq": evaluation["groq_score"],
					"sbert": evaluation["sbert_score"],
					"communication": evaluation["communication_score"],
					"targeting_detail": {
						"question_index": turn_index,
						"question_tier": str(question.get("question_tier") or "general").strip().lower(),
						"focus_skill": str(question.get("focus_skill") or "").strip() or None,
						"turn_action": str(question.get("turn_action") or "new_topic"),
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
			},
		)
	except DatabaseClientError as exc:
		raise WebSocketInterviewError(str(exc)) from exc

	final_score = float(evaluation["final_score"])

	def _apply(current: dict[str, Any]) -> dict[str, Any]:
		current = _record_covered_topic(
			current, runtime.round_type, turn_index, question, answered_at=now
		)
		dialogue = [dict(entry) for entry in (current.get("_dialogue") or [])]
		for entry in reversed(dialogue):
			if entry.get("role") == "candidate" and entry.get("i") == turn_index:
				entry["score"] = final_score
				break
		current["_dialogue"] = dialogue
		return current

	current_signal = float(runtime.round_record.get("difficulty_signal") or 0.5)
	runtime.round_record = {
		**runtime.round_record,
		"difficulty_signal": round(current_signal * 0.6 + final_score * 0.4, 4),
	}

	await _persist(runtime, _apply)

	await runtime.send_json(
		{
			"type": "answer_scored",
			"turn_index": turn_index,
			"saved_response_id": response_id,
			"score": final_score,
			"evaluation": evaluation,
		}
	)
	await runtime.send_json(
		{"type": "scoring_state", "turn_index": turn_index, "status": "done"}
	)
	# Legacy frame for a client written against the scripted engine.
	await runtime.send_json(
		{
			"type": "feedback",
			"saved_response_id": response_id,
			"question_index": turn_index,
			"evaluation": evaluation,
			"next_question_index": turn_index + 1,
			"is_last_question": False,
			"total_questions": int(_plan(runtime).get("max_turns") or 0),
			"round_session": runtime.round_record,
		}
	)
	await _send_coverage_update(runtime)


async def _run_scoring(
	runtime: InterviewRuntime,
	answer_text: str,
	*,
	turn_index: int,
	question: dict[str, Any],
) -> None:
	"""Background wrapper: a scoring failure must not tear down the interview."""

	try:
		await _score_answer(runtime, answer_text, turn_index=turn_index, question=question)
	except asyncio.CancelledError:
		raise
	except Exception as exc:  # noqa: BLE001 - reported to the client, not raised
		_LOGGER.exception("Scoring failed for turn %s.", turn_index)
		with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
			await runtime.send_json(
				{
					"type": "error",
					"code": "scoring_failed",
					"turn_index": turn_index,
					"message": str(exc),
				}
			)
			await runtime.send_json(
				{"type": "scoring_state", "turn_index": turn_index, "status": "failed"}
			)


def _track_scoring(runtime: InterviewRuntime, task: asyncio.Task[None]) -> None:
	tasks = [t for t in runtime.engine_state.get("score_tasks", []) if not t.done()]
	tasks.append(task)
	runtime.engine_state["score_tasks"] = tasks


async def _drain_scoring(runtime: InterviewRuntime) -> None:
	"""Wait for outstanding evaluations.

	The round score is averaged from persisted responses, so completion has to
	wait for every in-flight evaluation to land first.
	"""

	tasks = runtime.engine_state.get("score_tasks") or []
	if tasks:
		await asyncio.gather(*tasks, return_exceptions=True)
	runtime.engine_state["score_tasks"] = []


async def _complete_round(runtime: InterviewRuntime) -> None:
	"""Finish the round, reusing the scripted engine's completion path."""

	await _drain_scoring(runtime)
	runtime.last_question_completed = True
	await scripted._complete_round(runtime)


# ---------------------------------------------------------------------------
# Engine hooks
# ---------------------------------------------------------------------------


async def start(runtime: InterviewRuntime) -> None:
	_ensure_round_state(runtime)
	await _ask_next_turn(runtime)


async def handle_transcript(runtime: InterviewRuntime, text: str) -> None:
	if runtime.clarification_mode:
		await scripted._handle_clarification_audio(runtime, text)
		return

	clean_answer = str(text or "").strip()
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

	questions = list(runtime.questions_json.get("questions") or [])
	if not questions:
		await runtime.set_state("LISTENING")
		return

	# Captured before anything else runs: the director is about to append the
	# next question, which would shift this index out from under the scorer.
	turn_index = len(questions) - 1
	question = questions[turn_index]

	await _record_candidate_turn(runtime, clean_answer, turn_index=turn_index)

	# Scoring and the next question both depend only on the transcript, so they
	# run together. Serialising them put two Groq round trips between the
	# candidate finishing and hearing anything back.
	_track_scoring(
		runtime,
		asyncio.create_task(
			_run_scoring(runtime, clean_answer, turn_index=turn_index, question=question)
		),
	)

	plan = _plan(runtime)
	if _turns_used(runtime) >= int(plan.get("hard_max_turns") or 0):
		await _complete_round(runtime)
		return

	await _ask_next_turn(runtime)


async def handle_control(runtime: InterviewRuntime, payload: dict[str, Any]) -> None:
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
		await handle_transcript(runtime, str(payload.get("text") or ""))
		return

	if message_type == "request_next":
		# The conversational engine advances on its own once an answer is
		# scored, so this is a no-op rather than an error: a client written for
		# the scripted engine still sends it.
		await runtime.send_json(
			{"type": "info", "code": "auto_advance", "message": "The interviewer advances automatically."}
		)
		return

	if message_type == "end_round":
		await _cancel_turn_commit(runtime)
		await _stop_tts(runtime)
		await _complete_round(runtime)
		return

	if message_type in {"start_clarification", "end_clarification", "typed_clarification"}:
		await scripted._handle_control_message(runtime, payload)
		return

	await runtime.send_json(
		{
			"type": "error",
			"code": "unknown_message",
			"message": f"Unsupported client message type: {message_type or 'missing'}.",
		}
	)


__all__ = ["MODE", "handle_control", "handle_transcript", "is_dynamic_round", "start"]
