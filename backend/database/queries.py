"""Domain-level query helpers for interview simulator persistence.

This module provides a thin abstraction over MongoRepository, exposing
domain-specific functions for use by service and API layers. All functions
should use the process-wide repository instance from get_repository().
"""

from typing import Any, Mapping, Sequence
from .db_errors import (
	DatabaseClientError,
	DSAStage,
	JudgeStatus,
	RecordNotFoundError,
)
from .mongo_client import get_repository

# Session domain
def create_session(user_id: str, role_selected: str | None = None, status: str = "created") -> dict[str, Any]:
	repo = get_repository()
	return repo.create_session(user_id=user_id, role_selected=role_selected, status=status)

def get_session(session_id: str) -> dict[str, Any] | None:
	repo = get_repository()
	return repo.get_session(session_id)

def list_sessions_for_user(user_id: str) -> list[dict[str, Any]]:
	repo = get_repository()
	return repo.list_sessions_for_user(user_id=user_id)

def update_session_status(session_id: str, status: str) -> dict[str, Any]:
	repo = get_repository()
	return repo.update_session_status(session_id=session_id, status=status)

def update_session_role_selected(session_id: str, role_selected: str) -> dict[str, Any]:
	repo = get_repository()
	return repo.update_session_role_selected(
		session_id=session_id,
		role_selected=role_selected,
	)

# Resume domain
def save_resume_data(session_id: str, raw_text: str, parsed_json: Mapping[str, Any]) -> dict[str, Any]:
	repo = get_repository()
	return repo.save_resume_data(session_id=session_id, raw_text=raw_text, parsed_json=parsed_json)

def get_resume_data(session_id: str) -> dict[str, Any] | None:
	repo = get_repository()
	return repo.fetch_one("resume_data", filters={"session_id": session_id})

