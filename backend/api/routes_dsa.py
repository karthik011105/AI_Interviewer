"""DSA round REST routes for health checks and session start/resume."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.auth import AuthenticatedUser, ensure_session_access, require_current_user
from backend.database.queries import (
	append_dsa_submission,
	complete_dsa_session,
	create_dsa_session,
	get_final_report,
	get_dsa_session,
	get_session,
	list_dsa_sessions,
	persist_dsa_state,
	update_session_status,
	upsert_final_report,
)
from backend.database.db_errors import DSAStage, JudgeStatus, DatabaseClientError, build_default_dsa_state
from backend.dsa.code_analyzer import analyze_submission_code
from backend.dsa.code_executor import (
	build_editor_starter_code,
	CodeExecutionError,
	DSALanguageDefinition,
	SafetyViolationError,
	judge0_health_check,
	list_supported_dsa_languages,
	normalize_dsa_language,
	run_sample,
	run_submission,
)
from backend.dsa.dsa_evaluator import evaluate_dsa_question

from backend.dsa.interview_brain import (
	apply_message_intelligence,
	apply_submission_intelligence,
	normalize_candidate_model,
)
from backend.dsa.problem_selector import (
	CertifiedProblem,
	CertifiedProblemBankError,
	ProblemSelectionError,
	get_certified_problem,
	list_certified_problems,
	select_problem_pair,
	serialize_problem,
)
from backend.dsa.report_engine import build_dsa_question_report, build_dsa_round_report

router = APIRouter(prefix="/dsa", tags=["dsa"])

TOTAL_DSA_QUESTIONS = 2
DSA_QUESTION_DURATION_MINUTES = 20

SUPPORTED_DSA_LANGUAGES: tuple[DSALanguageDefinition, ...] = tuple(
	list_supported_dsa_languages()
)
SUPPORTED_DSA_LANGUAGE_OPTIONS = [
	{"key": language.key, "label": language.label, "file_name": language.file_name}
	for language in SUPPORTED_DSA_LANGUAGES
]


def _utcnow_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
	text = str(value or "").strip()
	if not text:
		return None
	if text.endswith("Z"):
		text = f"{text[:-1]}+00:00"
	try:
		parsed = datetime.fromisoformat(text)
	except ValueError:
		return None
	if parsed.tzinfo is None:
		return parsed.replace(tzinfo=timezone.utc)
	return parsed.astimezone(timezone.utc)


def _question_deadline_from(started_at: datetime) -> str:
	return (started_at + timedelta(minutes=DSA_QUESTION_DURATION_MINUTES)).isoformat()


def _coerce_mapping(value: Any) -> dict[str, Any]:
	if isinstance(value, Mapping):
		return dict(value)
	return {}


def _ensure_question_deadline(
	*,
	record: Mapping[str, Any],
	session_id: str,
	question_number: int,
) -> dict[str, Any]:
	state = _coerce_mapping(record.get("state_json"))
	question_started_at = _parse_iso_datetime(state.get("question_started_at"))
	deadline_at = _parse_iso_datetime(record.get("deadline_at") or state.get("deadline_at"))
	if question_started_at is not None and deadline_at is not None:
		return dict(record)

	anchor = (
		question_started_at
		or (
			deadline_at - timedelta(minutes=DSA_QUESTION_DURATION_MINUTES)
			if deadline_at is not None
			else None
		)
		or _parse_iso_datetime(record.get("created_at"))
		or _parse_iso_datetime(record.get("stage_started_at"))
		or _parse_iso_datetime(state.get("stage_started_at"))
		or datetime.now(timezone.utc)
	)
	state["question_started_at"] = anchor.isoformat()
	state["deadline_at"] = deadline_at.isoformat() if deadline_at is not None else _question_deadline_from(anchor)
	state.setdefault("stage_started_at", anchor.isoformat())
	try:
		return persist_dsa_state(
			session_id=session_id,
			question_number=question_number,
			state_json=state,
			expected_state_version=int(record.get("state_version") or 0),
			stage=str(record.get("stage") or state.get("stage") or DSAStage.PROBLEM_SETUP.value),
			approach_text=str(record.get("approach_text") or "") or None,
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc


class DSAStartRequest(BaseModel):
	session_id: str = Field(min_length=1)
	question_number: int = Field(ge=1, le=2)
	role_key: str | None = None
	language: str | None = Field(default=None, max_length=32)
	excluded_problem_ids: list[str] = Field(default_factory=list, max_length=20)


class DSARunRequest(BaseModel):
	session_id: str = Field(min_length=1)
	question_number: int = Field(ge=1, le=2)
	code: str = Field(min_length=1, max_length=50000)
	language: str | None = Field(default=None, max_length=32)


class DSASubmitRequest(BaseModel):
	session_id: str = Field(min_length=1)
	question_number: int = Field(ge=1, le=2)
	code: str = Field(min_length=1, max_length=50000)
	language: str | None = Field(default=None, max_length=32)


class DSAMessageRequest(BaseModel):
	session_id: str = Field(min_length=1)
	question_number: int = Field(ge=1, le=2)
	message: str = Field(min_length=1, max_length=4000)
	message_kind: str | None = Field(default=None, max_length=32)


@router.get("/health")
def dsa_health() -> dict[str, Any]:
	try:
		problem_count = len(list_certified_problems())
	except CertifiedProblemBankError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	judge0 = judge0_health_check()
	return {
		"status": "ok" if judge0.get("available") else "degraded",
		"certified_problem_count": problem_count,
		"judge0": judge0,
	}


@router.post("/start")
def start_dsa_round(
	request: DSAStartRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	parent_session = _require_parent_session(request.session_id, current_user)
	role_key = _resolve_role_key(parent_session, request.role_key)
	_require_question_start_preconditions(request.session_id, request.question_number)

	try:
		existing_record = get_dsa_session(request.session_id, request.question_number)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	if existing_record is not None:
		existing_record = _ensure_question_deadline(
			record=existing_record,
			session_id=request.session_id,
			question_number=request.question_number,
		)
		problem = _resolve_record_problem(existing_record)
		existing_record = _ensure_record_language_state(existing_record, problem, requested_language=request.language)
		response = _serialize_dsa_session_record(existing_record, problem=problem)
		response["created_dsa_session"] = False
		return response

	judge0 = judge0_health_check()
	if not judge0.get("available"):
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(judge0.get("detail") or "Judge0 is unavailable."),
		)

	try:
		q1_problem, q2_problem = select_problem_pair(
			role_key,
			session_id=request.session_id,
			excluded_problem_ids=request.excluded_problem_ids,
		)
	except (CertifiedProblemBankError, ProblemSelectionError) as exc:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail=str(exc),
		) from exc

	selected_problem = q1_problem if request.question_number == 1 else q2_problem
	selected_language = _resolve_supported_dsa_language(request.language)
	question_started_at = datetime.now(timezone.utc)
	deadline_at = _question_deadline_from(question_started_at)
	state_json, _, current_code_draft = _build_language_context(
		problem=selected_problem,
		state=build_default_dsa_state(
			stage=DSAStage.PROBLEM_SETUP,
			stage_started_at=question_started_at.isoformat(),
			deadline_at=deadline_at,
		),
		active_language=selected_language,
	)
	state_json["question_started_at"] = question_started_at.isoformat()

	try:
		record = create_dsa_session(
			session_id=request.session_id,
			problem_id=selected_problem.problem_id,
			question_number=request.question_number,
			stage=DSAStage.PROBLEM_SETUP,
			state_json=state_json,
			deadline_at=deadline_at,
			approach_text=None,
			current_code_draft=current_code_draft,
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	response = _serialize_dsa_session_record(record, problem=selected_problem)
	response["created_dsa_session"] = True
	response["started_at"] = _utcnow_iso()
	return response


@router.get("/session/{session_id}/{question_number}")
def get_dsa_session_route(
	session_id: str,
	question_number: int,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	_require_parent_session(session_id, current_user)
	try:
		record = get_dsa_session(session_id, question_number)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	if record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="No DSA session exists for the supplied session_id and question_number.",
		)
	record = _ensure_question_deadline(
		record=record,
		session_id=session_id,
		question_number=question_number,
	)
	problem = _resolve_record_problem(record)
	return _serialize_dsa_session_record(record, problem=problem)


@router.post("/run")
def run_dsa_code(
	request: DSARunRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	record, problem = _require_dsa_record(
		session_id=request.session_id,
		question_number=request.question_number,
		current_user=current_user,
	)
	language = _resolve_dsa_language(request.language)
	state_json, _, _ = _build_record_language_context(
		record,
		problem,
		requested_language=language,
		current_code_override=request.code,
	)
	judge0 = judge0_health_check()
	if not judge0.get("available"):
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(judge0.get("detail") or "Judge0 is unavailable."),
		)

	try:
		execution_results = run_sample(request.code, problem.examples, language=language)
	except SafetyViolationError as exc:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
	except CodeExecutionError as exc:
		print("!!! JUDGE0 ERROR:", str(exc))
		raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

	judge_status = _derive_judge_status(execution_results)
	submission_id = f"run_{uuid4().hex}"
	submission_payload = _build_submission_payload(
		record=record,
		problem=problem,
		code=request.code,
		execution_results=execution_results,
		submission_id=submission_id,
		submission_kind="sample_run",
		language=language,
	)

	updated_record = _append_submission_record(
		record=record,
		question_number=request.question_number,
		session_id=request.session_id,
		submission_payload=submission_payload,
		current_code_draft=request.code,
		last_submission_id=submission_id,
		judge_status=judge_status,
		execution_results=execution_results,
		state_json=state_json,
	)
	updated_problem = _resolve_record_problem(updated_record)
	response = _serialize_dsa_session_record(updated_record, problem=updated_problem)
	response["submission"] = submission_payload
	response["execution_results"] = execution_results
	return response


@router.post("/submit")
def submit_dsa_code(
	request: DSASubmitRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	record, problem = _require_dsa_record(
		session_id=request.session_id,
		question_number=request.question_number,
		current_user=current_user,
	)
	language = _resolve_dsa_language(request.language)
	state_json, _, _ = _build_record_language_context(
		record,
		problem,
		requested_language=language,
		current_code_override=request.code,
	)
	judge0 = judge0_health_check()
	if not judge0.get("available"):
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(judge0.get("detail") or "Judge0 is unavailable."),
		)

	try:
		execution_results = run_submission(request.code, problem.hidden_test_cases, language=language)
	except SafetyViolationError as exc:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
	except CodeExecutionError as exc:
		raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

	judge_status = _derive_judge_status(execution_results)
	submission_id = f"submit_{uuid4().hex}"
	submission_payload = _build_submission_payload(
		record=record,
		problem=problem,
		code=request.code,
		execution_results=execution_results,
		submission_id=submission_id,
		submission_kind="final_submit",
		language=language,
	)

	intermediate_record = _append_submission_record(
		record=record,
		question_number=request.question_number,
		session_id=request.session_id,
		submission_payload=submission_payload,
		current_code_draft=request.code,
		last_submission_id=submission_id,
		judge_status=judge_status,
		execution_results=execution_results,
		state_json=state_json,
	)
	next_stage = _select_post_submit_stage(execution_results)
	final_record = _apply_post_submit_state(
		record=intermediate_record,
		session_id=request.session_id,
		question_number=request.question_number,
		problem=problem,
		next_stage=next_stage,
	)
	progress_summary: dict[str, Any] | None = None
	report_state: dict[str, Any] | None = None
	if _hidden_tests_passed(execution_results):
		final_record, progress_summary, report_state = _maybe_complete_last_question_after_submission(
			record=final_record,
			session_id=request.session_id,
			question_number=request.question_number,
			problem=problem,
		)
	updated_problem = _resolve_record_problem(final_record)
	response = _serialize_dsa_session_record(
		final_record,
		problem=updated_problem,
		progress_summary=progress_summary,
		report_state=report_state,
	)
	response["submission"] = submission_payload
	response["execution_results"] = execution_results
	response["next_stage"] = str(final_record.get("stage") or next_stage)
	response["analysis"] = _coerce_mapping(response.get("analysis"))
	if response.get("coaching_note"):
		response["coach_message"] = response["coaching_note"]
	return response


@router.post("/message")
def persist_dsa_message(
	request: DSAMessageRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	record, problem = _require_dsa_record(
		session_id=request.session_id,
		question_number=request.question_number,
		current_user=current_user,
	)
	state = _coerce_mapping(record.get("state_json"))
	current_stage = str(record.get("stage") or state.get("stage") or DSAStage.PROBLEM_SETUP.value)
	message_text = request.message.strip()
	if not message_text:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="message must not be blank.",
		)
	message_kind = _resolve_message_kind(current_stage, request.message_kind)
	message_entry = {
		"message_id": f"msg_{uuid4().hex}",
		"message_kind": message_kind,
		"stage": current_stage,
		"text": message_text,
		"submitted_at": _utcnow_iso(),
	}
	next_stage = _select_post_message_stage(current_stage, message_kind)
	if next_stage != current_stage:
		state["stage_started_at"] = _utcnow_iso()
	state["stage"] = next_stage
	state.setdefault("approach_messages", [])
	state.setdefault("debrief_messages", [])
	state["awaiting_user_input"] = True

	approach_text = record.get("approach_text")
	if message_kind == "debrief":
		state["debrief_messages"] = list(state.get("debrief_messages") or []) + [message_entry]
		state["last_debrief_message_at"] = message_entry["submitted_at"]
	else:
		state["approach_messages"] = list(state.get("approach_messages") or []) + [message_entry]
		if message_kind == "clarification":
			state["clarification_count"] = int(state.get("clarification_count") or 0) + 1
		else:
			state["approach_exchange_count"] = int(state.get("approach_exchange_count") or 0) + 1
		approach_text = _build_approach_text(state.get("approach_messages") or [])

	brain_update = apply_message_intelligence(
		state=state,
		current_stage=current_stage,
		message_text=message_text,
		message_kind=message_kind,
		problem=problem,
	)
	state = _coerce_mapping(brain_update.get("state"))
	next_stage = str(brain_update.get("next_stage") or next_stage)
	if next_stage != current_stage:
		state["stage_started_at"] = _utcnow_iso()
	state["stage"] = next_stage
	if message_kind != "debrief":
		approach_text = _build_approach_text(state.get("approach_messages") or [])

	try:
		updated_record = persist_dsa_state(
			session_id=request.session_id,
			question_number=request.question_number,
			state_json=state,
			expected_state_version=int(record.get("state_version") or 0),
			stage=next_stage,
			approach_text=approach_text,
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	report_state: dict[str, Any] | None = None
	if message_kind == "debrief":
		updated_record = _finalize_completed_question(
			record=updated_record,
			session_id=request.session_id,
			question_number=request.question_number,
			problem=problem,
		)
		progress_summary = _get_dsa_progress_summary(request.session_id)
		report_state = _persist_round_completion_state(
			session_id=request.session_id,
			progress_summary=progress_summary,
		)
	else:
		progress_summary = _get_dsa_progress_summary(request.session_id)

	response = _serialize_dsa_session_record(updated_record, problem=problem, progress_summary=progress_summary, report_state=report_state)
	response["message"] = message_entry
	response["message_channel"] = "debrief" if message_kind == "debrief" else "approach"
	response["next_stage"] = str(updated_record.get("stage") or next_stage)
	if brain_update.get("coaching_note"):
		response["coach_message"] = brain_update["coaching_note"]
	return response


def _require_parent_session(
	session_id: str,
	current_user: AuthenticatedUser,
) -> Mapping[str, Any]:
	resolved_session_id = session_id.strip()
	if not resolved_session_id:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="session_id is required.",
		)
	try:
		parent_session = get_session(resolved_session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	if parent_session is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="No interview session exists for the supplied session_id.",
		)
	return ensure_session_access(parent_session, current_user)


def _resolve_role_key(parent_session: Mapping[str, Any], requested_role_key: str | None) -> str:
	resolved_role_key = str(requested_role_key or parent_session.get("role_selected") or "").strip()
	if not resolved_role_key:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="A role must be selected before the DSA round can start.",
		)
	return resolved_role_key


def _require_dsa_record(
	*,
	session_id: str,
	question_number: int,
	current_user: AuthenticatedUser,
) -> tuple[Mapping[str, Any], CertifiedProblem]:
	_require_parent_session(session_id, current_user)
	try:
		record = get_dsa_session(session_id, question_number)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	if record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="No DSA session exists for the supplied session_id and question_number.",
		)
	problem = _resolve_record_problem(record)
	return record, problem


def _require_question_start_preconditions(session_id: str, question_number: int) -> None:
	if question_number != 2:
		return
	try:
		question_one_record = get_dsa_session(session_id, 1)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	if question_one_record is None or not _can_open_question_two(question_one_record):
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="Question 1 hidden tests must pass before question 2 can start.",
		)


def _derive_judge_status(execution_results: Mapping[str, Any]) -> str:
	status_text = str(execution_results.get("status") or "").casefold()
	compile_output = str(execution_results.get("compile_output") or "").strip()
	stderr = str(execution_results.get("stderr") or "").strip()
	passed_count = int(execution_results.get("passed_count") or 0)
	total_count = int(execution_results.get("total_count") or 0)

	if compile_output:
		return JudgeStatus.COMPILE_ERROR.value
	if stderr or "runtime error" in status_text:
		return JudgeStatus.RUNTIME_ERROR.value
	if "time limit" in status_text:
		return JudgeStatus.TIME_LIMIT.value
	if total_count > 0 and passed_count == total_count:
		return JudgeStatus.ACCEPTED.value
	if total_count > 0:
		return JudgeStatus.WRONG_ANSWER.value
	return JudgeStatus.NOT_RUN.value


def _resolve_dsa_language(value: Any) -> str:
	try:
		return normalize_dsa_language(None if value in {None, ""} else str(value))
	except SafetyViolationError as exc:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _resolve_supported_dsa_language(value: Any) -> str:
	resolved_language = _resolve_dsa_language(value)
	supported_language_keys = {language.key for language in SUPPORTED_DSA_LANGUAGES}
	if resolved_language not in supported_language_keys:
		return SUPPORTED_DSA_LANGUAGES[0].key
	return resolved_language


def _build_problem_starter_codes(problem: CertifiedProblem) -> dict[str, str]:
	starter_codes: dict[str, str] = {}
	primary_example = problem.examples[0] if problem.examples else None
	for language in SUPPORTED_DSA_LANGUAGES:
		if language.key == "python":
			starter_codes[language.key] = str(problem.starter_code or "") or build_editor_starter_code(language.key, prompt_title=problem.title)
		else:
			starter_codes[language.key] = build_editor_starter_code(
				language.key,
				prompt_title=problem.title,
				python_starter_code=problem.starter_code,
				example_input=primary_example.input_text if primary_example else None,
				example_output=primary_example.output_text if primary_example else None,
			)
	return starter_codes


def _build_language_context(
	*,
	problem: CertifiedProblem,
	state: Mapping[str, Any] | None,
	active_language: str,
	fallback_code_draft: str | None = None,
	override_code_draft: str | None = None,
) -> tuple[dict[str, Any], dict[str, str], str]:
	resolved_state = _coerce_mapping(state)
	starter_codes = _build_problem_starter_codes(problem)
	code_drafts = dict(starter_codes)
	raw_code_drafts = resolved_state.get("code_drafts")
	if isinstance(raw_code_drafts, Mapping):
		for raw_language, raw_code in raw_code_drafts.items():
			try:
				language_key = normalize_dsa_language(str(raw_language))
			except SafetyViolationError:
				continue
			text = str(raw_code or "")
			if text:
				code_drafts[language_key] = text
	if override_code_draft is not None:
		code_drafts[active_language] = override_code_draft
	elif fallback_code_draft and not isinstance(raw_code_drafts, Mapping):
		code_drafts[active_language] = fallback_code_draft
	resolved_state["current_language"] = active_language
	resolved_state["code_drafts"] = code_drafts
	current_code_draft = str(code_drafts.get(active_language) or starter_codes.get(active_language) or "")
	return resolved_state, starter_codes, current_code_draft


def _build_record_language_context(
	record: Mapping[str, Any],
	problem: CertifiedProblem,
	*,
	requested_language: str | None = None,
	current_code_override: str | None = None,
) -> tuple[dict[str, Any], dict[str, str], str]:
	state = _coerce_mapping(record.get("state_json"))
	active_language = _resolve_supported_dsa_language(requested_language if requested_language is not None else state.get("current_language"))
	fallback_code_draft = str(record.get("current_code_draft") or "") or None
	return _build_language_context(
		problem=problem,
		state=state,
		active_language=active_language,
		fallback_code_draft=fallback_code_draft,
		override_code_draft=current_code_override,
	)


def _ensure_record_language_state(
	record: Mapping[str, Any],
	problem: CertifiedProblem,
	*,
	requested_language: str | None = None,
) -> dict[str, Any]:
	state_json, _, _ = _build_record_language_context(record, problem, requested_language=requested_language)
	current_state = _coerce_mapping(record.get("state_json"))
	if current_state == state_json:
		return dict(record)
	try:
		return persist_dsa_state(
			session_id=str(record.get("session_id") or ""),
			question_number=int(record.get("question_number") or 0),
			state_json=state_json,
			expected_state_version=int(record.get("state_version") or state_json.get("state_version") or 1),
			stage=str(record.get("stage") or DSAStage.PROBLEM_SETUP.value),
			approach_text=str(record.get("approach_text") or "") or None,
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc


def _build_submission_payload(
	*,
	record: Mapping[str, Any],
	problem: CertifiedProblem,
	code: str,
	execution_results: Mapping[str, Any],
	submission_id: str,
	submission_kind: str,
	language: str,
) -> dict[str, Any]:
	return {
		"submission_id": submission_id,
		"submitted_at": _utcnow_iso(),
		"submission_kind": submission_kind,
		"problem_id": problem.problem_id,
		"question_number": int(record.get("question_number") or 0),
		"language": language,
		"code": code,
		"judge_status": _derive_judge_status(execution_results),
		"execution_results": dict(execution_results),
	}


def _append_submission_record(
	*,
	record: Mapping[str, Any],
	question_number: int,
	session_id: str,
	submission_payload: Mapping[str, Any],
	current_code_draft: str,
	last_submission_id: str,
	judge_status: str,
	execution_results: Mapping[str, Any],
	state_json: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	try:
		return append_dsa_submission(
			session_id=session_id,
			question_number=question_number,
			submission=submission_payload,
			current_code_draft=current_code_draft,
			last_submission_id=last_submission_id,
			last_judge_status=judge_status,
			execution_results=execution_results,
			state_json=state_json,
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc


def _select_post_submit_stage(execution_results: Mapping[str, Any]) -> str:
	total_count = int(execution_results.get("total_count") or 0)
	passed_count = int(execution_results.get("passed_count") or 0)
	if total_count > 0 and passed_count == total_count:
		return DSAStage.DEBRIEF.value
	return DSAStage.CODING.value


def _coerce_numeric_score(value: Any) -> float | None:
	if value in {None, ""}:
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _has_saved_debrief(record: Mapping[str, Any]) -> bool:
	state = _coerce_mapping(record.get("state_json"))
	debrief_messages = state.get("debrief_messages") or []
	return isinstance(debrief_messages, list) and len(debrief_messages) > 0


def _hidden_tests_passed(execution_results: Mapping[str, Any]) -> bool:
	total_count = int(execution_results.get("total_count") or 0)
	passed_count = int(execution_results.get("passed_count") or 0)
	return total_count > 0 and passed_count == total_count


def _question_counts_as_complete(record: Mapping[str, Any]) -> bool:
	stage = str(record.get("stage") or DSAStage.PROBLEM_SETUP.value)
	if bool(record.get("completed_at")) or stage == DSAStage.COMPLETE.value or _has_saved_debrief(record):
		return True
	return stage == DSAStage.DEBRIEF.value and _hidden_tests_passed(_coerce_mapping(record.get("execution_results")))


def _can_open_question_two(record: Mapping[str, Any]) -> bool:
	stage = str(record.get("stage") or DSAStage.PROBLEM_SETUP.value)
	if stage in {DSAStage.DEBRIEF.value, DSAStage.COMPLETE.value}:
		return True
	return _hidden_tests_passed(_coerce_mapping(record.get("execution_results")))


def _derive_question_score(record: Mapping[str, Any]) -> float | None:
	persisted_score = _coerce_numeric_score(record.get("total_score"))
	if persisted_score is not None:
		return round(persisted_score, 4)
	execution_results = _coerce_mapping(record.get("execution_results"))
	total_count = int(execution_results.get("total_count") or 0)
	passed_count = int(execution_results.get("passed_count") or 0)
	if total_count <= 0:
		return None
	return round(passed_count / total_count, 4)


def _build_question_dimension_scores(record: Mapping[str, Any], question_score: float | None) -> dict[str, Any]:
	execution_results = _coerce_mapping(record.get("execution_results"))
	state = _coerce_mapping(record.get("state_json"))
	passed_count = int(execution_results.get("passed_count") or 0)
	total_count = int(execution_results.get("total_count") or 0)
	return {
		"evaluation_mode": "execution_pass_ratio",
		"hidden_test_pass_ratio": question_score,
		"passed_count": passed_count,
		"total_count": total_count,
		"language": _resolve_dsa_language(state.get("current_language")),
		"debrief_saved": _has_saved_debrief(record),
	}


def _build_question_progress_entry(question_number: int, record: Mapping[str, Any] | None) -> dict[str, Any]:
	if record is None:
		return {
			"question_number": question_number,
			"started": False,
			"problem_id": None,
			"stage": None,
			"status": "not_started",
			"debrief_saved": False,
			"hidden_tests_passed": False,
			"completed": False,
			"score": None,
		}

	stage = str(record.get("stage") or DSAStage.PROBLEM_SETUP.value)
	execution_results = _coerce_mapping(record.get("execution_results"))
	total_count = int(execution_results.get("total_count") or 0)
	passed_count = int(execution_results.get("passed_count") or 0)
	hidden_tests_passed = total_count > 0 and passed_count == total_count
	debrief_saved = _has_saved_debrief(record)
	completed = _question_counts_as_complete(record)
	status = "complete" if completed else stage
	return {
		"question_number": question_number,
		"started": True,
		"problem_id": str(record.get("problem_id") or "") or None,
		"stage": stage,
		"status": status,
		"debrief_saved": debrief_saved,
		"hidden_tests_passed": hidden_tests_passed,
		"completed": completed,
		"score": _derive_question_score(record),
	}


def _build_dsa_progress_summary_from_records(records: list[Mapping[str, Any]]) -> dict[str, Any]:
	records_by_question = {
		int(record.get("question_number") or 0): record
		for record in records
		if int(record.get("question_number") or 0) in {1, 2}
	}
	question_entries = [
		_build_question_progress_entry(1, records_by_question.get(1)),
		_build_question_progress_entry(2, records_by_question.get(2)),
	]
	question_one_score = question_entries[0]["score"]
	question_two_score = question_entries[1]["score"]
	round_completed = bool(question_entries[0]["completed"] and question_entries[1]["completed"])
	aggregate_score = None
	if round_completed:
		aggregate_score = round(
			(float(question_one_score or 0.0) * 0.45) + (float(question_two_score or 0.0) * 0.55),
			4,
		)
	return {
		"completed_question_count": sum(1 for entry in question_entries if entry["completed"]),
		"round_completed": round_completed,
		"aggregate_score": aggregate_score,
		"questions": question_entries,
	}


def _get_dsa_progress_summary(session_id: str) -> dict[str, Any]:
	try:
		records = list_dsa_sessions(session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	return _build_dsa_progress_summary_from_records(records)


def _compute_overall_score_from_round_scores(round_scores: Mapping[str, Any]) -> float | None:
	values = [
		float(value)
		for value in round_scores.values()
		if _coerce_numeric_score(value) is not None
	]
	if not values:
		return None
	return round(sum(values) / len(values), 4)


def _persist_round_completion_state(
	*,
	session_id: str,
	progress_summary: Mapping[str, Any],
) -> dict[str, Any]:
	report_state = {
		"session_status": None,
		"persisted": False,
		"detail": None,
		"overall_score": None,
	}
	if not bool(progress_summary.get("round_completed")):
		return report_state

	update_session_status(session_id, "dsa_complete")
	report_state["session_status"] = "dsa_complete"

	try:
		existing_report = get_final_report(session_id)
		round_report: dict[str, Any] = {
			"status": "complete",
			"aggregate_score": progress_summary.get("aggregate_score"),
			"question_reports": [],
			"strengths": [],
			"recommendations": [],
		}
		try:
			records = list_dsa_sessions(session_id)
		except DatabaseClientError:
			records = []
		if records:
			problem_lookup: dict[int, CertifiedProblem] = {}
			for record in records:
				question_number = int(record.get("question_number") or 0)
				if question_number not in {1, 2}:
					continue
				try:
					problem_lookup[question_number] = _resolve_record_problem(record)
				except HTTPException:
					continue
			round_report = build_dsa_round_report(records=records, problem_lookup=problem_lookup)
		question_scores = {
			f"q{entry['question_number']}": entry.get("score")
			for entry in progress_summary.get("questions") or []
		}
		question_dimensions = {
			f"q{report.get('question_number')}": _coerce_mapping(report.get("dimension_scores"))
			for report in round_report.get("question_reports") or []
			if report.get("question_number")
		}
		existing_round_scores = _coerce_mapping(existing_report.get("round_scores")) if existing_report else {}
		existing_dimension_scores = _coerce_mapping(existing_report.get("dimension_scores")) if existing_report else {}
		existing_report_json = _coerce_mapping(existing_report.get("report_json")) if existing_report else {}
		round_scores = {
			**existing_round_scores,
			"dsa": progress_summary.get("aggregate_score"),
		}
		dimension_scores = {
			**existing_dimension_scores,
			"dsa": {
				"question_scores": question_scores,
				"completed_question_count": progress_summary.get("completed_question_count"),
				"scoring_mode": "weighted_execution_pass_ratio",
				"question_dimensions": question_dimensions,
			},
		}
		report_json = {
			**existing_report_json,
			"dsa": {
				**round_report,
				"status": "complete",
				"aggregate_score": progress_summary.get("aggregate_score"),
				"questions": progress_summary.get("questions") or [],
			},
		}
		overall_score = _compute_overall_score_from_round_scores(round_scores)
		upsert_final_report(
			session_id=session_id,
			overall_score=overall_score,
			round_scores=round_scores,
			dimension_scores=dimension_scores,
			report_json=report_json,
		)
		report_state["persisted"] = True
		report_state["overall_score"] = overall_score
	except DatabaseClientError as exc:
		report_state["detail"] = str(exc)
	return report_state


def _finalize_completed_question(
	*,
	record: Mapping[str, Any],
	session_id: str,
	question_number: int,
	problem: CertifiedProblem,
) -> dict[str, Any]:
	state = _coerce_mapping(record.get("state_json"))
	analysis = _coerce_mapping(state.get("latest_analysis"))
	if not analysis:
		analysis = analyze_submission_code(
			str(record.get("current_code_draft") or ""),
			language=_resolve_dsa_language(state.get("current_language")),
			problem=problem,
		)
	candidate_model = normalize_candidate_model(state.get("candidate_model"))
	evaluation = evaluate_dsa_question(
		record=record,
		analysis=analysis,
		candidate_model=candidate_model,
		problem=problem,
	)
	question_report = build_dsa_question_report(
		record=record,
		problem=problem,
		analysis=analysis,
		evaluation=evaluation,
	)
	dimension_scores = {
		"approach_quality": evaluation["approach_quality"],
		"code_correctness": evaluation["code_correctness"],
		"complexity_awareness": evaluation["complexity_awareness"],
		"followup_depth": evaluation["followup_depth"],
		"communication": evaluation["communication"],
		"scoring_mode": evaluation["scoring_mode"],
		"pass_ratio": evaluation["pass_ratio"],
		"penalties": _coerce_mapping(evaluation.get("penalties")),
		"analysis": analysis,
		"candidate_model": candidate_model,
		"feedback_summary": evaluation.get("feedback_summary"),
		"question_report": question_report,
	}
	try:
		return complete_dsa_session(
			session_id=session_id,
			question_number=question_number,
			final_code=str(record.get("current_code_draft") or "") or None,
			execution_results=_coerce_mapping(record.get("execution_results")),
			dimension_scores=dimension_scores,
			total_score=evaluation["total_score"],
			completed_at=_utcnow_iso(),
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc


def _maybe_complete_last_question_after_submission(
	*,
	record: Mapping[str, Any],
	session_id: str,
	question_number: int,
	problem: CertifiedProblem,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
	stage = str(record.get("stage") or DSAStage.CODING.value)
	if question_number != TOTAL_DSA_QUESTIONS or stage != DSAStage.DEBRIEF.value:
		return dict(record), None, None
	completed_record = _finalize_completed_question(
		record=record,
		session_id=session_id,
		question_number=question_number,
		problem=problem,
	)
	progress_summary = _get_dsa_progress_summary(session_id)
	report_state = _persist_round_completion_state(
		session_id=session_id,
		progress_summary=progress_summary,
	)
	return completed_record, progress_summary, report_state


def _resolve_message_kind(current_stage: str, requested_kind: str | None) -> str:
	stage = str(current_stage or "").strip()
	normalized_kind = str(requested_kind or "").strip().casefold()
	if not normalized_kind:
		normalized_kind = "debrief" if stage == DSAStage.DEBRIEF.value else "approach"
	if normalized_kind not in {"clarification", "approach", "debrief"}:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="message_kind must be clarification, approach, or debrief.",
		)
	if stage == DSAStage.COMPLETE.value:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="This DSA session is already complete.",
		)
	if normalized_kind == "debrief" and stage != DSAStage.DEBRIEF.value:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="Debrief messages are only allowed during the debrief stage.",
		)
	if normalized_kind == "clarification" and stage != DSAStage.PROBLEM_SETUP.value:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="Clarification messages are only allowed during problem setup.",
		)
	if normalized_kind in {"clarification", "approach"} and stage == DSAStage.DEBRIEF.value:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="Approach messages are not allowed after the session enters debrief.",
		)
	return normalized_kind


def _select_post_message_stage(current_stage: str, message_kind: str) -> str:
	if current_stage == DSAStage.PROBLEM_SETUP.value and message_kind == "approach":
		return DSAStage.APPROACH_DISCUSSION.value
	return current_stage


def _build_approach_text(messages: list[Mapping[str, Any]]) -> str:
	fragments = [
		str(message.get("text") or "").strip()
		for message in messages
		if str(message.get("text") or "").strip()
	]
	return "\n\n".join(fragments[-8:])


def _apply_post_submit_state(
	*,
	record: Mapping[str, Any],
	session_id: str,
	question_number: int,
	problem: CertifiedProblem,
	next_stage: str,
) -> dict[str, Any]:
	state = _coerce_mapping(record.get("state_json"))
	analysis = analyze_submission_code(
		str(record.get("current_code_draft") or ""),
		language=_resolve_dsa_language(state.get("current_language")),
		problem=problem,
	)
	brain_update = apply_submission_intelligence(
		state=state,
		current_stage=str(record.get("stage") or state.get("stage") or DSAStage.CODING.value),
		analysis=analysis,
		execution_results=_coerce_mapping(record.get("execution_results")),
		problem=problem,
	)
	state = _coerce_mapping(brain_update.get("state"))
	current_stage_value = str(record.get("stage") or state.get("stage") or DSAStage.CODING.value)
	resolved_next_stage = str(brain_update.get("next_stage") or next_stage)
	if resolved_next_stage != current_stage_value:
		state["stage_started_at"] = _utcnow_iso()
	state["stage"] = resolved_next_stage
	state["awaiting_user_input"] = True
	state["last_submission_id"] = record.get("last_submission_id")
	state["last_judge_status"] = record.get("last_judge_status")
	if resolved_next_stage == DSAStage.OPTIMIZATION.value:
		state["optimization_used"] = True
	try:
		return persist_dsa_state(
			session_id=session_id,
			question_number=question_number,
			state_json=state,
			expected_state_version=int(record.get("state_version") or 0),
			stage=resolved_next_stage,
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc


def _resolve_record_problem(record: Mapping[str, Any]) -> CertifiedProblem:
	problem_id = str(record.get("problem_id") or "").strip()
	if not problem_id:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="Stored DSA session is missing its problem_id.",
		)
	try:
		return get_certified_problem(problem_id)
	except ProblemSelectionError as exc:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail=str(exc),
		) from exc


def _serialize_dsa_session_record(
	record: Mapping[str, Any],
	*,
	problem: CertifiedProblem,
	progress_summary: Mapping[str, Any] | None = None,
	report_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	session_id = str(record.get("session_id") or "")
	resolved_progress_summary = dict(progress_summary) if isinstance(progress_summary, Mapping) else _get_dsa_progress_summary(session_id)
	state_json, starter_codes, current_code_draft = _build_record_language_context(record, problem)
	current_language = _resolve_supported_dsa_language(state_json.get("current_language"))
	problem_payload = serialize_problem(problem, include_private=False)
	problem_payload["starter_code"] = starter_codes.get(current_language) or problem_payload.get("starter_code")
	problem_payload["starter_codes"] = starter_codes
	return {
		"session_id": session_id,
		"question_number": int(record.get("question_number") or 0),
		"problem_id": problem.problem_id,
		"stage": str(record.get("stage") or DSAStage.PROBLEM_SETUP.value),
		"state_json": state_json,
		"candidate_model": _coerce_mapping(state_json.get("candidate_model")),
		"analysis": _coerce_mapping(state_json.get("latest_analysis")),
		"coaching_note": str(state_json.get("last_coaching_note") or "") or None,
		"deadline_at": record.get("deadline_at"),
		"approach_text": record.get("approach_text"),
		"language": current_language,
		"supported_languages": SUPPORTED_DSA_LANGUAGE_OPTIONS,
		"current_code_draft": current_code_draft,
		"all_code_submissions": list(record.get("all_code_submissions") or []),
		"execution_results": _coerce_mapping(record.get("execution_results")),
		"dimension_scores": _coerce_mapping(record.get("dimension_scores")),
		"final_code": str(record.get("final_code") or ""),
		"completed_at": record.get("completed_at"),
		"last_submission_id": record.get("last_submission_id"),
		"last_judge_status": record.get("last_judge_status"),
		"total_score": record.get("total_score"),
		"progress_summary": resolved_progress_summary,
		"report_state": dict(report_state) if isinstance(report_state, Mapping) else None,
		"problem": problem_payload,
	}
