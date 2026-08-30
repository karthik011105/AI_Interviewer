"""REST routes for the voice interview rounds (HR, Technical, Project Discussion).

Route summary:
    GET  /interview/health
    POST /interview/start          — create / resume a round session
    GET  /interview/session/{session_id}/{round}
	POST /interview/clarify        — answer a candidate clarification turn
    POST /interview/answer         — evaluate one answer and advance the index
    POST /interview/complete       — mark a round done and generate coaching feedback
    GET  /interview/responses/{session_id}/{round}
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.auth import AuthenticatedUser, ensure_session_access, require_current_user
from backend.config import get_settings
from backend.database.queries import (
	advance_interview_question,
	complete_interview_round,
	create_interview_round_session,
	get_interview_round_session,
	list_interview_responses,
	list_sessions_for_user,
	get_resume_data,
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
from backend.nlp.groq_client import (
	GroqCompletionError,
	GroqDependencyError,
	create_chat_completion,
)
from backend.nlp.feedback_generator import (
	FeedbackGeneratorLlmError,
	FeedbackGeneratorUnavailableError,
	generate_round_feedback,
)
from backend.nlp.interview_context import (
	build_hr_round_context,
	build_project_round_context,
	build_technical_round_context,
)
from backend.nlp.question_generator import (
	QuestionGeneratorLlmError,
	QuestionGeneratorUnavailableError,
	get_or_generate_questions,
	has_valid_non_technical_hr_questions,
	has_valid_conceptual_technical_questions,
)

router = APIRouter(prefix="/interview", tags=["interview"])

_VALID_ROUNDS = {"hr", "technical", "project_discussion"}


def _utcnow_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _coerce_mapping(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}


def _coerce_numeric(value: Any) -> float | None:
	try:
		if value is None:
			return None
		return float(value)
	except (TypeError, ValueError):
		return None


def _normalize_string_list(values: Any) -> list[str]:
	if not isinstance(values, list):
		return []

	result: list[str] = []
	seen: set[str] = set()
	for value in values:
		text = str(value or "").strip()
		if not text:
			continue
		key = text.casefold()
		if key in seen:
			continue
		seen.add(key)
		result.append(text)
	return result


def _build_skill_profile_summary(context: Mapping[str, Any]) -> dict[str, Any]:
	strong_skills = _normalize_string_list(context.get("strong_skills"))
	familiar_skills = _normalize_string_list(context.get("familiar_skills"))
	mentioned_skills = _normalize_string_list(context.get("mentioned_skills"))
	absent_skills = _normalize_string_list(context.get("absent_skills"))
	soft_gap_skills = _normalize_string_list(context.get("soft_gap_skills"))
	priority_focus_areas = _normalize_string_list(context.get("priority_focus_areas"))

	if not any((strong_skills, familiar_skills, mentioned_skills, absent_skills, soft_gap_skills, priority_focus_areas)):
		return {}

	return {
		"strong_count": len(strong_skills),
		"familiar_count": len(familiar_skills),
		"mentioned_count": len(mentioned_skills),
		"absent_count": len(absent_skills),
		"soft_gap_count": len(soft_gap_skills),
		"strong_skills": strong_skills,
		"familiar_skills": familiar_skills,
		"mentioned_skills": mentioned_skills,
		"absent_skills": absent_skills,
		"soft_gap_skills": soft_gap_skills,
		"priority_focus_areas": priority_focus_areas,
	}


def _summarize_covered_topics(entries: list[dict[str, Any]]) -> dict[str, Any]:
	tier_counts = {
		"strong": 0,
		"familiar": 0,
		"mentioned": 0,
		"absent": 0,
		"general": 0,
	}
	skills_by_tier: dict[str, list[str]] = {
		"strong": [],
		"familiar": [],
		"mentioned": [],
		"absent": [],
		"general": [],
	}

	for entry in entries:
		tier = str(entry.get("question_tier") or "general").strip().lower()
		if tier not in tier_counts:
			tier = "general"
		tier_counts[tier] += 1
		focus_skill = str(entry.get("focus_skill") or "").strip()
		if focus_skill and focus_skill not in skills_by_tier[tier]:
			skills_by_tier[tier].append(focus_skill)

	covered_focus_skills: list[str] = []
	for tier in ("absent", "familiar", "mentioned", "strong", "general"):
		for focus_skill in skills_by_tier[tier]:
			if focus_skill not in covered_focus_skills:
				covered_focus_skills.append(focus_skill)

	return {
		"total_questions_covered": len(entries),
		"tier_counts": tier_counts,
		"skills_by_tier": skills_by_tier,
		"covered_focus_skills": covered_focus_skills,
	}


def _prepare_questions_json_for_persistence(
	round_name: str,
	questions_json: Mapping[str, Any],
	context: Mapping[str, Any],
) -> dict[str, Any]:
	prepared = dict(questions_json)
	if round_name != "technical":
		return prepared

	prepared.setdefault("covered_topics", [])
	prepared.setdefault("covered_topic_summary", _summarize_covered_topics([]))
	skill_profile_summary = _build_skill_profile_summary(context)
	if skill_profile_summary:
		prepared["skill_profile_summary"] = skill_profile_summary
	return prepared


def _record_covered_topic(
	questions_json: Mapping[str, Any],
	round_name: str,
	question_index: int,
	question: Mapping[str, Any],
	*,
	answered_at: str,
) -> dict[str, Any]:
	updated = dict(questions_json)
	if round_name != "technical":
		return updated

	covered_topics = [
		dict(item)
		for item in (updated.get("covered_topics") or [])
		if isinstance(item, Mapping)
	]
	covered_topics = [
		item for item in covered_topics
		if int(item.get("question_index") or -1) != question_index
	]

	question_tier = str(question.get("question_tier") or "general").strip().lower()
	if question_tier not in {"strong", "familiar", "mentioned", "absent", "general"}:
		question_tier = "general"

	covered_topics.append({
		"question_index": question_index,
		"question_tier": question_tier,
		"focus_skill": str(question.get("focus_skill") or "").strip() or None,
		"question_text": str(question.get("question") or "").strip(),
		"answered_at": answered_at,
	})
	covered_topics.sort(key=lambda item: int(item.get("question_index") or 0))

	updated["covered_topics"] = covered_topics
	updated["covered_topic_summary"] = _summarize_covered_topics(covered_topics)
	return updated


def _merge_saved_response_history(
	questions_json: Mapping[str, Any],
	saved_response: Mapping[str, Any] | None,
) -> dict[str, Any]:
	updated = dict(questions_json)
	history = [
		dict(item)
		for item in (updated.get("_response_history") or [])
		if isinstance(item, Mapping)
	]
	if not isinstance(saved_response, Mapping):
		if history:
			updated["_response_history"] = history
		return updated

	candidate = dict(saved_response)
	candidate_id = str(candidate.get("id") or "").strip()
	if candidate_id:
		existing_ids = {
			str(item.get("id") or "").strip()
			for item in history
			if str(item.get("id") or "").strip()
		}
		if candidate_id not in existing_ids:
			history.append(candidate)
	elif candidate not in history:
		history.append(candidate)

	updated["_response_history"] = history
	return updated


def _extract_targeting_detail(
	response: Mapping[str, Any],
	round_record: Mapping[str, Any],
) -> dict[str, Any]:
	dimension_scores = _coerce_mapping(response.get("dimension_scores"))
	targeting_detail = _coerce_mapping(dimension_scores.get("targeting_detail"))
	question_index_value = targeting_detail.get("question_index")
	if question_index_value is None:
		question_id = str(response.get("question_id") or "").strip()
		_, _, maybe_index = question_id.rpartition("_")
		question_index_value = maybe_index

	try:
		question_index = int(question_index_value)
	except (TypeError, ValueError):
		question_index = -1

	question_tier = str(targeting_detail.get("question_tier") or "").strip().lower()
	focus_skill = str(targeting_detail.get("focus_skill") or "").strip()
	question_text = str(response.get("question_text") or "").strip()

	questions_json = _coerce_mapping(round_record.get("questions_json"))
	covered_topics = [
		dict(item)
		for item in (questions_json.get("covered_topics") or [])
		if isinstance(item, Mapping)
	]
	metadata_by_index = {
		int(item.get("question_index") or -1): item
		for item in covered_topics
		if int(item.get("question_index") or -1) >= 0
	}

	metadata = metadata_by_index.get(question_index)
	if metadata is None:
		question = _get_question_at_index(questions_json, "technical", question_index)
		if question is not None:
			metadata = {
				"question_tier": question.get("question_tier"),
				"focus_skill": question.get("focus_skill"),
				"question_text": question.get("question"),
			}

	if metadata is not None:
		if not question_tier:
			question_tier = str(metadata.get("question_tier") or "").strip().lower()
		if not focus_skill:
			focus_skill = str(metadata.get("focus_skill") or "").strip()
		if not question_text:
			question_text = str(metadata.get("question_text") or "").strip()

	if question_tier not in {"strong", "familiar", "mentioned", "absent", "general"}:
		question_tier = "general"

	return {
		"question_index": question_index,
		"question_tier": question_tier,
		"focus_skill": focus_skill or None,
		"question_text": question_text,
	}


def _extract_confidence_detail(response: Mapping[str, Any]) -> dict[str, Any]:
	dimension_scores = _coerce_mapping(response.get("dimension_scores"))
	communication_detail = _coerce_mapping(dimension_scores.get("communication_detail"))
	if communication_detail:
		return communication_detail
	return _coerce_mapping(response.get("communication"))


def _empty_targeting_analytics(*, scope: str, selected_role: str | None) -> dict[str, Any]:
	return {
		"scope": scope,
		"selected_role": selected_role,
		"sessions_analyzed": 0,
		"responses_analyzed": 0,
		"overall_average_score": None,
		"overall_average_confidence": None,
		"overall_hesitation_rate": None,
		"tier_stats": [],
		"lowest_scoring_tier": None,
		"highest_hesitation_tier": None,
	}


def _build_targeting_analytics_for_sessions(
	*,
	session_rows: list[Mapping[str, Any]],
	selected_role: str | None,
	current_role_only: bool,
) -> dict[str, Any]:
	role_key = str(selected_role or "").strip() or None
	technical_entries: list[dict[str, Any]] = []
	sessions_analyzed = 0

	for session_row in session_rows:
		session_id = str(session_row.get("id") or "").strip()
		if not session_id:
			continue
		if current_role_only and role_key:
			session_role = str(session_row.get("role_selected") or "").strip()
			if session_role != role_key:
				continue

		round_record = get_interview_round_session(session_id=session_id, round="technical")
		if round_record is None:
			continue

		responses = list_interview_responses(session_id=session_id, round="technical")
		if not responses:
			continue

		sessions_analyzed += 1
		for response in responses:
			targeting_detail = _extract_targeting_detail(response, round_record)
			confidence_detail = _extract_confidence_detail(response)
			confidence_score = _coerce_numeric(confidence_detail.get("confidence_score"))
			confidence_label = str(confidence_detail.get("confidence_label") or "").strip().lower()
			score = _coerce_numeric(response.get("score"))
			if score is None:
				continue
			technical_entries.append({
				"session_id": session_id,
				"question_tier": targeting_detail["question_tier"],
				"focus_skill": targeting_detail["focus_skill"],
				"score": score,
				"confidence_score": confidence_score,
				"hesitant": confidence_label == "hesitant",
			})

	scope = "same_role_saved_sessions" if current_role_only and role_key else "all_saved_technical_sessions"
	if not technical_entries:
		return _empty_targeting_analytics(scope=scope, selected_role=role_key)

	overall_average_score = round(
		sum(entry["score"] for entry in technical_entries) / len(technical_entries),
		4,
	)
	confidence_values = [
		entry["confidence_score"]
		for entry in technical_entries
		if entry["confidence_score"] is not None
	]
	overall_average_confidence = round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else None
	overall_hesitation_rate = round(
		sum(1 for entry in technical_entries if entry["hesitant"]) / len(technical_entries),
		4,
	)

	tier_buckets: dict[str, list[dict[str, Any]]] = {}
	for entry in technical_entries:
		tier_buckets.setdefault(entry["question_tier"], []).append(entry)

	tier_stats: list[dict[str, Any]] = []
	for tier, entries in tier_buckets.items():
		average_score = round(sum(entry["score"] for entry in entries) / len(entries), 4)
		score_drop_vs_overall = round(overall_average_score - average_score, 4)
		tier_confidence_values = [
			entry["confidence_score"]
			for entry in entries
			if entry["confidence_score"] is not None
		]
		average_confidence = round(sum(tier_confidence_values) / len(tier_confidence_values), 4) if tier_confidence_values else None
		hesitation_rate = round(
			sum(1 for entry in entries if entry["hesitant"]) / len(entries),
			4,
		)
		focus_skills: list[str] = []
		for entry in entries:
			focus_skill = str(entry.get("focus_skill") or "").strip()
			if focus_skill and focus_skill not in focus_skills:
				focus_skills.append(focus_skill)
		tier_stats.append({
			"tier": tier,
			"response_count": len(entries),
			"average_score": average_score,
			"score_delta_vs_overall": round(average_score - overall_average_score, 4),
			"score_drop_vs_overall": score_drop_vs_overall,
			"average_confidence": average_confidence,
			"hesitation_rate": hesitation_rate,
			"focus_skills": focus_skills[:4],
		})

	tier_stats.sort(key=lambda entry: entry["tier"])
	lowest_scoring_tier = max(tier_stats, key=lambda entry: (entry["score_drop_vs_overall"], entry["response_count"], entry["tier"]))
	highest_hesitation_tier = max(tier_stats, key=lambda entry: (entry["hesitation_rate"], entry["response_count"], entry["tier"]))

	return {
		"scope": scope,
		"selected_role": role_key,
		"sessions_analyzed": sessions_analyzed,
		"responses_analyzed": len(technical_entries),
		"overall_average_score": overall_average_score,
		"overall_average_confidence": overall_average_confidence,
		"overall_hesitation_rate": overall_hesitation_rate,
		"tier_stats": tier_stats,
		"lowest_scoring_tier": lowest_scoring_tier,
		"highest_hesitation_tier": highest_hesitation_tier,
	}


def _build_technical_targeting_analytics(
	*,
	user_id: str,
	selected_role: str | None,
) -> dict[str, Any]:
	session_rows = list_sessions_for_user(user_id)
	role_specific = _build_targeting_analytics_for_sessions(
		session_rows=session_rows,
		selected_role=selected_role,
		current_role_only=True,
	)
	if role_specific["responses_analyzed"] >= 3 or role_specific["sessions_analyzed"] >= 2:
		return role_specific
	return _build_targeting_analytics_for_sessions(
		session_rows=session_rows,
		selected_role=selected_role,
		current_role_only=False,
	)


# ---------------------------------------------------------------------------
# Auth helpers (mirrors routes_assessment.py exactly)
# ---------------------------------------------------------------------------


def _require_parent_session(
	session_id: str,
	current_user: AuthenticatedUser,
) -> dict[str, Any]:
	"""Load the parent session and verify ownership; raise 404 / 403 on failure."""
	try:
		session = get_session(session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	if session is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Session not found.",
		)

	ensure_session_access(session, current_user)
	return session


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class InterviewStartRequest(BaseModel):
	session_id: str
	round: str = Field(..., pattern="^(hr|technical|project_discussion)$")
	force_restart: bool = False


class InterviewAnswerRequest(BaseModel):
	session_id: str
	round: str = Field(..., pattern="^(hr|technical|project_discussion)$")
	question_index: int = Field(..., ge=0)
	answer_text: str = Field(..., min_length=1, max_length=4000)
	follow_up_asked: bool = False


class InterviewClarifyRequest(BaseModel):
	session_id: str
	round: str = Field(..., pattern="^(hr|technical|project_discussion)$")
	question_index: int = Field(..., ge=0)
	clarification_text: str = Field(..., min_length=1, max_length=1200)


class InterviewCompleteRequest(BaseModel):
	session_id: str
	round: str = Field(..., pattern="^(hr|technical|project_discussion)$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_parsed_resume(session_id: str) -> dict[str, Any]:
	"""Fetch parsed resume from MongoDB; raise 404 if not found."""
	try:
		resume_row = get_resume_data(session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	if resume_row is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="No parsed resume found for this session. Upload and parse a resume first.",
		)
	parsed = resume_row.get("parsed_json") or {}
	if not isinstance(parsed, dict):
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
			detail="Stored resume data is malformed.",
		)
	return parsed


def _build_round_context(
	round_name: str,
	parsed_resume: dict[str, Any],
	parent_session: dict[str, Any],
) -> dict[str, Any]:
	"""Build the round-specific context from a parsed resume and session data."""
	role_key = str(parent_session.get("role_selected") or "").strip() or None
	role_match: dict[str, Any] | None = None  # role_match detail not needed here

	if round_name == "hr":
		return build_hr_round_context(
			parsed_resume, selected_role=role_key, role_match=role_match
		)
	if round_name == "technical":
		return build_technical_round_context(
			parsed_resume, selected_role=role_key, role_match=role_match
		)
	# project_discussion
	return build_project_round_context(
		parsed_resume, selected_role=role_key, role_match=role_match
	)


def _get_question_list(questions_json: dict[str, Any], round_name: str) -> list[Any]:
	"""Extract the flat question list from questions_json for HR or Technical rounds."""
	return list(questions_json.get("questions") or [])


def _get_question_at_index(
	questions_json: dict[str, Any],
	round_name: str,
	index: int,
) -> dict[str, Any] | None:
	"""Return the question dict at the given index, or None if out of range."""
	if round_name == "project_discussion":
		# project_discussion stores a list of ProjectQuestionSet; flatten to get question
		all_qs: list[Any] = []
		for proj in (questions_json.get("projects") or []):
			all_qs.extend(proj.get("questions") or [])
		if index < 0 or index >= len(all_qs):
			return None
		return dict(all_qs[index])
	else:
		questions = list(questions_json.get("questions") or [])
		if index < 0 or index >= len(questions):
			return None
		return dict(questions[index])


def _total_question_count(questions_json: dict[str, Any], round_name: str) -> int:
	if round_name == "project_discussion":
		count = 0
		for proj in (questions_json.get("projects") or []):
			count += len(proj.get("questions") or [])
		return count
	return len(questions_json.get("questions") or [])


def _fetch_round_responses(session_id: str, round_name: str) -> list[dict[str, Any]]:
	"""Fetch all persisted responses for a round from MongoDB."""
	try:
		return list_interview_responses(session_id=session_id, round=round_name)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=f"Failed to fetch round responses: {exc}",
		) from exc


def _generate_clarification_reply(
	*,
	round_type: str,
	role_key: str | None,
	question_text: str,
	clarification_text: str,
) -> str:
	"""Generate a short interviewer clarification response.

	The response should clarify scope or assumptions without disclosing the
	full answer, algorithm, or ideal response.
	"""
	settings = get_settings().groq
	if settings is None:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Groq is not configured, so interactive clarification is unavailable.",
		)

	if round_type == "technical":
		system_prompt = (
			"You are a technical interviewer in a mock interview. "
			"The candidate is asking a clarification question about the interview prompt. "
			"Reply in 1 to 3 short sentences. Clarify scope, assumptions, inputs, or expected behavior only. "
			"Do not reveal the full solution, exact algorithm, code, or ideal answer. "
			"If the candidate asks for the answer directly, politely refuse and restate the intent of the prompt."
		)
	elif round_type == "hr":
		system_prompt = (
			"You are an HR interviewer in a mock interview. "
			"The candidate is asking a clarification question about the interview prompt. "
			"Reply in 1 to 3 short sentences. Clarify the question's framing, scope, or what kind of response is expected. "
			"Do not coach the candidate on what strengths to claim, what story to tell, or provide an ideal answer. "
			"If the candidate asks for the answer directly, politely refuse and restate the intent of the prompt."
		)
	elif round_type == "project_discussion":
		system_prompt = (
			"You are a technical interviewer discussing the candidate's own project in a mock interview. "
			"The candidate is asking a clarification question about the interview prompt. "
			"Reply in 1 to 3 short sentences. Clarify which project aspect, tradeoff, architecture decision, outcome, or implementation detail they should focus on. "
			"Do not invent missing project details, suggest design improvements, or provide an ideal answer. "
			"If the candidate asks for the answer directly, politely refuse and restate the intent of the prompt."
		)
	else:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail=f"Unsupported round '{round_type}' for clarification.",
		)

	messages = [
		{
			"role": "system",
			"content": system_prompt,
		},
		{
			"role": "user",
			"content": (
				f"Interview round: {round_type}\n"
				f"Role: {role_key or 'technical interview candidate'}\n"
				f"Interview question: {question_text}\n"
				f"Candidate clarification question: {clarification_text}\n\n"
				"Return only the interviewer response text."
			),
		},
	]

	try:
		completion = create_chat_completion(
			settings=settings,
			model=settings.answer_evaluator_model,
			temperature=0.2,
			messages=messages,
		)
	except GroqDependencyError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	except GroqCompletionError as exc:
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail=f"Clarification response failed: {exc}",
		) from exc

	response_text = (
		getattr(completion.choices[0].message, "content", "")
		if getattr(completion, "choices", None)
		else ""
	)
	response_text = str(response_text or "").strip()
	if not response_text:
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail="Clarification response was empty.",
		)

	return response_text


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health")
def interview_health() -> dict[str, str]:
	return {"status": "ok"}


@router.post("/start")
def start_interview_round(
	request: InterviewStartRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	"""Create or resume an interview round session.

	- If a round session already exists and is in_progress, returns cached questions.
	- If force_restart=True, regenerates questions unconditionally.
	"""
	parent_session = _require_parent_session(request.session_id, current_user)

	# Load existing round session (may be None)
	try:
		existing = get_interview_round_session(
			session_id=request.session_id, round=request.round
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	if existing is not None and not request.force_restart:
		round_status = str(existing.get("status") or "")
		if round_status == "complete":
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail=(
					"This interview round is already complete. "
					"Set force_restart=true to restart it."
				),
			)
		existing_questions_json = dict(existing.get("questions_json") or {})
		current_index = int(existing.get("current_question_index") or 0)
		if (
			(request.round == "technical" and (current_index > 0 or has_valid_conceptual_technical_questions(existing_questions_json)))
			or (request.round == "hr" and (current_index > 0 or has_valid_non_technical_hr_questions(existing_questions_json)))
			or request.round == "project_discussion"
		):
			# Resume in-progress round — return cached questions
			return {
				"created": False,
				"round_session": existing,
				"questions_json": existing_questions_json,
			}

	# Need to generate questions — load parsed resume
	parsed_resume = _get_parsed_resume(request.session_id)
	context = _build_round_context(request.round, parsed_resume, parent_session)

	cached_questions_json = None
	if existing and not request.force_restart:
		existing_questions_json = dict(existing.get("questions_json") or {})
		if (
			(request.round == "technical" and has_valid_conceptual_technical_questions(existing_questions_json))
			or (request.round == "hr" and has_valid_non_technical_hr_questions(existing_questions_json))
			or request.round == "project_discussion"
		):
			cached_questions_json = existing_questions_json
	difficulty_signal = float(
		(existing or {}).get("difficulty_signal") or 0.5
	)

	try:
		questions_json = get_or_generate_questions(
			request.round,
			context,
			cached_questions_json=cached_questions_json,
			difficulty_signal=difficulty_signal,
		)
	except QuestionGeneratorUnavailableError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	except QuestionGeneratorLlmError as exc:
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail=f"Question generation failed: {exc}",
		) from exc
	questions_json = _prepare_questions_json_for_persistence(request.round, questions_json, context)

	role_key = str(parent_session.get("role_selected") or "").strip() or "unknown"

	try:
		round_session = create_interview_round_session(
			session_id=request.session_id,
			round=request.round,
			role_key=role_key,
			questions_json=questions_json,
			started_at=_utcnow_iso(),
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	return {
		"created": True,
		"round_session": round_session,
		"questions_json": questions_json,
	}


@router.get("/session/{session_id}/{round}")
def get_interview_round(
	session_id: str,
	round: str,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	"""Fetch the current state of an interview round session."""
	if round not in _VALID_ROUNDS:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail=f"Invalid round '{round}'. Must be one of: {', '.join(sorted(_VALID_ROUNDS))}.",
		)
	_require_parent_session(session_id, current_user)

	try:
		record = get_interview_round_session(session_id=session_id, round=round)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	if record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail=f"No interview round session found for round '{round}'.",
		)

	return record


@router.get("/analytics/{session_id}/technical")
def get_technical_targeting_analytics(
	session_id: str,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	parent_session = _require_parent_session(session_id, current_user)
	selected_role = str(parent_session.get("role_selected") or "").strip() or None

	try:
		analytics = _build_technical_targeting_analytics(
			user_id=current_user.user_id,
			selected_role=selected_role,
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	return {
		"session_id": session_id,
		"round": "technical",
		"selected_role": selected_role,
		"analytics": analytics,
	}


@router.post("/clarify")
def clarify_interview_question(
	request: InterviewClarifyRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	"""Answer one candidate clarification turn for the current interview question."""
	parent_session = _require_parent_session(request.session_id, current_user)

	try:
		round_record = get_interview_round_session(
			session_id=request.session_id,
			round=request.round,
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	if round_record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Interview round session not found. Call /interview/start first.",
		)

	if str(round_record.get("status") or "") == "complete":
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="This interview round is already complete.",
		)

	if str((round_record.get("questions_json") or {}).get("mode") or "") == "dynamic":
		# A conversational round has no stable question index to answer against:
		# the interviewer may already have moved on. Accepting an answer here
		# would silently corrupt the transcript rather than fail loudly.
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="This round runs in conversational mode; answer over the WebSocket.",
		)

	persisted_index = int(round_record.get("current_question_index") or 0)
	if request.question_index != persisted_index:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail=(
				f"Question index mismatch. Expected {persisted_index}, "
				f"got {request.question_index}. Reload the session to sync."
			),
		)

	questions_json = _coerce_mapping(round_record.get("questions_json"))
	question = _get_question_at_index(questions_json, request.round, persisted_index)
	if question is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail=f"No question at index {persisted_index}.",
		)

	role_key = str(parent_session.get("role_selected") or "").strip() or None
	question_text = str(question.get("question") or "")
	response_text = _generate_clarification_reply(
		round_type=request.round,
		role_key=role_key,
		question_text=question_text,
		clarification_text=request.clarification_text,
	)

	return {
		"question_index": persisted_index,
		"clarification_text": request.clarification_text,
		"response_text": response_text,
	}


@router.post("/answer")
def submit_interview_answer(
	request: InterviewAnswerRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	"""Evaluate one answer, persist it, and advance the question index.

	The caller supplies the question_index they answered so the backend can
	validate it matches the persisted current_question_index.
	"""
	_require_parent_session(request.session_id, current_user)

	# Load round session
	try:
		round_record = get_interview_round_session(
			session_id=request.session_id, round=request.round
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	if round_record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Interview round session not found. Call /interview/start first.",
		)

	if str(round_record.get("status") or "") == "complete":
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="This interview round is already complete.",
		)

	persisted_index = int(round_record.get("current_question_index") or 0)
	if request.question_index != persisted_index:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail=(
				f"Question index mismatch. Expected {persisted_index}, "
				f"got {request.question_index}. Reload the session to sync."
			),
		)

	questions_json = round_record.get("questions_json") or {}
	question = _get_question_at_index(questions_json, request.round, persisted_index)
	if question is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail=f"No question at index {persisted_index}.",
		)

	question_text = str(question.get("question") or "")
	ideal_points: list[str] = list(question.get("ideal_points") or [])
	follow_up_text = str(question.get("follow_up") or "") if request.follow_up_asked else ""

	# Evaluate answer
	try:
		evaluation = evaluate_answer(
			question=question_text,
			ideal_points=ideal_points,
			follow_up=follow_up_text,
			answer_text=request.answer_text,
			round_type=request.round,
		)
	except EvaluatorUnavailableError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	except EvaluatorLlmError as exc:
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail=f"Answer evaluation failed: {exc}",
		) from exc

	# Persist response
	response_id = str(uuid.uuid4())
	now = _utcnow_iso()
	try:
		saved_response = save_interview_response({
			"id": response_id,
			"session_id": request.session_id,
			"round": request.round,
			"question_id": f"{request.round}_{persisted_index}",
			"question_text": question_text,
			"user_answer_text": request.answer_text,
			"audio_file_path": None,
			"score": evaluation["final_score"],
			"dimension_scores": {
				"groq": evaluation["groq_score"],
				"sbert": evaluation["sbert_score"],
				"communication": evaluation["communication_score"],
				"targeting_detail": {
					"question_index": persisted_index,
					"question_tier": str(question.get("question_tier") or "general").strip().lower(),
					"focus_skill": str(question.get("focus_skill") or "").strip() or None,
				},
				"concept_coverage": evaluation.get("concept_coverage"),
				"scoring_profile": evaluation.get("scoring_profile"),
				"rubric": evaluation["rubric"],
				"communication_detail": evaluation["communication"],
				"evaluation_mode": evaluation.get("evaluation_mode"),
				"fallback_reason": evaluation.get("fallback_reason"),
				"local_score": evaluation.get("local_score"),
			},
			"feedback": evaluation["feedback"],
			"created_at": now,
		})
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	# Compute updated difficulty signal (running average weighted toward new score)
	current_signal = float(round_record.get("difficulty_signal") or 0.5)
	new_signal = round((current_signal * 0.6 + evaluation["final_score"] * 0.4), 4)
	updated_questions_json = _record_covered_topic(
		_merge_saved_response_history(questions_json, saved_response),
		request.round,
		persisted_index,
		question,
		answered_at=now,
	)

	total_questions = _total_question_count(questions_json, request.round)
	next_index = persisted_index + 1
	is_last_question = next_index >= total_questions

	# Advance question index (optimistic concurrency)
	state_version = int(round_record.get("state_version") or 1)
	try:
		updated_round = advance_interview_question(
			session_id=request.session_id,
			round=request.round,
			new_index=next_index,
			difficulty_signal=new_signal,
			questions_json=updated_questions_json,
			expected_state_version=state_version,
		)
	except ConcurrentUpdateError:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="Concurrent update detected. Please reload the session.",
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	return {
		"saved_response_id": response_id,
		"question_index": persisted_index,
		"evaluation": evaluation,
		"next_question_index": next_index,
		"is_last_question": is_last_question,
		"total_questions": total_questions,
		"covered_topics": updated_questions_json.get("covered_topics") or [],
		"covered_topic_summary": updated_questions_json.get("covered_topic_summary") or {},
		"round_session": updated_round,
	}


@router.post("/complete")
def complete_round(
	request: InterviewCompleteRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	"""Mark a round complete and generate round-level coaching feedback."""
	_require_parent_session(request.session_id, current_user)

	try:
		round_record = get_interview_round_session(
			session_id=request.session_id, round=request.round
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	if round_record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Interview round session not found.",
		)

	if str(round_record.get("status") or "") == "complete":
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="This round is already marked complete.",
		)

	# Fetch all responses for this round
	responses = _fetch_round_responses(request.session_id, request.round)

	# Compute aggregate score from saved responses
	total_score = 0.0
	if responses:
		total_score = round(
			sum(float(r.get("score") or 0.0) for r in responses) / len(responses), 4
		)

	# Generate coaching feedback
	try:
		round_feedback = generate_round_feedback(request.round, responses)
	except FeedbackGeneratorUnavailableError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	except FeedbackGeneratorLlmError as exc:
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail=f"Feedback generation failed: {exc}",
		) from exc

	# Persist completion
	try:
		completed_record = complete_interview_round(
			session_id=request.session_id,
			round=request.round,
			total_score=total_score,
		)
	except (RecordNotFoundError, DatabaseClientError) as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	round_questions_json = _coerce_mapping(round_record.get("questions_json"))

	return {
		"round": request.round,
		"total_score": total_score,
		"response_count": len(responses),
		"covered_topics": round_questions_json.get("covered_topics") or [],
		"covered_topic_summary": round_questions_json.get("covered_topic_summary") or {},
		"skill_profile_summary": round_questions_json.get("skill_profile_summary") or {},
		"round_feedback": round_feedback,
		"round_session": completed_record,
	}


@router.get("/responses/{session_id}/{round}")
def get_round_responses(
	session_id: str,
	round: str,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	"""Fetch all persisted answers for a round (used by the report)."""
	if round not in _VALID_ROUNDS:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail=f"Invalid round '{round}'.",
		)
	_require_parent_session(session_id, current_user)
	responses = _fetch_round_responses(session_id, round)
	return {"round": round, "responses": responses, "count": len(responses)}
