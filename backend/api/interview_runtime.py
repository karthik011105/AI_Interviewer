"""Shared transport and runtime state for the interview websocket.

Everything here is engine-agnostic: connection auth, the runtime dataclass and
its state machine, spend quotas, microphone buffering, speech-to-text, and
sentence-streamed prompt audio.

What happens *after* a turn is transcribed differs between the scripted and
conversational engines, so this module never decides that itself - it calls
through `runtime.engine`, which is attached when the socket connects.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import struct
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from anyio import ClosedResourceError
from fastapi import HTTPException
from fastapi import WebSocket, WebSocketDisconnect

from backend.api.auth import AuthenticatedUser, authenticate_access_token, ensure_session_access
from backend.api.quotas import INTERVIEW_TURN, VOICE, try_consume_quota
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
from backend.nlp.question_generator import (
	QuestionGeneratorLlmError,
	QuestionGeneratorUnavailableError,
	get_or_generate_questions,
	has_valid_non_technical_hr_questions,
	has_valid_conceptual_technical_questions,
)
from backend.voice.stt import STTTranscriptionError, STTUnavailableError, transcribe_audio
from backend.nlp.sentence_chunker import SentenceAccumulator
from backend.voice.tts import TTSOutput, TTSSynthesisError, synthesize_detailed

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
	from backend.api.interview_engines import InterviewEngine

_LOGGER = logging.getLogger(__name__)

_VALID_ROUNDS = {"hr", "technical", "project_discussion"}
_MIC_SAMPLE_RATE = 16000
# The client runs Silero VAD and sends one binary frame per complete utterance,
# so end-of-turn is a client signal (`speech_end`), not something the server
# re-derives from audio energy. This debounce lets a candidate string several
# utterances into one answer ("Um... so the way it works is...") before commit.
_END_OF_TURN_GRACE_SEC = 1.5
_MAX_CLARIFICATIONS_PER_QUESTION = 2

# CPU-bound work (Whisper decode, SBERT encode) runs in worker threads. The
# default asyncio executor allows min(32, cpu+4) threads, which is far more
# CPU contention than a small box absorbs; cap it explicitly.
_MAX_CONCURRENT_CPU_TASKS = max(1, int(os.getenv("INTERVIEW_MAX_CONCURRENT_EVALS", "4")))
_CPU_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_CPU_TASKS)

_BUSY_STATES = frozenset({"TRANSCRIBING", "EVALUATING", "FEEDBACK", "COMPLETE"})

# A real transition table. `set_state` logs and drops an illegal transition
# rather than raising: killing the socket mid-interview loses state that is
# expensive to rebuild, and an unexpected ordering is not worth that cost.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
	"IDLE": frozenset({"PLAYING", "LISTENING", "COMPLETE"}),
	"PLAYING": frozenset({"LISTENING", "CLARIFYING", "TRANSCRIBING", "EVALUATING", "COMPLETE"}),
	"LISTENING": frozenset({"TRANSCRIBING", "EVALUATING", "CLARIFYING", "PLAYING", "COMPLETE"}),
	"TRANSCRIBING": frozenset({"LISTENING", "EVALUATING", "CLARIFYING", "COMPLETE"}),
	"EVALUATING": frozenset({"FEEDBACK", "LISTENING", "COMPLETE"}),
	"FEEDBACK": frozenset({"PLAYING", "LISTENING", "COMPLETE"}),
	"CLARIFYING": frozenset({"LISTENING", "PLAYING", "TRANSCRIBING", "COMPLETE"}),
	"COMPLETE": frozenset(),
}
_VALID_INTERVIEW_STATES = frozenset(_ALLOWED_TRANSITIONS)


class WebSocketInterviewError(RuntimeError):
	"""Raised for recoverable websocket interview flow failures."""


@dataclass(slots=True)
class InterviewRuntime:
	websocket: WebSocket
	session_id: str
	round_type: str
	# The authenticated owner of this session. Carried on the runtime so the
	# spend quotas below can be charged per account, matching how the REST
	# routes meter (see backend/api/quotas.py).
	user_id: str
	parent_session: dict[str, Any]
	round_record: dict[str, Any]
	questions_json: dict[str, Any]
	total_questions: int
	current_index: int
	state: str = "IDLE"
	pending_pcm_frames: list[bytes] = field(default_factory=list)
	interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
	tts_task: asyncio.Task[None] | None = None
	turn_commit_task: asyncio.Task[None] | None = None
	send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
	last_question_completed: bool = False
	prompt_phase: str = "idle"
	clarification_mode: bool = False
	clarification_count: int = 0
	# Set at connect time; see backend/api/interview_engines.
	engine: "InterviewEngine" = None  # type: ignore[assignment]
	# Scratch space owned by the active engine (the conversational engine keeps
	# its coverage plan, dialogue, and cached system prompt here).
	engine_state: dict[str, Any] = field(default_factory=dict)
	# Serializes writes to questions_json. advance_interview_question does a
	# whole-document $set guarded by state_version, so two coroutines racing to
	# persist would make one of them lose with ConcurrentUpdateError.
	persist_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

	async def send_json(self, payload: dict[str, Any]) -> None:
		async with self.send_lock:
			await self.websocket.send_json(payload)

	async def send_bytes(self, payload: bytes) -> None:
		async with self.send_lock:
			await self.websocket.send_bytes(payload)

	async def set_state(self, state: str) -> None:
		if state not in _VALID_INTERVIEW_STATES:
			_LOGGER.warning("Ignoring unknown interview state %r (from %r).", state, self.state)
			return
		if state == self.state:
			return
		if state not in _ALLOWED_TRANSITIONS[self.state]:
			_LOGGER.warning("Ignoring illegal interview transition %s -> %s.", self.state, state)
			return
		self.state = state
		await self.send_json({"type": "state_change", "state": state})

	def reset_audio_capture(self) -> None:
		self.pending_pcm_frames.clear()


async def _quota_blocked(runtime: "InterviewRuntime", quota_name: str) -> bool:
	"""Charge one unit of a spend quota; report and refuse if it is exhausted.

	Unlike the REST routes, an exhausted quota here must not tear down the
	socket: the candidate is mid-interview and the connection carries state that
	is expensive to rebuild. The refusal is sent as an error frame and the
	runtime is returned to LISTENING so the round can continue once the window
	rolls over.
	"""

	retry_after_seconds = try_consume_quota(quota_name, runtime.user_id)
	if retry_after_seconds is None:
		return False

	await runtime.send_json(
		{
			"type": "error",
			"code": "quota_exceeded",
			"quota": quota_name,
			"retry_after_seconds": retry_after_seconds,
			"message": (
				"You have reached the hourly limit for this operation. "
				f"Try again in {retry_after_seconds} seconds."
			),
		}
	)
	if runtime.state not in {"COMPLETE"}:
		await runtime.set_state("LISTENING")
	return True


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
		"Missing access token. Connect with ?access_token=<jwt_access_token>."
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


def _round_is_dynamic(round_type: str) -> bool:
	"""Whether this round is configured to use the conversational engine."""

	from backend.api.interview_engines import dynamic_rounds

	return str(round_type or "").strip().casefold() in dynamic_rounds()


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

	wants_dynamic = _round_is_dynamic(round_type)

	if existing is not None:
		existing_questions_json = dict(existing.get("questions_json") or {})
		current_index = int(existing.get("current_question_index") or 0)
		round_status = str(existing.get("status") or "")
		existing_mode = str(existing_questions_json.get("mode") or "")

		# A finished round is never rebuilt, whatever mode it ran in.
		if round_status == "complete":
			return existing, existing_questions_json

		if existing_mode == "dynamic" and wants_dynamic:
			# A conversational round has no pre-generated batch to validate: its
			# questions accrete as they are asked, so an empty list on turn one is
			# expected rather than a signal to regenerate.
			return existing, existing_questions_json

		# Mode changed since this round was created. Rebuilding is the only safe
		# option: letting the conversational engine append to a scripted batch
		# starts it mid-count and burns the whole turn budget before it asks
		# anything, and the reverse leaves the scripted engine walking turns that
		# were never generated.
		if existing_mode == "dynamic" or wants_dynamic:
			pass
		elif (
			round_status == "complete"
			or (round_type == "technical" and (current_index > 0 or has_valid_conceptual_technical_questions(existing_questions_json)))
			or (round_type == "hr" and (current_index > 0 or has_valid_non_technical_hr_questions(existing_questions_json)))
			or round_type == "project_discussion"
		):
			return existing, existing_questions_json


	parsed_resume = _get_parsed_resume(session_id)
	context = _build_round_context(round_type, parsed_resume, parent_session)
	difficulty_signal = float((existing or {}).get("difficulty_signal") or 0.5)

	if wants_dynamic:
		from backend.nlp.coverage_director import build_coverage_plan
		from backend.nlp.question_generator import _build_skill_allocation_plan

		questions_json = _prepare_questions_json_for_persistence(
			round_type, {"questions": []}, context
		)
		questions_json["mode"] = "dynamic"
		# Built here rather than on the first turn so the connect frame can report
		# a meaningful turn budget before anything has been asked.
		questions_json["coverage_plan"] = build_coverage_plan(
			_build_skill_allocation_plan(context, 6)
		)
		questions_json["_dialogue"] = []
		questions_json["_dialogue_digest"] = []
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
			if runtime.state in _BUSY_STATES:
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


async def _iter_prompt_sentences(text: str) -> AsyncIterator[str]:
	"""Yield a fully known prompt as speakable sentences.

	The scripted rounds already have the whole question in hand, so this is a
	single pass through the same chunker the streaming director will use. It
	exists so both paths share one definition of a sentence boundary.
	"""

	# Tuned shorter than the chunker's defaults. A prompt opens with "Welcome.
	# Let's begin. First question." — around 37 characters — and letting that
	# go out as its own chunk starts the audio roughly a full sentence of
	# synthesis earlier. The cap keeps one long question from becoming a
	# single blob, which is what streaming is meant to avoid.
	accumulator = SentenceAccumulator(min_chars=30, max_chars=160)
	for sentence in accumulator.feed(text):
		yield sentence
	remainder = accumulator.flush()
	if remainder:
		yield remainder


async def speak_stream(runtime: InterviewRuntime, text_chunks: AsyncIterator[str]) -> None:
	"""Synthesize and send prompt audio one sentence at a time.

	Sentence *k+1* is synthesized while *k* is still on the wire, so the
	candidate hears the first words after one sentence of synthesis instead of
	waiting for the whole utterance. Each chunk is a complete, independently
	decodable container, which is what lets the client schedule them gaplessly
	without any protocol change.
	"""

	# ElevenLabs bills per character when it is the active provider, so charge
	# the same VOICE budget the REST /voice/tts route uses. Speech is a prompt,
	# not an answer: when the allowance is gone the round continues silently
	# rather than stalling, since the question text is already on screen.
	if await _quota_blocked(runtime, VOICE):
		runtime.prompt_phase = "idle"
		runtime.tts_task = None
		return

	# A small queue is what creates the overlap: the synthesizer may run one
	# sentence ahead of the sender, but not race arbitrarily far in front of
	# an interrupt.
	audio_queue: asyncio.Queue[TTSOutput | None] = asyncio.Queue(maxsize=2)
	synth_error: TTSSynthesisError | None = None

	async def _synthesize_sentences() -> None:
		nonlocal synth_error
		try:
			async for sentence in text_chunks:
				if runtime.interrupt_event.is_set():
					break
				try:
					output = await asyncio.to_thread(synthesize_detailed, sentence)
				except TTSSynthesisError as exc:
					synth_error = exc
					break
				if output.audio:
					await audio_queue.put(output)
		finally:
			await audio_queue.put(None)

	synth_task = asyncio.create_task(_synthesize_sentences())
	stream_started = False

	interrupt_waiter = asyncio.ensure_future(runtime.interrupt_event.wait())
	try:
		while True:
			if runtime.interrupt_event.is_set():
				break

			# Race the next audio chunk against the interrupt. Waiting on the
			# queue alone would keep the sender blocked until the in-flight
			# synthesis finished, delaying the barge-in by a whole sentence.
			getter = asyncio.ensure_future(audio_queue.get())
			done, _ = await asyncio.wait(
				{getter, interrupt_waiter}, return_when=asyncio.FIRST_COMPLETED
			)
			if getter not in done:
				getter.cancel()
				with contextlib.suppress(asyncio.CancelledError):
					await getter
				break

			output = getter.result()
			if output is None:
				break
			if runtime.interrupt_event.is_set():
				break

			if not stream_started:
				if runtime.prompt_phase != "preparing":
					break
				runtime.prompt_phase = "streaming"
				await runtime.send_json(
					{
						"type": "tts_start",
						"sample_rate": output.sample_rate,
						"channels": 1,
						# Report what the provider actually produced. Piper and
						# ElevenLabs return WAV, edge-tts returns MP3.
						"encoding": output.encoding,
						"provider": output.provider,
					}
				)
				stream_started = True

			await runtime.send_bytes(output.audio)
	finally:
		interrupt_waiter.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await interrupt_waiter
		synth_task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await synth_task

		runtime.prompt_phase = "idle"
		if stream_started:
			with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
				await runtime.send_json({"type": "tts_end"})
		elif synth_error is not None:
			# Nothing was spoken at all, so surface the failure. A mid-stream
			# failure after audio has started is left as truncated speech
			# instead: partial audio beats an error banner.
			with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
				await runtime.send_json(
					{"type": "error", "code": "tts_failed", "message": str(synth_error)}
				)

		runtime.tts_task = None
		if runtime.state not in _BUSY_STATES:
			with contextlib.suppress(RuntimeError, WebSocketDisconnect, ClosedResourceError):
				await runtime.set_state("LISTENING")


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

	# Transcription is CPU-bound server work; share the VOICE budget with the
	# equivalent REST route so both paths draw on one allowance.
	if await _quota_blocked(runtime, VOICE):
		return

	await runtime.set_state("TRANSCRIBING")
	try:
		async with _CPU_SEMAPHORE:
			result = await asyncio.to_thread(
				transcribe_audio,
				_pcm16le_to_wav_bytes(pcm_blob),
				sample_rate=_MIC_SAMPLE_RATE,
			)
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

	# The engine owns everything past this point: a scripted round evaluates
	# against a fixed question, a conversational one also decides what to ask
	# next from the same transcript.
	await runtime.engine.handle_transcript(runtime, result.text)


async def _handle_audio_frame(runtime: InterviewRuntime, pcm_bytes: bytes) -> None:
	"""Buffer one complete utterance sent by the client's Silero VAD.

	Each binary frame is a whole utterance, not a slice of a continuous stream,
	so there is nothing for the server to detect here — it only accumulates.
	Turn boundaries arrive as explicit `speech_start` / `speech_end` control
	messages, or as the `end_answer` button.
	"""

	if runtime.state in _BUSY_STATES:
		return
	if runtime.prompt_phase == "preparing":
		return
	if not pcm_bytes:
		return

	await _cancel_turn_commit(runtime)
	runtime.pending_pcm_frames.append(pcm_bytes)
	if runtime.state != "LISTENING":
		await runtime.set_state("LISTENING")


async def _handle_speech_start(runtime: InterviewRuntime) -> None:
	"""The candidate started talking: barge in over any prompt still playing."""

	if runtime.state in _BUSY_STATES:
		return

	await _cancel_turn_commit(runtime)
	if runtime.state in {"PLAYING", "CLARIFYING"} and runtime.prompt_phase == "streaming":
		await _stop_tts(runtime, notify_interrupt=True)
	if runtime.state != "LISTENING":
		await runtime.set_state("LISTENING")


async def _handle_speech_end(runtime: InterviewRuntime) -> None:
	"""The candidate paused: arm the commit debounce.

	A following `speech_start` cancels it, so several utterances separated by
	short pauses still commit as a single answer.
	"""

	if runtime.state in _BUSY_STATES:
		return
	if not runtime.pending_pcm_frames:
		return

	await _cancel_turn_commit(runtime)
	_schedule_turn_commit(runtime)