# Role matching domain
def save_role_matches(
	session_id: str,
	matches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
	repo = get_repository()
	payloads = [{"session_id": session_id, **dict(match)} for match in matches]
	if not payloads:
		return []
	return repo.insert_many("role_matches", payloads)

# Interview responses domain
def save_interview_response(payload: Mapping[str, Any]) -> dict[str, Any]:
	repo = get_repository()
	return repo.save_interview_response(payload)

def list_interview_responses(session_id: str, round: str) -> list[dict[str, Any]]:
	repo = get_repository()
	return repo.list_interview_responses(session_id=session_id, round=round)

# Assessment session domain
def create_assessment_session(
	session_id: str,
	role_key: str,
	total_questions: int,
	batch_json: Mapping[str, Any],
	state_json: Mapping[str, Any],
	status: str = "in_progress",
	answered_count: int = 0,
	correct_count: int = 0,
	score_percent: float = 0.0,
	started_at: str | None = None,
	completed_at: str | None = None,
) -> dict[str, Any]:
	repo = get_repository()
	return repo.create_assessment_session(
		session_id=session_id,
		role_key=role_key,
		total_questions=total_questions,
		batch_json=batch_json,
		state_json=state_json,
		status=status,
		answered_count=answered_count,
		correct_count=correct_count,
		score_percent=score_percent,
		started_at=started_at,
		completed_at=completed_at,
	)

def get_assessment_session(session_id: str) -> dict[str, Any] | None:
	repo = get_repository()
	return repo.get_assessment_session(session_id=session_id)

def delete_assessment_session(session_id: str) -> int:
	repo = get_repository()
	return repo.delete_assessment_session(session_id=session_id)

def persist_assessment_session(
	session_id: str,
	state_json: Mapping[str, Any],
	status: str,
	answered_count: int,
	correct_count: int,
	score_percent: float,
	completed_at: str | None = None,
) -> dict[str, Any]:
	repo = get_repository()
	return repo.persist_assessment_session(
		session_id=session_id,
		state_json=state_json,
		status=status,
		answered_count=answered_count,
		correct_count=correct_count,
		score_percent=score_percent,
		completed_at=completed_at,
	)

# Interview round contexts domain
def save_interview_contexts(session_id: str, contexts: Mapping[str, Any]) -> list[dict[str, Any]]:
	"""Upsert per-round contexts (hr, technical, project_discussion) for a session."""
	repo = get_repository()
	return repo.save_interview_contexts(session_id=session_id, contexts=contexts)

def get_interview_context(session_id: str, round: str) -> dict[str, Any] | None:
	"""Fetch stored context dict for one round (hr / technical / project_discussion)."""
	repo = get_repository()
	return repo.get_interview_context(session_id=session_id, round=round)

def get_all_interview_contexts(session_id: str) -> dict[str, Any]:
	"""Fetch all stored round contexts keyed by round name."""
	repo = get_repository()
	return repo.get_all_interview_contexts(session_id=session_id)

# Final report domain
def save_final_report(payload: Mapping[str, Any]) -> dict[str, Any]:
	repo = get_repository()
	return repo.save_final_report(payload)

def get_final_report(session_id: str) -> dict[str, Any] | None:
	repo = get_repository()
	return repo.get_final_report(session_id=session_id)

def upsert_final_report(
	session_id: str,
	overall_score: float | int | None = None,
	round_scores: Mapping[str, Any] | None = None,
	dimension_scores: Mapping[str, Any] | None = None,
	report_json: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	repo = get_repository()
	return repo.upsert_final_report(
		session_id=session_id,
		overall_score=overall_score,
		round_scores=round_scores,
		dimension_scores=dimension_scores,
		report_json=report_json,
	)

# DSA session domain
def create_dsa_session(session_id: str, problem_id: str, question_number: int, stage: DSAStage | str = DSAStage.PROBLEM_SETUP, state_json: Mapping[str, Any] | None = None, deadline_at: str | None = None, approach_text: str | None = None, current_code_draft: str | None = None) -> dict[str, Any]:
	repo = get_repository()
	return repo.create_dsa_session(
		session_id=session_id,
		problem_id=problem_id,
		question_number=question_number,
		stage=stage,
		state_json=state_json,
		deadline_at=deadline_at,
		approach_text=approach_text,
		current_code_draft=current_code_draft,
	)

def get_dsa_session(session_id: str, question_number: int) -> dict[str, Any] | None:
	repo = get_repository()
	return repo.get_dsa_session(session_id=session_id, question_number=question_number)

def list_dsa_sessions(session_id: str) -> list[dict[str, Any]]:
	repo = get_repository()
	return repo.list_dsa_sessions(session_id=session_id)

def delete_dsa_sessions(session_id: str) -> int:
	repo = get_repository()
	return repo.delete_dsa_sessions(session_id=session_id)

def persist_dsa_state(session_id: str, question_number: int, state_json: Mapping[str, Any], expected_state_version: int | None = None, stage: DSAStage | str | None = None, approach_text: str | None = None) -> dict[str, Any]:
	repo = get_repository()
	return repo.persist_dsa_state(
		session_id=session_id,
		question_number=question_number,
		state_json=state_json,
		expected_state_version=expected_state_version,
		stage=stage,
		approach_text=approach_text,
	)

def append_dsa_submission(session_id: str, question_number: int, submission: Mapping[str, Any], current_code_draft: str | None = None, last_submission_id: str | None = None, last_judge_status: str | None = None, execution_results: Mapping[str, Any] | None = None, state_json: Mapping[str, Any] | None = None) -> dict[str, Any]:
	repo = get_repository()
	return repo.append_dsa_submission(
		session_id=session_id,
		question_number=question_number,
		submission=submission,
		current_code_draft=current_code_draft,
		last_submission_id=last_submission_id,
		last_judge_status=last_judge_status,
		execution_results=execution_results,
		state_json=state_json,
	)

def complete_dsa_session(session_id: str, question_number: int, final_code: str | None, execution_results: Mapping[str, Any] | None, dimension_scores: Mapping[str, Any] | None, total_score: float | int | None, completed_at: str | None = None) -> dict[str, Any]:
	repo = get_repository()
	return repo.complete_dsa_session(
		session_id=session_id,
		question_number=question_number,
		final_code=final_code,
		execution_results=execution_results,
		dimension_scores=dimension_scores,
		total_score=total_score,
		completed_at=completed_at,
	)


# Interview round session domain

def create_interview_round_session(
	session_id: str,
	round: str,
	role_key: str,
	questions_json: Mapping[str, Any] | None = None,
	started_at: str | None = None,
	deadline_at: str | None = None,
) -> dict[str, Any]:
	repo = get_repository()
	return repo.create_interview_round_session(
		session_id=session_id,
		round=round,
		role_key=role_key,
		questions_json=questions_json,
		started_at=started_at,
		deadline_at=deadline_at,
	)


def get_interview_round_session(session_id: str, round: str) -> dict[str, Any] | None:
	repo = get_repository()
	return repo.get_interview_round_session(session_id=session_id, round=round)

def delete_interview_round_session(session_id: str, round: str) -> int:
	repo = get_repository()
	return repo.delete_interview_round_session(session_id=session_id, round=round)

def delete_interview_responses(session_id: str, round: str) -> int:
	repo = get_repository()
	return repo.delete_interview_responses(session_id=session_id, round=round)

def delete_final_report(session_id: str) -> int:
	repo = get_repository()
	return repo.delete_final_report(session_id=session_id)


def advance_interview_question(
	session_id: str,
	round: str,
	new_index: int,
	difficulty_signal: float,
	questions_json: Mapping[str, Any] | None,
	expected_state_version: int,
) -> dict[str, Any]:
	repo = get_repository()
	return repo.advance_interview_question(
		session_id=session_id,
		round=round,
		new_index=new_index,
		difficulty_signal=difficulty_signal,
		questions_json=questions_json,
		expected_state_version=expected_state_version,
	)


def complete_interview_round(
	session_id: str,
	round: str,
	total_score: float,
	completed_at: str | None = None,
) -> dict[str, Any]:
	repo = get_repository()
	return repo.complete_interview_round(
		session_id=session_id,
		round=round,
		total_score=total_score,
		completed_at=completed_at,
	)
