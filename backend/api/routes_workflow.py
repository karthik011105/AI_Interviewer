"""Workflow reset routes for assessment, interview, DSA, and report state."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.auth import AuthenticatedUser, ensure_session_access, require_current_user
from backend.database.queries import (
	delete_assessment_session,
	delete_dsa_sessions,
	delete_final_report,
	delete_interview_responses,
	delete_interview_round_session,
	get_session,
	update_session_status,
)
from backend.database.db_errors import DatabaseClientError

router = APIRouter(prefix="/workflow", tags=["workflow"])

ROUND_RESET_ORDER = ("assessment", "technical", "dsa", "project_discussion", "hr", "report")
INTERVIEW_ROUNDS = ("technical", "project_discussion", "hr")
RESET_CASCADE: dict[str, tuple[str, ...]] = {
	"assessment": ROUND_RESET_ORDER,
	"technical": ("technical", "dsa", "project_discussion", "hr", "report"),
	"dsa": ("dsa", "project_discussion", "hr", "report"),
	"project_discussion": ("project_discussion", "hr", "report"),
	"hr": ("hr", "report"),
	"report": ("report",),
	"entire_interview": ROUND_RESET_ORDER,
}


class WorkflowResetRequest(BaseModel):
	session_id: str = Field(min_length=1)
	target: str = Field(
		...,
		pattern="^(assessment|technical|dsa|project_discussion|hr|report|entire_interview)$",
	)


def _require_parent_session(
	session_id: str,
	current_user: AuthenticatedUser,
) -> dict[str, Any]:
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


def _should_downgrade_session_status(cleared_targets: tuple[str, ...]) -> bool:
	return any(target in cleared_targets for target in ("assessment", "technical", "dsa"))


@router.post("/reset")
def reset_workflow_stage(
	request: WorkflowResetRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	parent_session = _require_parent_session(request.session_id, current_user)
	cleared_targets = RESET_CASCADE[request.target]
	summary: dict[str, Any] = {
		"assessment_session": 0,
		"dsa_sessions": 0,
		"final_reports": 0,
		"interview_round_sessions": {},
		"interview_responses": {},
	}

	try:
		if "assessment" in cleared_targets:
			summary["assessment_session"] = delete_assessment_session(request.session_id)

		for round_name in INTERVIEW_ROUNDS:
			if round_name not in cleared_targets:
				continue
			summary["interview_round_sessions"][round_name] = delete_interview_round_session(request.session_id, round_name)
			summary["interview_responses"][round_name] = delete_interview_responses(request.session_id, round_name)

		if "dsa" in cleared_targets:
			summary["dsa_sessions"] = delete_dsa_sessions(request.session_id)

		if "report" in cleared_targets:
			summary["final_reports"] = delete_final_report(request.session_id)

		next_session_status = str(parent_session.get("status") or "created")
		if _should_downgrade_session_status(cleared_targets):
			updated_session = update_session_status(request.session_id, "created")
			next_session_status = str(updated_session.get("status") or "created")
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	return {
		"session_id": request.session_id,
		"requested_target": request.target,
		"cleared_targets": list(cleared_targets),
		"session_status": next_session_status,
		"selected_role": parent_session.get("role_selected"),
		"summary": summary,
	}