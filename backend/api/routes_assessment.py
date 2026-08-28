"""Pre-interview assessment REST routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.auth import AuthenticatedUser, ensure_session_access, require_current_user
from backend.assessment import (
	AssessmentBankError,
	AssessmentBatch,
	build_assessment_batch,
	normalize_assessment_role_key,
)
from backend.database.queries import (
	create_assessment_session,
	get_assessment_session as fetch_assessment_session,
	get_session,
	persist_assessment_session,
)
from backend.database.db_errors import RecordNotFoundError, DatabaseClientError

router = APIRouter(prefix="/assessment", tags=["assessment"])


def _utcnow_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


class AssessmentStartRequest(BaseModel):
	session_id: str
	role_key: str | None = None
	total_questions: int = Field(default=8, ge=4, le=20)
	force_restart: bool = False


class AssessmentAnswerRequest(BaseModel):
	session_id: str
	question_id: str
	selected_option_id: str = Field(min_length=1, max_length=32)


@router.get("/health")
def assessment_health() -> dict[str, str]:
	return {"status": "ok"}


@router.post("/start")
def start_assessment(
	request: AssessmentStartRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	parent_session = _require_parent_session(request.session_id, current_user)
	role_key = _resolve_role_key(parent_session, request.role_key)

	try:
		existing_record = fetch_assessment_session(request.session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	if existing_record is not None and not request.force_restart:
		existing_role_key = str(existing_record.get("role_key") or "").strip()
		existing_total_questions = int(existing_record.get("total_questions") or 0)
		if existing_role_key != role_key or existing_total_questions != request.total_questions:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail=(
					"An assessment session already exists with different role or question count. "
					"Set force_restart=true to rebuild it."
				),
			)
		response = _serialize_assessment_record(existing_record)
		response["created_assessment_session"] = False
		return response

	try:
		batch = build_assessment_batch(
			session_id=request.session_id,
			role_key=role_key,
			total_questions=request.total_questions,
		)
		record = create_assessment_session(
			session_id=request.session_id,
			role_key=role_key,
			total_questions=request.total_questions,
			batch_json=_build_batch_snapshot(batch),
			state_json=_build_initial_state(total_questions=request.total_questions),
			status="in_progress",
			answered_count=0,
			correct_count=0,
			score_percent=0.0,
			started_at=_utcnow_iso(),
		)
	except AssessmentBankError as exc:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail=str(exc),
		) from exc
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	response = _serialize_assessment_record(record)
	response["created_assessment_session"] = True
	return response


@router.get("/session/{session_id}")
def get_assessment_session_route(
	session_id: str,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	_require_parent_session(session_id, current_user)
	try:
		record = fetch_assessment_session(session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	if record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="No assessment session exists for the supplied session_id.",
		)
	return _serialize_assessment_record(record)


@router.post("/submit")
def submit_assessment_answer(
	request: AssessmentAnswerRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	_require_parent_session(request.session_id, current_user)
	try:
		record = fetch_assessment_session(request.session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	if record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="No assessment session exists for the supplied session_id.",
		)

	if str(record.get("status") or "") == "completed":
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="This assessment session is already completed.",
		)

	batch_snapshot = _require_mapping(
		record.get("batch_json"),
		error_message="Stored assessment batch is malformed.",
	)
	question_snapshots = _require_question_snapshots(batch_snapshot)
	question_lookup = {
		str(question.get("question_id") or ""): question
		for question in question_snapshots
	}
	question_snapshot = question_lookup.get(request.question_id)
	if question_snapshot is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="The supplied question_id does not belong to this assessment session.",
		)

	option_ids = {
		str(option.get("id") or "")
		for option in _require_option_snapshots(question_snapshot)
	}
	if request.selected_option_id not in option_ids:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="selected_option_id is not valid for the supplied question_id.",
		)

	state = _require_mapping(
		record.get("state_json"),
		error_message="Stored assessment state is malformed.",
	)
	answers = _clone_answers(state.get("answers"))
	if request.question_id in answers:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="An answer for this question has already been submitted.",
		)

	answered_at = _utcnow_iso()
	is_correct = request.selected_option_id == str(question_snapshot.get("correct_option_id") or "")
	answers[request.question_id] = {
		"selected_option_id": request.selected_option_id,
		"is_correct": is_correct,
		"answered_at": answered_at,
	}
	answered_question_ids = [
		question_id
		for question_id in state.get("answered_question_ids") or []
		if isinstance(question_id, str) and question_id
	]
	answered_question_ids.append(request.question_id)
	answered_count = len(answers)
	correct_count = sum(
		1 for answer in answers.values() if bool(answer.get("is_correct"))
	)
	total_questions = int(record.get("total_questions") or len(question_snapshots))
	score_percent = round((correct_count / total_questions) * 100, 2) if total_questions else 0.0
	current_question_index = _find_next_question_index(question_snapshots, answers)
	assessment_status = "completed" if answered_count >= total_questions else "in_progress"
	completed_at = answered_at if assessment_status == "completed" else None
	updated_state = {
		"answers": answers,
		"answered_question_ids": answered_question_ids,
		"current_question_index": current_question_index,
		"last_answered_question_id": request.question_id,
		"last_answered_at": answered_at,
	}

	try:
		updated_record = persist_assessment_session(
			session_id=request.session_id,
			state_json=updated_state,
			status=assessment_status,
			answered_count=answered_count,
			correct_count=correct_count,
			score_percent=score_percent,
			completed_at=completed_at,
		)
	except (RecordNotFoundError, DatabaseClientError) as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	response = _serialize_assessment_record(updated_record)
	response["submitted_result"] = {
		"question_id": request.question_id,
		"selected_option_id": request.selected_option_id,
		"correct_option_id": str(question_snapshot.get("correct_option_id") or ""),
		"is_correct": is_correct,
		"explanation": str(question_snapshot.get("explanation") or ""),
	}
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
			detail="The supplied session_id does not exist.",
		)
	return ensure_session_access(parent_session, current_user)


def _resolve_role_key(parent_session: Mapping[str, Any], requested_role_key: str | None) -> str:
	persisted_role_key = normalize_assessment_role_key(
		str(parent_session.get("role_selected") or "").strip()
	)
	provided_role_key = normalize_assessment_role_key(str(requested_role_key or "").strip())
	if provided_role_key and persisted_role_key and provided_role_key != persisted_role_key:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail=(
				"The supplied role_key does not match the role already selected for this session. "
				"Use the persisted role or re-select the role before starting the assessment."
			),
		)
	resolved_role_key = provided_role_key or persisted_role_key
	if not resolved_role_key:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="A role must be selected before the assessment can start.",
		)
	return resolved_role_key


def _build_batch_snapshot(batch: AssessmentBatch) -> dict[str, Any]:
	explanations = _build_explanations_map()
	return {
		"role_families": list(batch.role_families),
		"domains": list(batch.domains),
		"questions": [
			{
				"question_id": question.question_id,
				"prompt": question.prompt,
				"question_type": question.question_type,
				"topic": question.topic,
				"difficulty": question.difficulty,
				"skill_tag": question.skill_tag,
				"options": [dict(option) for option in question.options],
				"correct_option_id": question.correct_option_id,
				"option_order": list(question.option_order),
				"selection_source": question.selection_source,
				"is_core": question.is_core,
				"explanation": explanations.get(question.question_id, ""),
			}
			for question in batch.questions
		],
	}


def _build_explanations_map() -> dict[str, str]:
	from backend.assessment.question_bank import load_question_bank

	return {
		question.question_id: question.explanation
		for question in load_question_bank()
	}


def _build_initial_state(*, total_questions: int) -> dict[str, Any]:
	return {
		"answers": {},
		"answered_question_ids": [],
		"current_question_index": 0,
		"last_answered_question_id": None,
		"last_answered_at": None,
		"total_questions": total_questions,
	}


def _serialize_assessment_record(record: Mapping[str, Any]) -> dict[str, Any]:
	batch_snapshot = _require_mapping(
		record.get("batch_json"),
		error_message="Stored assessment batch is malformed.",
	)
	state = _require_mapping(
		record.get("state_json"),
		error_message="Stored assessment state is malformed.",
	)
	question_snapshots = _require_question_snapshots(batch_snapshot)
	answers = _clone_answers(state.get("answers"))
	next_question_id = _find_next_question_id(question_snapshots, answers)
	return {
		"session_id": str(record.get("session_id") or ""),
		"role_key": str(record.get("role_key") or ""),
		"status": str(record.get("status") or "in_progress"),
		"started_at": record.get("started_at"),
		"completed_at": record.get("completed_at"),
		"total_questions": int(record.get("total_questions") or len(question_snapshots)),
		"answered_count": int(record.get("answered_count") or len(answers)),
		"correct_count": int(record.get("correct_count") or 0),
		"score_percent": float(record.get("score_percent") or 0.0),
		"current_question_index": int(state.get("current_question_index") or 0),
		"next_question_id": next_question_id,
		"role_families": list(batch_snapshot.get("role_families") or []),
		"domains": list(batch_snapshot.get("domains") or []),
		"questions": [
			_serialize_question_snapshot(question, answers)
			for question in question_snapshots
		],
	}


def _serialize_question_snapshot(
	question_snapshot: Mapping[str, Any],
	answers: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
	question_id = str(question_snapshot.get("question_id") or "")
	answer = answers.get(question_id)
	payload = {
		"question_id": question_id,
		"prompt": str(question_snapshot.get("prompt") or ""),
		"question_type": str(question_snapshot.get("question_type") or ""),
		"topic": str(question_snapshot.get("topic") or ""),
		"difficulty": str(question_snapshot.get("difficulty") or ""),
		"skill_tag": str(question_snapshot.get("skill_tag") or ""),
		"options": [
			{"id": str(option.get("id") or ""), "text": str(option.get("text") or "")}
			for option in _require_option_snapshots(question_snapshot)
		],
		"selection_source": str(question_snapshot.get("selection_source") or ""),
		"is_core": bool(question_snapshot.get("is_core", False)),
		"selected_option_id": None,
		"answered": False,
	}
	if answer is not None:
		payload["selected_option_id"] = str(answer.get("selected_option_id") or "")
		payload["answered"] = True
		payload["was_correct"] = bool(answer.get("is_correct"))
		payload["answered_at"] = answer.get("answered_at")
	return payload


def _require_mapping(value: object, *, error_message: str) -> dict[str, Any]:
	if not isinstance(value, Mapping):
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail=error_message,
		)
	return dict(value)


def _require_question_snapshots(batch_snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
	questions = batch_snapshot.get("questions")
	if not isinstance(questions, list) or not questions:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Stored assessment batch has no questions.",
		)
	result: list[dict[str, Any]] = []
	for question in questions:
		if not isinstance(question, Mapping):
			raise HTTPException(
				status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
				detail="Stored assessment batch contains a malformed question entry.",
			)
		result.append(dict(question))
	return result


def _require_option_snapshots(question_snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
	options = question_snapshot.get("options")
	if not isinstance(options, list) or len(options) < 2:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Stored assessment question options are malformed.",
		)
	result: list[dict[str, Any]] = []
	for option in options:
		if not isinstance(option, Mapping):
			raise HTTPException(
				status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
				detail="Stored assessment question contains a malformed option.",
			)
		result.append(dict(option))
	return result


def _clone_answers(value: object) -> dict[str, dict[str, Any]]:
	if not isinstance(value, Mapping):
		return {}
	answers: dict[str, dict[str, Any]] = {}
	for question_id, payload in value.items():
		if not isinstance(question_id, str) or not question_id:
			continue
		if not isinstance(payload, Mapping):
			continue
		answers[question_id] = dict(payload)
	return answers


def _find_next_question_index(
	question_snapshots: list[Mapping[str, Any]],
	answers: Mapping[str, Mapping[str, Any]],
) -> int:
	for index, question in enumerate(question_snapshots):
		question_id = str(question.get("question_id") or "")
		if question_id and question_id not in answers:
			return index
	return len(question_snapshots)


def _find_next_question_id(
	question_snapshots: list[Mapping[str, Any]],
	answers: Mapping[str, Mapping[str, Any]],
) -> str | None:
	index = _find_next_question_index(question_snapshots, answers)
	if index >= len(question_snapshots):
		return None
	return str(question_snapshots[index].get("question_id") or "") or None


__all__ = ["router"]
