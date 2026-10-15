"""Ending an interview early ("Finish interview").

Every round runs in a full-screen shell on the frontend; leaving full screen
has no consequence. What the shell does offer is "Finish interview": the
candidate ends the whole interview now. That is stored as ``interview_ended``
on the session, and from then on the round endpoints refuse to continue (see
``ensure_interview_open``) while the report stays available.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.auth import AuthenticatedUser, ensure_session_access, require_current_user
from backend.database.db_errors import DatabaseClientError
from backend.database.queries import get_session, mark_interview_finished

router = APIRouter(prefix="/interview-end", tags=["interview-end"])

FINISHED_MESSAGE = "You finished this interview early. You can still view your report."


class FinishInterviewRequest(BaseModel):
	session_id: str = Field(min_length=1)
	round: str = Field(pattern="^(assessment|technical|dsa|project_discussion|hr)$")


def interview_end_state(session: Mapping[str, Any]) -> dict[str, Any]:
	ended = session.get("interview_ended") or {}
	return {"finished": bool(ended), "finished_round": ended.get("round")}


def interview_end_message(session: Mapping[str, Any]) -> str | None:
	"""Why this interview can't continue, or None while it is open."""
	return FINISHED_MESSAGE if session.get("interview_ended") else None


def ensure_interview_open(session: Mapping[str, Any]) -> None:
	"""Refuse round actions on an interview the candidate has finished."""
	message = interview_end_message(session)
	if message:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _owned_session(session_id: str, current_user: AuthenticatedUser) -> Mapping[str, Any]:
	try:
		session = get_session(session_id)
	except DatabaseClientError as exc:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
	if session is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
	return ensure_session_access(session, current_user)


@router.get("/{session_id}")
def get_interview_end(
	session_id: str,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	return interview_end_state(_owned_session(session_id, current_user))


@router.post("/finish")
def finish_interview(
	request: FinishInterviewRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	"""End the whole interview now, at the candidate's request. Idempotent."""
	session = _owned_session(request.session_id, current_user)
	if interview_end_message(session):
		return interview_end_state(session)
	try:
		updated = mark_interview_finished(request.session_id, request.round)
	except DatabaseClientError as exc:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
	return interview_end_state(updated)
