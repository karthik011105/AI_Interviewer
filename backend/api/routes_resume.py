"""Resume parsing and role-matching routes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from backend.api.auth import (
	AuthenticatedUser,
	ensure_session_access,
	require_current_user,
	resolve_authenticated_user_id,
)
from backend.api.quotas import RESUME_PARSE, ROLE_MATCH, quota_dependency
from backend.assessment import normalize_assessment_role_key
from backend.config import ConfigurationError, get_settings
from backend.database.queries import (
	create_session,
	get_resume_data,
	get_session,
	save_interview_contexts,
	update_session_role_selected,
)
from backend.database.db_errors import DatabaseClientError
from backend.nlp.interview_context import build_interview_round_contexts
from backend.nlp.resume_parser import (
	ResumeEncryptedPdfError,
	ResumeExtractionError,
	ResumeLlmError,
	ResumeParser,
	ResumeSchemaError,
)
from backend.nlp.role_matcher import match_and_store_roles, rank_roles, serialize_role_match_result
from backend.nlp.resume_quality_scorer import score_resume_as_dict

router = APIRouter(prefix="/resume", tags=["resume"])
_LOGGER = logging.getLogger(__name__)

_PDF_FILE_SIGNATURE = b"%PDF-"
_ALLOWED_PDF_CONTENT_TYPES = {
	"application/pdf",
	"application/x-pdf",
	"application/acrobat",
	"applications/vnd.pdf",
	"text/pdf",
}


class ResumeParseRequest(BaseModel):
	pdf_path: str
	session_id: str | None = None
	user_id: str | None = None
	role_selected: str | None = None
	structured_resume_payload: dict[str, object] | None = None
	persist_resume: bool = True
	run_role_matching: bool = True
	use_groq_profiles: bool = True
	persist_role_matches: bool = True
	max_roles: int = Field(default=5, ge=1, le=10)
	# When persist_resume=True, round-specific contexts are always built and stored.
	# Set to False only to suppress context storage (e.g. during unit tests).
	persist_interview_contexts: bool = True


class RoleMatchRequest(BaseModel):
	session_id: str
	max_roles: int = Field(default=5, ge=1, le=10)
	persist_role_matches: bool = True
	use_groq_profiles: bool = True


class RoleSelectionRequest(BaseModel):
	session_id: str
	role_key: str
	role_title: str | None = None
	skill_gaps: list[str] = Field(default_factory=list)


@router.get("/health")
def resume_health() -> dict[str, str]:
	return {"status": "ok"}


if get_settings().allow_local_resume_path_api:
	@router.post("/parse")
	def parse_resume(
		request: ResumeParseRequest,
		current_user: AuthenticatedUser = Depends(require_current_user),
	) -> dict[str, object]:
		return _parse_resume_request(request, Path(request.pdf_path), current_user=current_user)


@router.post("/parse-upload")
async def parse_resume_upload(
	resume_file: UploadFile = File(...),
	session_id: str | None = Form(None),
	user_id: str | None = Form(None),
	role_selected: str | None = Form(None),
	persist_resume: bool = Form(True),
	run_role_matching: bool = Form(True),
	use_groq_profiles: bool = Form(True),
	persist_role_matches: bool = Form(True),
	max_roles: int = Form(5),
	persist_interview_contexts: bool = Form(True),
	# Metered: this route runs a full-resume Groq extraction, the most expensive
	# single LLM call in the application.
	current_user: AuthenticatedUser = Depends(quota_dependency(RESUME_PARSE)),
) -> dict[str, object]:
	filename = (resume_file.filename or "resume.pdf").strip()
	if not filename.lower().endswith(".pdf"):
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Only PDF resumes are supported.",
		)

	temp_pdf_path: Path | None = None
	try:
		temp_pdf_path = await _persist_validated_upload_to_temp_pdf(resume_file)

		request = ResumeParseRequest(
			pdf_path=str(temp_pdf_path),
			session_id=session_id,
			user_id=user_id,
			role_selected=role_selected,
			persist_resume=persist_resume,
			run_role_matching=run_role_matching,
			use_groq_profiles=use_groq_profiles,
			persist_role_matches=persist_role_matches,
			max_roles=max_roles,
			persist_interview_contexts=persist_interview_contexts,
		)
		response = _parse_resume_request(request, temp_pdf_path, current_user=current_user)
		response["uploaded_filename"] = filename
		return response
	finally:
		await resume_file.close()
		if temp_pdf_path is not None:
			temp_pdf_path.unlink(missing_ok=True)


def _format_upload_limit(max_upload_bytes: int) -> str:
	mebibytes = max_upload_bytes / (1024 * 1024)
	if mebibytes.is_integer():
		return f"{int(mebibytes)} MiB"
	return f"{mebibytes:.1f} MiB"


async def _persist_validated_upload_to_temp_pdf(resume_file: UploadFile) -> Path:
	settings = get_settings().resume_parsing
	content_type = str(resume_file.content_type or "").strip().casefold()
	if content_type and content_type not in _ALLOWED_PDF_CONTENT_TYPES:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="The uploaded file must use a PDF content type.",
		)

	temp_pdf_path: Path | None = None
	total_bytes = 0
	header = b""
	chunk_size = 1024 * 1024
	max_upload_bytes = settings.max_upload_bytes

	try:
		with NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
			temp_pdf_path = Path(temp_file.name)
			while True:
				chunk = await resume_file.read(chunk_size)
				if not chunk:
					break

				if len(header) < len(_PDF_FILE_SIGNATURE):
					header += chunk[: len(_PDF_FILE_SIGNATURE) - len(header)]

				total_bytes += len(chunk)
				if total_bytes > max_upload_bytes:
					raise HTTPException(
						status_code=status.HTTP_413_CONTENT_TOO_LARGE,
						detail=(
							"The uploaded PDF exceeds the maximum allowed size of "
							f"{_format_upload_limit(max_upload_bytes)}."
						),
					)

				temp_file.write(chunk)

		if total_bytes == 0:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="The uploaded PDF is empty.",
			)

		if not header.startswith(_PDF_FILE_SIGNATURE):
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="The uploaded file is not a valid PDF.",
			)

		if temp_pdf_path is None:
			raise HTTPException(
				status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
				detail="Failed to persist the uploaded PDF.",
			)

		return temp_pdf_path
	except Exception:
		if temp_pdf_path is not None:
			temp_pdf_path.unlink(missing_ok=True)
		raise


def _parse_resume_request(
	request: ResumeParseRequest,
	pdf_path: Path,
	*,
	current_user: AuthenticatedUser,
) -> dict[str, object]:
	if not pdf_path.is_file():
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="The supplied PDF path does not exist.",
		)

	try:
		session_id, created_session = _resolve_session(
			requested_session_id=request.session_id,
			user_id=request.user_id,
			role_selected=request.role_selected,
			should_persist=request.persist_resume or request.persist_role_matches,
			current_user=current_user,
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	parser = ResumeParser()
	extractor = _build_override_extractor(request.structured_resume_payload)
	try:
		if request.persist_resume:
			parse_result = parser.parse_and_store_resume(
				session_id=session_id,
				pdf_path=pdf_path,
				extractor=extractor,
			)
		else:
			parse_result = parser.parse_pdf(
				session_id=session_id,
				pdf_path=pdf_path,
				extractor=extractor,
			)
	except (ConfigurationError, ResumeEncryptedPdfError, ResumeExtractionError, ResumeSchemaError, ResumeLlmError) as exc:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail=str(exc),
		) from exc

	response: dict[str, object] = {
		"session_id": session_id,
		"created_session": created_session,
		"persisted_resume": request.persist_resume,
		"resume": asdict(parse_result),
	}

	# Role matching — optional, produces the suggested fresher roles shown after upload.
	selected_role_match: dict[str, object] | None = None
	if request.run_role_matching:
		try:
			match_result = rank_roles(
				session_id=session_id,
				parsed_resume=parse_result.parsed_resume,
				max_roles=request.max_roles,
				persist=request.persist_role_matches,
				use_groq_profiles=request.use_groq_profiles,
			)
		except DatabaseClientError as exc:
			raise HTTPException(
				status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
				detail=str(exc),
			) from exc
		matches_serialized = serialize_role_match_result(match_result)
		response["role_matches"] = matches_serialized
		response["related_job_roles"] = matches_serialized["matches"]
		response["persisted_role_matches"] = request.persist_role_matches
		selected_role_match = _pick_selected_role_match(
			matches_serialized["matches"],
			request.role_selected,
		)

	# Build round-specific contexts from the parsed resume.
	# skill_gaps in the technical context come from the top role match if available.
	round_contexts = build_interview_round_contexts(
		parse_result.parsed_resume,
		selected_role=request.role_selected,
		role_match=selected_role_match,
	)
	response["interview_contexts"] = round_contexts
	response["selected_role_key"] = request.role_selected
	response["role_selection_required"] = bool(response.get("related_job_roles")) and not bool(request.role_selected)
	response["role_selection_endpoint"] = "/resume/select-role"

	# Persist contexts to MongoDB when the resume itself is being persisted.
	if request.persist_resume and request.persist_interview_contexts:
		try:
			save_interview_contexts(session_id, round_contexts)
			response["persisted_interview_contexts"] = True
		except DatabaseClientError as exc:
			_LOGGER.warning(
				"Failed to persist interview contexts for session %s after resume parsing: %s",
				session_id,
				exc,
			)
			# Non-fatal: resume is already stored; log and continue.
			response["persisted_interview_contexts"] = False
	else:
		response["persisted_interview_contexts"] = False

	# Resume Quality Score — local NLP, no API calls. Safe to always compute.
	try:
		role_key_for_scoring = (
			request.role_selected
			or (selected_role_match or {}).get("role_key")
			or "software_engineer"
		)
		response["resume_quality"] = score_resume_as_dict(
			parsed_resume=parse_result.parsed_resume,
			raw_text=parse_result.raw_text,
			role_key=str(role_key_for_scoring),
		)
	except Exception:
		response["resume_quality"] = None

	return response


@router.post("/match-roles")
def match_roles(
	request: RoleMatchRequest,
	# Metered: role matching optionally builds Groq-backed role profiles.
	current_user: AuthenticatedUser = Depends(quota_dependency(ROLE_MATCH)),
) -> dict[str, object]:
	_require_accessible_session(request.session_id, current_user)
	try:
		resume_record = get_resume_data(request.session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	if resume_record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="No stored resume was found for the supplied session_id.",
		)

	parsed_resume = resume_record.get("parsed_json")
	if not isinstance(parsed_resume, dict):
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Stored resume data is malformed and cannot be role matched.",
		)

	try:
		match_result = (
			match_and_store_roles(
				session_id=request.session_id,
				parsed_resume=parsed_resume,
				max_roles=request.max_roles,
				use_groq_profiles=request.use_groq_profiles,
			)
			if request.persist_role_matches
			else rank_roles(
				session_id=request.session_id,
				parsed_resume=parsed_resume,
				max_roles=request.max_roles,
				persist=False,
				use_groq_profiles=request.use_groq_profiles,
			)
		)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	return serialize_role_match_result(match_result)


@router.post("/select-role")
def select_role(
	request: RoleSelectionRequest,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, object]:
	# Preserve the original role key from the role matcher exactly as sent.
	# Normalization for assessment blueprint lookup happens later in routes_assessment.
	role_key = request.role_key.strip()
	if not role_key:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="role_key is required.",
		)

	_require_accessible_session(request.session_id, current_user)

	try:
		update_session_role_selected(request.session_id, role_key)
		resume_record = get_resume_data(request.session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	if resume_record is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="No stored resume was found for the supplied session_id.",
		)

	parsed_resume = resume_record.get("parsed_json")
	if not isinstance(parsed_resume, dict):
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Stored resume data is malformed and cannot build interview contexts.",
		)

	selected_role_match = {
		"role_key": role_key,
		"title": request.role_title or _humanize_role_key(role_key),
		"skill_gaps": request.skill_gaps,
	}
	round_contexts = build_interview_round_contexts(
		parsed_resume,
		selected_role=role_key,
		role_match=selected_role_match,
	)

	try:
		save_interview_contexts(request.session_id, round_contexts)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	return {
		"session_id": request.session_id,
		"selected_role": selected_role_match,
		"interview_contexts": round_contexts,
		"persisted_interview_contexts": True,
		"role_selection_required": False,
	}


def _resolve_session(
	*,
	requested_session_id: str | None,
	user_id: str | None,
	role_selected: str | None,
	should_persist: bool,
	current_user: AuthenticatedUser,
) -> tuple[str, bool]:
	resolved_user_id = resolve_authenticated_user_id(user_id, current_user)
	resolved_role_selected = (
		normalize_assessment_role_key(role_selected) if role_selected else None
	)

	if not should_persist:
		return requested_session_id or str(uuid4()), False

	if requested_session_id:
		_require_accessible_session(requested_session_id, current_user)
		return requested_session_id, False

	if not resolved_user_id:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Authenticated session creation requires a user owner.",
		)

	created_session = create_session(user_id=resolved_user_id, role_selected=resolved_role_selected)
	session_id = created_session.get("id")
	if not isinstance(session_id, str) or not session_id:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Session creation succeeded but did not return an id.",
		)
	return session_id, True


def _require_accessible_session(
	session_id: str,
	current_user: AuthenticatedUser,
) -> Mapping[str, object]:
	existing_session = get_session(session_id)
	if existing_session is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="The supplied session_id does not exist.",
		)
	return ensure_session_access(existing_session, current_user)


def _build_override_extractor(
	structured_resume_payload: Mapping[str, object] | None,
) -> callable | None:
	if structured_resume_payload is None:
		return None

	def extractor(_: str) -> Mapping[str, object]:
		return dict(structured_resume_payload)

	return extractor


def _pick_selected_role_match(
	matches: list[dict[str, object]],
	selected_role_key: str | None,
) -> dict[str, object] | None:
	if not matches:
		return None
	if not selected_role_key:
		return matches[0]

	selected_key = selected_role_key.strip().casefold()
	for match in matches:
		role_key = str(match.get("role_key") or "").strip().casefold()
		if role_key == selected_key:
			return match
	return matches[0]


def _humanize_role_key(role_key: str) -> str:
	return " ".join(part.capitalize() for part in role_key.split("_") if part)


__all__ = ["router"]
