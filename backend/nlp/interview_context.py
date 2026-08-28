"""Round-specific interview context builders derived from parsed resume data.

These helpers keep later interview modules from carrying the full parsed resume
payload when only a small subset of fields is needed for prompt generation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from backend.nlp.resume_skill_profiler import profile_resume_skills

_LOGGER = logging.getLogger(__name__)


def _clean_text(value: Any) -> str:
	if value is None:
		return ""
	return " ".join(str(value).split()).strip()


def _normalize_string_list(values: Any) -> list[str]:
	if values is None:
		return []
	if not isinstance(values, list):
		values = [values]

	result: list[str] = []
	seen: set[str] = set()
	for value in values:
		text = _clean_text(value)
		if not text:
			continue
		key = text.casefold()
		if key in seen:
			continue
		seen.add(key)
		result.append(text)
	return result


def _normalize_projects(values: Any) -> list[dict[str, Any]]:
	if not isinstance(values, list):
		return []

	projects: list[dict[str, Any]] = []
	for value in values:
		if not isinstance(value, Mapping):
			continue
		project = {
			"title": _clean_text(value.get("title")),
			"description": _clean_text(value.get("description")),
			"tech_stack": _normalize_string_list(value.get("tech_stack")),
			"outcomes": _normalize_string_list(value.get("outcomes")),
			"role": _clean_text(value.get("role")),
		}
		if any(project.values()):
			projects.append(project)
	return projects


def _resolve_role_context(
	*,
	selected_role: str | None = None,
	role_match: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	role_key = _clean_text(selected_role)
	role_title = ""
	skill_gaps: list[str] = []

	if isinstance(role_match, Mapping):
		role_key = _clean_text(role_match.get("role_key")) or role_key
		role_title = _clean_text(role_match.get("title"))
		skill_gaps = _normalize_string_list(role_match.get("skill_gaps"))

	return {
		"selected_role_key": role_key or None,
		"selected_role_title": role_title or None,
		"skill_gaps": skill_gaps,
	}


def _build_technical_skill_profile(
	parsed_resume: Mapping[str, Any],
	role_key: str | None,
) -> dict[str, Any]:
	if not role_key:
		return {
			"skill_profile": None,
			"strong_skills": [],
			"familiar_skills": [],
			"mentioned_skills": [],
			"absent_skills": [],
			"soft_gap_skills": [],
			"priority_focus_areas": [],
		}

	try:
		profile = profile_resume_skills(parsed_resume, role_key)
	except Exception:
		_LOGGER.exception("Failed to build technical skill profile for role %s.", role_key)
		return {
			"skill_profile": None,
			"strong_skills": [],
			"familiar_skills": [],
			"mentioned_skills": [],
			"absent_skills": [],
			"soft_gap_skills": [],
			"priority_focus_areas": [],
		}

	return {
		"skill_profile": profile.to_dict(),
		"strong_skills": list(profile.strong_skills),
		"familiar_skills": list(profile.familiar_skills),
		"mentioned_skills": list(profile.mentioned_skills),
		"absent_skills": list(profile.absent_skills),
		"soft_gap_skills": list(profile.soft_gap_skills),
		"priority_focus_areas": list(profile.priority_focus_areas),
	}


def build_hr_round_context(
	parsed_resume: Mapping[str, Any],
	*,
	selected_role: str | None = None,
	role_match: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	"""Build the minimal context required for HR question generation."""

	role_context = _resolve_role_context(
		selected_role=selected_role,
		role_match=role_match,
	)
	return {
		"candidate_name": _clean_text(parsed_resume.get("name")),
		"summary": _clean_text(parsed_resume.get("summary")),
		"interests": _normalize_string_list(parsed_resume.get("interests")),
		"selected_role_key": role_context["selected_role_key"],
		"selected_role_title": role_context["selected_role_title"],
	}


def build_technical_round_context(
	parsed_resume: Mapping[str, Any],
	*,
	selected_role: str | None = None,
	role_match: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	"""Build the minimal resume-derived context required for technical rounds."""

	role_context = _resolve_role_context(
		selected_role=selected_role,
		role_match=role_match,
	)
	skill_profile_context = _build_technical_skill_profile(
		parsed_resume,
		role_context["selected_role_key"],
	)
	return {
		"skills": _normalize_string_list(parsed_resume.get("skills")),
		"technologies": _normalize_string_list(parsed_resume.get("technologies")),
		"selected_role_key": role_context["selected_role_key"],
		"selected_role_title": role_context["selected_role_title"],
		"skill_gaps": role_context["skill_gaps"],
		**skill_profile_context,
	}


def build_project_round_context(
	parsed_resume: Mapping[str, Any],
	*,
	selected_role: str | None = None,
	role_match: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	"""Build the minimal context required for project discussion prompts."""

	role_context = _resolve_role_context(
		selected_role=selected_role,
		role_match=role_match,
	)
	return {
		"projects": _normalize_projects(parsed_resume.get("projects")),
		"selected_role_key": role_context["selected_role_key"],
		"selected_role_title": role_context["selected_role_title"],
	}


def build_interview_round_contexts(
	parsed_resume: Mapping[str, Any],
	*,
	selected_role: str | None = None,
	role_match: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
	"""Build all round-specific interview contexts from one parsed resume."""

	return {
		"hr": build_hr_round_context(
			parsed_resume,
			selected_role=selected_role,
			role_match=role_match,
		),
		"technical": build_technical_round_context(
			parsed_resume,
			selected_role=selected_role,
			role_match=role_match,
		),
		"project_discussion": build_project_round_context(
			parsed_resume,
			selected_role=selected_role,
			role_match=role_match,
		),
	}


__all__ = [
	"build_hr_round_context",
	"build_interview_round_contexts",
	"build_project_round_context",
	"build_technical_round_context",
]