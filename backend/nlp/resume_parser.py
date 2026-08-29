"""Resume parsing with PDF extraction, structured JSON cleanup, and persistence."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from backend.config import AppSettings, ConfigurationError, GroqSettings, get_settings
from backend.database.queries import save_resume_data
from backend.nlp.groq_client import GroqCompletionError, GroqDependencyError, create_chat_completion
from backend.nlp.ner_extractor import extract_entities, validate_skills

StructuredResumeExtractor = Callable[[str], Mapping[str, Any] | str]

_JSON_CODE_FENCE_PATTERN = re.compile(
	r"```(?:json)?\s*(?P<body>.*?)\s*```",
	re.IGNORECASE | re.DOTALL,
)


class ResumeParserError(RuntimeError):
	"""Base error for resume parsing failures."""


class ResumeDependencyError(ResumeParserError):
	"""Raised when an optional resume parsing dependency is missing."""


class ResumeEncryptedPdfError(ResumeParserError):
	"""Raised when a PDF requires a password or is otherwise encrypted."""


class ResumeExtractionError(ResumeParserError):
	"""Raised when a PDF cannot be converted into usable text."""


class ResumeSchemaError(ResumeParserError):
	"""Raised when structured resume output is malformed or incomplete."""


class ResumeLlmError(ResumeParserError):
	"""Raised when the structured resume extraction call fails."""


@dataclass(frozen=True, slots=True)
class ResumeParseResult:
	"""Parsed resume data before or after persistence."""

	session_id: str
	raw_text: str
	parsed_resume: dict[str, Any]
	stored_record: dict[str, Any] | None = None


def _load_pymupdf() -> Any:
	try:
		return import_module("fitz")
	except ModuleNotFoundError as exc:
		raise ResumeDependencyError(
			"PyMuPDF is not installed. Install it with `pip install pymupdf`."
		) from exc


def _clean_text(value: Any) -> str:
	if value is None:
		return ""
	text = str(value).replace("\x00", " ")
	return re.sub(r"\s+", " ", text).strip()


def _flatten_to_strings(value: Any) -> list[str]:
	"""Recursively flatten dicts/lists into a plain list of non-empty strings.

	Handles the case where Groq returns skills as a nested object like
	{"programmingLanguages": ["Python"], "pythonLibraries": [...]} instead of
	a flat array, or as a single-item list containing a stringified dict.
	"""
	if value is None:
		return []
	if isinstance(value, dict):
		result: list[str] = []
		for v in value.values():
			result.extend(_flatten_to_strings(v))
		return result
	if isinstance(value, list):
		result = []
		for item in value:
			result.extend(_flatten_to_strings(item))
		return result
	text = _clean_text(value)
	if not text:
		return []
	# Detect a stringified Python dict or JSON object and parse it
	if text.startswith("{") and text.endswith("}"):
		try:
			# Try JSON first, then Python literal eval as fallback
			try:
				parsed = json.loads(text)
			except json.JSONDecodeError:
				import ast
				parsed = ast.literal_eval(text)
			if isinstance(parsed, dict):
				return _flatten_to_strings(parsed)
		except Exception:
			pass
	return [text]


def _normalize_string_list(values: Any) -> list[str]:
	if values is None or values == "":
		return []
	# If Groq returned skills as a dict object at top level, flatten it
	if isinstance(values, dict):
		values = _flatten_to_strings(values)
	elif not isinstance(values, list):
		values = [values]

	result: list[str] = []
	seen: set[str] = set()
	for value in values:
		for text in _flatten_to_strings(value):
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


def _normalize_education(values: Any) -> list[dict[str, str]]:
	if not isinstance(values, list):
		return []

	education_entries: list[dict[str, str]] = []
	for value in values:
		if not isinstance(value, Mapping):
			continue
		entry = {
			"institution": _clean_text(value.get("institution")),
			"degree": _clean_text(value.get("degree")),
			"cgpa": _clean_text(value.get("cgpa")),
			"year": _clean_text(value.get("year")),
		}
		if any(entry.values()):
			education_entries.append(entry)
	return education_entries


def _normalize_certifications(values: Any) -> list[dict[str, str]]:
	if not isinstance(values, list):
		return []

	certifications: list[dict[str, str]] = []
	for value in values:
		if not isinstance(value, Mapping):
			continue
		entry = {
			"title": _clean_text(value.get("title")),
			"platform": _clean_text(value.get("platform")),
			"status": _clean_text(value.get("status")),
		}
		if any(entry.values()):
			certifications.append(entry)
	return certifications


def _normalize_achievements(values: Any) -> list[dict[str, str]]:
	if not isinstance(values, list):
		return []

	achievements: list[dict[str, str]] = []
	for value in values:
		if not isinstance(value, Mapping):
			continue
		entry = {
			"title": _clean_text(value.get("title")),
			"details": _clean_text(value.get("details")),
		}
		if any(entry.values()):
			achievements.append(entry)
	return achievements


def cleanup_structured_json(payload: Mapping[str, Any] | str) -> dict[str, Any]:
	"""Extract a JSON object from raw LLM output before validation."""

	if isinstance(payload, Mapping):
		return dict(payload)

	text = str(payload).strip()
	if not text:
		raise ResumeSchemaError("Structured resume output was empty.")

	match = _JSON_CODE_FENCE_PATTERN.search(text)
	if match:
		text = match.group("body").strip()

	start_index = text.find("{")
	end_index = text.rfind("}")
	if start_index == -1 or end_index == -1 or end_index <= start_index:
		raise ResumeSchemaError(
			"Structured resume output did not contain a valid JSON object."
		)

	json_text = text[start_index : end_index + 1]
	try:
		parsed = json.loads(json_text)
	except json.JSONDecodeError as exc:
		raise ResumeSchemaError("Structured resume output was not valid JSON.") from exc

	if not isinstance(parsed, dict):
		raise ResumeSchemaError("Structured resume output must decode to a JSON object.")
	return dict(parsed)


def _enrich_with_ner(raw_text: str, parsed_resume: dict[str, Any]) -> dict[str, Any]:
	"""
	Augment parsed resume data with Named Entity Recognition results.

	Runs two NLP passes on the raw text:
	  1. Entity extraction (PERSON, ORG, GPE, TECH) using spaCy if available,
	     regex patterns otherwise.
	  2. Skill cross-validation — checks which Groq-extracted skills/technologies
	     are directly confirmed in the raw text and surfaces any extras found by NER.

	The results are added under ``ner_extraction`` and ``skill_validation`` keys
	in the parsed resume dict and stored alongside the Groq-extracted fields in
	MongoDB.  Extraction never overwrites the main skills/technologies lists so
	the Groq output remains authoritative.
	"""
	import logging
	_log = logging.getLogger(__name__)

	try:
		ner_result = extract_entities(raw_text)
		groq_skills = list(parsed_resume.get("skills", [])) + list(
			parsed_resume.get("technologies", [])
		)
		skill_val = validate_skills(groq_skills, raw_text)

		parsed_resume["ner_extraction"] = {
			"mode": ner_result.ner_mode,
			"entity_count": ner_result.entity_count,
			"persons": ner_result.persons[:5],
			"organizations": ner_result.organizations[:10],
			"locations": ner_result.locations[:5],
			"technologies_found": ner_result.technologies[:20],
			"skills_found": ner_result.skills[:20],
		}
		parsed_resume["skill_validation"] = {
			"confirmed": skill_val.confirmed,
			"unconfirmed": skill_val.unconfirmed,
			"extra_found": skill_val.extra_found[:15],
			"coverage_ratio": skill_val.coverage_ratio,
		}
	except Exception as exc:
		_log.warning("NER enrichment failed (non-fatal): %s", exc)

	return parsed_resume


def normalize_resume_payload(
	*,
	session_id: str,
	payload: Mapping[str, Any] | str,
) -> dict[str, Any]:
	"""Normalize and validate the structured resume schema before persistence."""

	parsed_payload = cleanup_structured_json(payload)
	normalized = {
		"session_id": session_id,
		"name": _clean_text(parsed_payload.get("name")),
		"email": _clean_text(parsed_payload.get("email")),
		"phone": _clean_text(parsed_payload.get("phone")),
		"location": _clean_text(parsed_payload.get("location")),
		"summary": _clean_text(parsed_payload.get("summary")),
		"interests": _normalize_string_list(parsed_payload.get("interests")),
		"skills": _normalize_string_list(parsed_payload.get("skills")),
		"technologies": _normalize_string_list(parsed_payload.get("technologies")),
		"projects": _normalize_projects(parsed_payload.get("projects")),
		"education": _normalize_education(parsed_payload.get("education")),
		"certifications": _normalize_certifications(
			parsed_payload.get("certifications")
		),
		"achievements": _normalize_achievements(parsed_payload.get("achievements")),
	}

	if not any(
		[
			normalized["name"],
			normalized["summary"],
			normalized["skills"],
			normalized["projects"],
			normalized["education"],
		]
	):
		raise ResumeSchemaError(
			"Structured resume output was missing essential candidate data."
		)

	return normalized


class GroqResumeStructuredExtractor:
	"""Groq SDK-backed structured resume extractor."""

	def __init__(self, settings: GroqSettings | None = None) -> None:
		resolved_settings = settings or get_settings().groq
		if resolved_settings is None:
			raise ConfigurationError(
				"GROQ_API_KEY must be configured to use the default resume extractor."
			)
		self._settings = resolved_settings

	def __call__(self, raw_text: str) -> dict[str, Any]:
		return self.extract(raw_text)

	def extract(self, raw_text: str) -> dict[str, Any]:
		try:
			completion = create_chat_completion(
				settings=self._settings,
				model=self._settings.resume_parser_model,
				temperature=0,
				response_format={"type": "json_object"},
				messages=[
					{
						"role": "system",
						"content": (
							"You are a resume parsing API. Extract information from the resume text "
							"into the exact JSON schema below. Return JSON only — no markdown fences, "
							"no commentary, no extra keys.\n\n"
							"Schema rules (follow exactly):\n"
							"- name: full candidate name as a string\n"
							"- email: email address string\n"
							"- phone: phone number string\n"
							"- location: city and country string\n"
							"- summary: one-paragraph professional summary string\n"
							"- interests: FLAT array of strings, e.g. [\"Machine Learning\", \"Cloud\"].\n"
							"  Extract from any 'interests', 'hobbies', or career objective text.\n"
							"- skills: FLAT array of plain strings only, e.g. [\"Python\", \"NumPy\", \"AWS\"].\n"
							"  DO NOT return a dict or nested object. Flatten all skill categories into one array.\n"
							"- technologies: FLAT array of framework/tool/platform strings\n"
							"- projects: array of project objects. Each project MUST have:\n"
							"    title: the project heading or name that appears before the bullet points.\n"
							"      If no explicit title exists, infer a short descriptive title from the description.\n"
							"      NEVER leave title as an empty string.\n"
							"    description: one-paragraph summary of the project\n"
							"    tech_stack: FLAT array of technologies used in this project\n"
							"    outcomes: FLAT array of measurable results or achievements from this project\n"
							"    role: the candidate's role or contribution (e.g. 'Built and deployed end to end')\n"
							"- education: array of education objects with institution, degree, cgpa, year\n"
							"- certifications: array of certification objects. Each MUST have:\n"
							"    title: the exact certification name (never empty if a cert exists)\n"
							"    platform: the provider/platform name\n"
							"    status: Completed | In progress | Planned\n"
							"- achievements: array of achievement objects with title and details\n\n"
							"Use empty string \"\" only when the data truly does not exist. "
							"Prefer inferring reasonable values over leaving fields empty."
						),
					},
					{
						"role": "user",
						"content": (
							"Parse this resume text into the JSON schema described above.\n\n"
							f"Resume text:\n{raw_text}"
						),
					},
				],
			)
		except GroqDependencyError as exc:
			raise ResumeDependencyError(str(exc)) from exc
		except GroqCompletionError as exc:
			raise ResumeLlmError(f"Groq resume extraction failed: {exc}") from exc
		except Exception as exc:
			raise ResumeLlmError(
				f"Groq resume extraction failed: {exc}"
			) from exc

		content = completion.choices[0].message.content
		return cleanup_structured_json(content)


class ResumeParser:
	"""Service object for extracting, structuring, and storing resumes."""

	def __init__(
		self,
		*,
		settings: AppSettings | None = None,
		extractor: StructuredResumeExtractor | None = None,
	) -> None:
		self._settings = settings or get_settings()
		self._extractor = extractor

	@property
	def settings(self) -> AppSettings:
		return self._settings

	def extract_text(self, pdf_path: str | Path) -> str:
		"""Extract text from a PDF and reject encrypted or low-signal documents."""

		fitz = _load_pymupdf()
		document = fitz.open(str(pdf_path))
		try:
			if getattr(document, "needs_pass", False) or getattr(
				document,
				"is_encrypted",
				False,
			):
				raise ResumeEncryptedPdfError(
					"The uploaded PDF is encrypted and cannot be parsed."
				)

			page_texts: list[str] = []
			for page in document:
				page_texts.append(page.get_text("text"))
		finally:
			document.close()

		raw_text = self._settings.resume_parsing.page_separator.join(page_texts)
		raw_text = raw_text.replace("\x00", " ").strip()
		raw_text = re.sub(r"\n{3,}", "\n\n", raw_text)

		if len(raw_text) < self._settings.resume_parsing.min_text_characters:
			raise ResumeExtractionError(
				"The PDF appears scanned, image-only, or too low in text content to parse reliably."
			)

		max_length = self._settings.resume_parsing.max_text_characters
		if len(raw_text) > max_length:
			raw_text = raw_text[:max_length].rstrip()

		return raw_text

	def parse_structured_resume(
		self,
		*,
		session_id: str,
		raw_text: str,
		extractor: StructuredResumeExtractor | None = None,
	) -> dict[str, Any]:
		resolved_extractor = self._resolve_extractor(extractor)
		structured_output = resolved_extractor(raw_text)
		parsed_resume = normalize_resume_payload(
			session_id=session_id,
			payload=structured_output,
		)
		# NER enrichment: supplement Groq extraction with local NLP entity recognition
		parsed_resume = _enrich_with_ner(raw_text, parsed_resume)
		return parsed_resume

	def parse_pdf(
		self,
		*,
		session_id: str,
		pdf_path: str | Path,
		extractor: StructuredResumeExtractor | None = None,
	) -> ResumeParseResult:
		raw_text = self.extract_text(pdf_path)
		parsed_resume = self.parse_structured_resume(
			session_id=session_id,
			raw_text=raw_text,
			extractor=extractor,
		)
		return ResumeParseResult(
			session_id=session_id,
			raw_text=raw_text,
			parsed_resume=parsed_resume,
		)

	def persist_resume(self, result: ResumeParseResult) -> dict[str, Any]:
		return save_resume_data(
			session_id=result.session_id,
			raw_text=result.raw_text,
			parsed_json=result.parsed_resume,
		)

	def parse_and_store_resume(
		self,
		*,
		session_id: str,
		pdf_path: str | Path,
		extractor: StructuredResumeExtractor | None = None,
	) -> ResumeParseResult:
		result = self.parse_pdf(
			session_id=session_id,
			pdf_path=pdf_path,
			extractor=extractor,
		)
		stored_record = self.persist_resume(result)
		return ResumeParseResult(
			session_id=result.session_id,
			raw_text=result.raw_text,
			parsed_resume=result.parsed_resume,
			stored_record=stored_record,
		)

	def _resolve_extractor(
		self,
		extractor: StructuredResumeExtractor | None,
	) -> StructuredResumeExtractor:
		if extractor is not None:
			return extractor
		if self._extractor is not None:
			return self._extractor
		if self._settings.groq is None:
			raise ResumeLlmError(
				"No structured extractor was provided and GROQ_API_KEY is not configured."
			)
		return GroqResumeStructuredExtractor(self._settings.groq)


def parse_and_store_resume(
	*,
	session_id: str,
	pdf_path: str | Path,
	extractor: StructuredResumeExtractor | None = None,
	settings: AppSettings | None = None,
) -> ResumeParseResult:
	"""Convenience wrapper for route handlers and service modules."""

	parser = ResumeParser(settings=settings, extractor=extractor)
	return parser.parse_and_store_resume(session_id=session_id, pdf_path=pdf_path)


__all__ = [
	"GroqResumeStructuredExtractor",
	"ResumeDependencyError",
	"ResumeEncryptedPdfError",
	"ResumeExtractionError",
	"ResumeLlmError",
	"ResumeParseResult",
	"ResumeParser",
	"ResumeParserError",
	"ResumeSchemaError",
	"cleanup_structured_json",
	"normalize_resume_payload",
	"parse_and_store_resume",
]
