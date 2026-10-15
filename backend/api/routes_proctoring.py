"""Full-screen proctoring for the interview rounds, and finishing early.

Every round (assessment, technical, coding, project discussion, HR) runs in
full screen. Leaving it, or switching tab/window, is a warning; the third
warning ends the whole interview. The count lives on the session record, so
a page refresh or a second tab cannot reset it, and once the interview has
ended the round endpoints refuse to continue (see ``ensure_interview_open``).

The candidate can also end it themselves ("Finish interview"); that is stored
as ``interview_ended`` on the session and closes the rounds the same way.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.auth import AuthenticatedUser, ensure_session_access, require_current_user
from backend.database.db_errors import DatabaseClientError
from backend.database.queries import get_session, mark_interview_finished, record_proctoring_warning

router = APIRouter(prefix="/proctoring", tags=["proctoring"])

WARNING_LIMIT = 3
ENDED_MESSAGE = (
	"This interview has ended: you left full screen or switched tabs "
	f"{WARNING_LIMIT} times. You can still view your report."
)
FINISHED_MESSAGE = "You finished this interview early. You can still view your report."


_ROUND_PATTERN = "^(assessment|technical|dsa|project_discussion|hr)$"


class FinishInterviewRequest(BaseModel):
	session_id: str = Field(min_length=1)
	round: str = Field(pattern=_ROUND_PATTERN)


class ProctoringWarningRequest(BaseModel):
	session_id: str = Field(min_length=1)
	round: str = Field(pattern=_ROUND_PATTERN)
	reason: str = Field(pattern="^(exit_fullscreen|tab_hidden|window_blur)$")


def proctoring_state(session: Mapping[str, Any]) -> dict[str, Any]:
	proctoring = session.get("proctoring") or {}
	ended = session.get("interview_ended") or {}
	warnings = int(proctoring.get("warnings") or 0)
	return {
		"warnings": warnings,
		"limit": WARNING_LIMIT,
		"remaining": max(0, WARNING_LIMIT - warnings),
		"terminated": bool(proctoring.get("terminated")),
		"terminated_round": proctoring.get("terminated_round"),
		"finished": bool(ended),
		"finished_round": ended.get("round"),
	}


def interview_end_message(session: Mapping[str, Any]) -> str | None:
	"""Why this interview can't continue, or None while it is open."""
	if (session.get("proctoring") or {}).get("terminated"):
		return ENDED_MESSAGE
	if session.get("interview_ended"):
		return FINISHED_MESSAGE
	return None


def ensure_interview_open(session: Mapping[str, Any]) -> None:
	"""Refuse round actions on an interview that has ended (proctoring or
	the candidate's own Finish interview)."""
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
def get_proctoring(
	session_id: str,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	return proctoring_state(_owned_session(session_id, current_user))


@router.post("/warning")
def post_warning(
	request: ProctoringWarningRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	session = _owned_session(request.session_id, current_user)
	if interview_end_message(session):
		return proctoring_state(session)
	try:
		updated = record_proctoring_warning(request.session_id, request.round, request.reason, WARNING_LIMIT)
	except DatabaseClientError as exc:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
	return proctoring_state(updated)


@router.post("/finish")
def finish_interview(
	request: FinishInterviewRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	"""End the whole interview now, at the candidate's request. Idempotent."""
	session = _owned_session(request.session_id, current_user)
	if interview_end_message(session):
		return proctoring_state(session)
	try:
		updated = mark_interview_finished(request.session_id, request.round)
	except DatabaseClientError as exc:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
	return proctoring_state(updated)
