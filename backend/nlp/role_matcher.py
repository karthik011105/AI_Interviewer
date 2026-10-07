"""Role matching using weighted scoring with optional semantic embeddings.

Role profiles can be generated dynamically by Groq (one call per session),
giving candidate-specific roles rather than a fixed list.
The curated DEFAULT_ROLE_PROFILES serve as fallback when Groq is unavailable.
Ranking blends exact skill overlap, semantic similarity, BM25 lexical retrieval,
project relevance, and bonus-skill alignment.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import import_module
from typing import Any

from backend.config import get_settings
from backend.database.queries import save_role_matches
from backend.nlp.groq_client import GroqCompletionError, GroqDependencyError, create_chat_completion

_TOKEN_PATTERN = re.compile(r"[a-z0-9+#.]+")
_LOGGER = logging.getLogger(__name__)
_SBERT_MODEL_NAME = "all-MiniLM-L6-v2"
_ROLE_SKILL_WEIGHT = 0.35
_ROLE_SEMANTIC_WEIGHT = 0.25
_ROLE_BM25_WEIGHT = 0.15
_ROLE_PROJECT_WEIGHT = 0.15
_ROLE_BONUS_WEIGHT = 0.10
_BM25_K1 = 1.5
_BM25_B = 0.75
_NON_FRESHER_ROLE_TOKENS = {
	"senior",
	"sr",
	"lead",
	"principal",
	"staff",
	"architect",
	"manager",
	"director",
	"head",
	"specialist",
	"consultant",
	"administrator",
}

DEFAULT_ROLE_PROFILES: tuple[dict[str, Any], ...] = (
	{
		"key": "software_engineer",
		"title": "Software Engineer",
		"description": "Designs, builds, and maintains software systems across the full technology stack.",
		"required_skills": ["programming", "data structures", "algorithms", "git"],
		"bonus_skills": ["system design", "sql", "linux"],
		"project_signals": ["application", "system", "software", "platform", "service"],
	},
	{
		"key": "backend_python_developer",
		"title": "Backend Python Developer",
		"description": "Builds APIs, backend services, and data-backed application flows.",
		"required_skills": ["python", "sql", "git", "fastapi"],
		"bonus_skills": ["docker", "postgresql", "mysql"],
		"project_signals": ["backend", "service", "api", "database", "auth"],
	},
	{
		"key": "backend_java_developer",
		"title": "Backend Java Developer",
		"description": "Builds server-side applications and REST APIs using Java and Spring Boot.",
		"required_skills": ["java", "spring boot", "sql", "git"],
		"bonus_skills": ["hibernate", "maven", "microservices"],
		"project_signals": ["spring", "java", "rest", "api", "backend"],
	},
	{
		"key": "backend_node_developer",
		"title": "Backend Node.js Developer",
		"description": "Builds scalable server-side services and APIs using Node.js.",
		"required_skills": ["javascript", "node.js", "express", "sql"],
		"bonus_skills": ["mongodb", "typescript", "docker"],
		"project_signals": ["node", "express", "api", "server", "backend"],
	},
	{
		"key": "frontend_react_developer",
		"title": "Frontend React Developer",
		"description": "Builds interactive web interfaces with modern JavaScript tooling.",
		"required_skills": ["javascript", "html", "css", "react"],
		"bonus_skills": ["typescript", "tailwind", "ui"],
		"project_signals": ["frontend", "dashboard", "ui", "component", "web"],
	},
	{
		"key": "full_stack_developer",
		"title": "Full Stack Developer",
		"description": "Works across both frontend and backend layers to deliver complete features.",
		"required_skills": ["javascript", "react", "node.js", "sql"],
		"bonus_skills": ["python", "docker", "rest api"],
		"project_signals": ["full stack", "frontend", "backend", "web app", "crud"],
	},
	{
		"key": "mobile_app_developer",
		"title": "Mobile App Developer",
		"description": "Builds Android or iOS applications using Flutter, React Native, or native tooling.",
		"required_skills": ["dart", "flutter", "android", "git"],
		"bonus_skills": ["react native", "firebase", "kotlin"],
		"project_signals": ["mobile", "android", "ios", "app", "flutter"],
	},
	{
		"key": "data_analyst",
		"title": "Data Analyst",
		"description": "Analyzes datasets, builds reports, and communicates quantitative insights.",
		"required_skills": ["python", "pandas", "matplotlib", "sql"],
		"bonus_skills": ["numpy", "jupyter notebook", "power bi"],
		"project_signals": ["analysis", "dataset", "insight", "report", "dashboard"],
	},
	{
		"key": "data_engineer",
		"title": "Data Engineer",
		"description": "Builds data pipelines, ETL workflows, and data warehouse solutions.",
		"required_skills": ["python", "sql", "spark", "airflow"],
		"bonus_skills": ["kafka", "aws", "bigquery"],
		"project_signals": ["pipeline", "etl", "warehouse", "ingestion", "streaming"],
	},
	{
		"key": "machine_learning_engineer",
		"title": "Machine Learning Engineer",
		"description": "Builds and evaluates ML models, feature pipelines, and applied AI systems.",
		"required_skills": ["python", "pytorch", "numpy", "pandas"],
		"bonus_skills": ["tensorflow", "keras", "aws"],
		"project_signals": ["model", "training", "classification", "prediction", "inference"],
	},
	{
		"key": "ai_engineer",
		"title": "AI Engineer",
		"description": "Builds AI-powered applications, LLM pipelines, and intelligent system integrations.",
		"required_skills": ["python", "llm", "prompt engineering", "api integration"],
		"bonus_skills": ["langchain", "openai", "vector database"],
		"project_signals": ["llm", "generative", "agent", "ai assistant", "ai"],
	},
	{
		"key": "data_scientist",
		"title": "Data Scientist",
		"description": "Applies statistical modeling, ML, and analytics to solve business problems.",
		"required_skills": ["python", "statistics", "scikit-learn", "sql"],
		"bonus_skills": ["r", "tableau", "deep learning"],
		"project_signals": ["prediction", "modeling", "experiment", "hypothesis", "analytics"],
	},
	{
		"key": "devops_engineer",
		"title": "DevOps Engineer",
		"description": "Builds CI/CD pipelines, automates infrastructure, and manages deployments.",
		"required_skills": ["linux", "docker", "git", "bash"],
		"bonus_skills": ["kubernetes", "ansible", "terraform"],
		"project_signals": ["ci/cd", "pipeline", "deployment", "infrastructure", "automation"],
	},
	{
		"key": "cloud_engineer",
		"title": "Cloud Engineer",
		"description": "Deploys and manages cloud infrastructure across AWS, GCP, or Azure.",
		"required_skills": ["aws", "linux", "terraform", "python"],
		"bonus_skills": ["gcp", "azure", "kubernetes"],
		"project_signals": ["cloud", "aws", "serverless", "deployment", "infrastructure"],
	},
	{
		"key": "qa_automation_engineer",
		"title": "QA Automation Engineer",
		"description": "Designs tests, automates verification flows, and improves product reliability.",
		"required_skills": ["python", "git", "analytical thinking", "problem-solving"],
		"bonus_skills": ["selenium", "pytest", "c++"],
		"project_signals": ["test", "validation", "automation", "bug", "quality"],
	},
	{
		"key": "cybersecurity_analyst",
		"title": "Cybersecurity Analyst",
		"description": "Identifies and mitigates security vulnerabilities in systems and applications.",
		"required_skills": ["networking", "linux", "python", "security"],
		"bonus_skills": ["ethical hacking", "wireshark", "sql"],
		"project_signals": ["security", "vulnerability", "network", "penetration", "threat"],
	},
)

_ROLE_GENERATOR_SYSTEM_PROMPT = """\
You are a technical recruiter API. Given a candidate's resume summary, skills, technologies, \
and project descriptions, generate exactly 5 role profiles that best fit this candidate for \
fresher/entry-level positions.

Return a JSON object with a single key "roles" containing an array of exactly 5 role objects.
Each role object must have these exact keys:
- key: lowercase_underscore unique identifier (e.g. "ml_engineer")
- title: human-readable role title (e.g. "Machine Learning Engineer")
- description: one sentence describing what this role does day-to-day
- required_skills: array of 3-4 lowercase skill strings that are must-haves for this role
- bonus_skills: array of 3 lowercase skill strings that are nice-to-have extras
- project_signals: array of 5-6 lowercase keywords that would appear in projects for this role

Rules:
- Base roles on real industry job postings for freshers, not generic categories
- Only return roles that are realistic for fresher or entry-level hiring.
- Never return senior, lead, principal, manager, architect, or specialist roles.
- Tailor required_skills to what the candidate already has or is close to having
- Do NOT invent skills the candidate clearly doesn't have as required
- Return JSON only. No markdown, no commentary.
"""


def _is_fresher_friendly_role(role: Mapping[str, Any]) -> bool:
	"""Reject generated roles that imply non-entry-level seniority."""

	combined_text = " ".join(
		str(role.get(field) or "")
		for field in ("key", "title", "description")
	).casefold()
	combined_tokens = _tokenize(combined_text)
	return combined_tokens.isdisjoint(_NON_FRESHER_ROLE_TOKENS)


def _build_candidate_summary_for_groq(parsed_resume: Mapping[str, Any]) -> str:
	"""Build a compact resume summary string to send to Groq for role generation."""
	lines: list[str] = []

	name = str(parsed_resume.get("name") or "").strip()
	if name:
		lines.append(f"Candidate: {name}")

	summary = str(parsed_resume.get("summary") or "").strip()
	if summary:
		lines.append(f"Summary: {summary}")

	skills = parsed_resume.get("skills")
	if isinstance(skills, list) and skills:
		lines.append(f"Skills: {', '.join(str(s) for s in skills[:20])}")

	technologies = parsed_resume.get("technologies")
	if isinstance(technologies, list) and technologies:
		lines.append(f"Technologies: {', '.join(str(t) for t in technologies[:10])}")

	interests = parsed_resume.get("interests")
	if isinstance(interests, list) and interests:
		lines.append(f"Interests: {', '.join(str(i) for i in interests[:6])}")

	projects = parsed_resume.get("projects")
	if isinstance(projects, list):
		for project in projects[:3]:
			if not isinstance(project, Mapping):
				continue
			title = str(project.get("title") or "").strip()
			desc = str(project.get("description") or "").strip()
			if title or desc:
				lines.append(f"Project: {title} — {desc[:200]}")

	return "\n".join(lines)


def generate_role_profiles_with_groq(
	parsed_resume: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
	"""Ask Groq to generate 5 candidate-fit role profiles in one call.

	Returns a list of raw profile dicts on success, or None if Groq is
	unavailable, unconfigured, or returns malformed output. Callers should
	fall back to DEFAULT_ROLE_PROFILES in the None case.
	"""
	settings = get_settings()
	if settings.groq is None:
		return None

	candidate_summary = _build_candidate_summary_for_groq(parsed_resume)

	try:
		completion = create_chat_completion(
			settings=settings.groq,
			model=settings.groq.resume_parser_model,
			temperature=0.3,
			response_format={"type": "json_object"},
			messages=[
				{"role": "system", "content": _ROLE_GENERATOR_SYSTEM_PROMPT},
				{
					"role": "user",
					"content": (
						"Generate 5 role profiles for this candidate:\n\n"
						f"{candidate_summary}"
					),
				},
			],
			timeout=settings.groq.timeout_seconds,
		)
	except (GroqDependencyError, GroqCompletionError) as exc:
		_LOGGER.warning("Groq role generation failed; using curated fallback roles: %s", exc)
		return None

	try:
		content = completion.choices[0].message.content
		parsed = json.loads(content)
		roles = parsed.get("roles")
		if not isinstance(roles, list) or not roles:
			return None
		# Validate each role has the minimum required keys
		valid_roles = []
		seen_keys: set[str] = set()
		for role in roles:
			if not isinstance(role, dict):
				continue
			if not role.get("key") or not role.get("title") or not role.get("description"):
				continue
			if not _is_fresher_friendly_role(role):
				continue
			# Deduplicate by normalised key to prevent React duplicate key issues.
			norm_key = str(role["key"]).strip().casefold()
			if norm_key in seen_keys:
				continue
			seen_keys.add(norm_key)
			# Ensure list fields default to empty lists if missing
			role.setdefault("required_skills", [])
			role.setdefault("bonus_skills", [])
			role.setdefault("project_signals", [])
			valid_roles.append(role)
		return valid_roles if len(valid_roles) >= 3 else None
	except Exception:
		return None


class RoleMatcherError(RuntimeError):
	"""Base error for role matching failures."""


class RoleProfileSchemaError(RoleMatcherError):
	"""Raised when a role profile is malformed."""


@dataclass(frozen=True, slots=True)
class RoleProfile:
	"""Normalized role profile used for ranking candidate fit."""

	key: str
	title: str
	description: str
	required_skills: tuple[str, ...]
	bonus_skills: tuple[str, ...]
	project_signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoleMatch:
	"""Role scoring output for one candidate-role pairing."""

	role_key: str
	title: str
	description: str
	match_percent: float
	skill_gaps: list[str]
	score_breakdown: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RoleMatchResult:
	"""Final ranked roles for a candidate resume."""

	session_id: str
	matches: list[RoleMatch]
	signal_warning: str | None = None


def _normalize_text(value: Any) -> str:
	if value is None:
		return ""
	return re.sub(r"\s+", " ", str(value)).strip()


def _tokenize(value: str) -> set[str]:
	return {token for token in _TOKEN_PATTERN.findall(value.casefold()) if token}


def _tokenize_sequence(value: str) -> list[str]:
	return [token for token in _TOKEN_PATTERN.findall(value.casefold()) if token]


def _normalize_skill_set(values: Any) -> set[str]:
	if values is None:
		return set()
	if not isinstance(values, list):
		values = [values]
	return {_normalize_text(value).casefold() for value in values if _normalize_text(value)}


def _normalize_string_tuple(values: Any) -> tuple[str, ...]:
	if values is None:
		return ()
	if not isinstance(values, list):
		values = [values]
	result: list[str] = []
	seen: set[str] = set()
	for value in values:
		text = _normalize_text(value).casefold()
		if not text or text in seen:
			continue
		seen.add(text)
		result.append(text)
	return tuple(result)


def _coerce_role_profile(value: Mapping[str, Any] | RoleProfile) -> RoleProfile:
	if isinstance(value, RoleProfile):
		return value

	key = _normalize_text(value.get("key"))
	title = _normalize_text(value.get("title"))
	description = _normalize_text(value.get("description"))
	if not key or not title or not description:
		raise RoleProfileSchemaError(
			"Role profiles must define key, title, and description fields."
		)

	return RoleProfile(
		key=key,
		title=title,
		description=description,
		required_skills=_normalize_string_tuple(value.get("required_skills")),
		bonus_skills=_normalize_string_tuple(value.get("bonus_skills")),
		project_signals=_normalize_string_tuple(value.get("project_signals")),
	)


def _safe_ratio(numerator: int, denominator: int) -> float:
	if denominator <= 0:
		return 0.0
	return max(0.0, min(1.0, numerator / denominator))


@lru_cache(maxsize=1)
def get_semantic_encoder() -> Any | None:
	"""Return a process-wide SBERT encoder instance when available."""

	if os.getenv("DISABLE_SEMANTIC_ENCODER", "").strip().lower() in {"1", "true", "yes"}:
		_LOGGER.info("Semantic encoder disabled via DISABLE_SEMANTIC_ENCODER.")
		return None

	try:
		sentence_transformers = import_module("sentence_transformers")
	except ModuleNotFoundError:
		return None

	try:
		return sentence_transformers.SentenceTransformer(_SBERT_MODEL_NAME)
	except Exception:
		_LOGGER.exception("Failed to load semantic encoder %s.", _SBERT_MODEL_NAME)
		return None


@lru_cache(maxsize=1)
def _load_bm25_okapi() -> Any | None:
	"""Return the optional rank_bm25 Okapi implementation when installed."""

	try:
		rank_bm25 = import_module("rank_bm25")
	except ModuleNotFoundError:
		return None
	return getattr(rank_bm25, "BM25Okapi", None)


def _build_profile_text(profile: RoleProfile) -> str:
	return " ".join(
		[
			profile.title,
			profile.description,
			" ".join(profile.required_skills),
			" ".join(profile.bonus_skills),
			" ".join(profile.project_signals),
		]
	)


def _compute_builtin_bm25_scores(
	*,
	query_tokens: Sequence[str],
	documents: Sequence[Sequence[str]],
) -> list[float]:
	"""Compute BM25 scores in pure Python when rank_bm25 is unavailable."""
	if not query_tokens or not documents:
		return [0.0 for _ in documents]

	avg_doc_length = sum(len(doc) for doc in documents) / max(len(documents), 1)
	avg_doc_length = max(avg_doc_length, 1.0)
	document_frequencies: dict[str, int] = {}
	term_frequencies: list[dict[str, int]] = []
	for document in documents:
		counts: dict[str, int] = {}
		for token in document:
			counts[token] = counts.get(token, 0) + 1
		term_frequencies.append(counts)
		for token in counts:
			document_frequencies[token] = document_frequencies.get(token, 0) + 1

	unique_query_tokens = tuple(dict.fromkeys(query_tokens))
	corpus_size = len(documents)
	scores: list[float] = []
	for document, counts in zip(documents, term_frequencies):
		document_length = max(len(document), 1)
		score = 0.0
		for token in unique_query_tokens:
			frequency = counts.get(token, 0)
			if frequency <= 0:
				continue
			doc_frequency = document_frequencies.get(token, 0)
			if doc_frequency <= 0:
				continue
			idf = math.log(1.0 + ((corpus_size - doc_frequency + 0.5) / (doc_frequency + 0.5)))
			numerator = frequency * (_BM25_K1 + 1.0)
			denominator = frequency + _BM25_K1 * (
				1.0 - _BM25_B + _BM25_B * (document_length / avg_doc_length)
			)
			score += idf * (numerator / denominator)
		scores.append(score)
	return scores


class SemanticEncoderUnavailableError(RoleMatcherError):
	"""Raised at startup when a required semantic encoder could not load."""


def semantic_encoder_required(env: Mapping[str, str] | None = None) -> bool:
	"""Whether a missing semantic encoder should be fatal at startup.

	Read from the environment next to ``DISABLE_SEMANTIC_ENCODER`` rather than
	from ``AppSettings``, because the two are halves of one switch for this
	subsystem and splitting them across two modules would be worse than the
	small inconsistency with the project's usual settings home.
	"""

	source = os.environ if env is None else env
	return (source.get("REQUIRE_SEMANTIC_ENCODER") or "").strip().lower() in {
		"1",
		"true",
		"yes",
		"on",
	}


def warmup_semantic_encoder() -> bool:
	"""Load the shared SBERT encoder during app startup when possible.

	Returns True when the encoder is live, False when role matching will run on
	the token-overlap fallback instead.

	The fallback is a deliberate design choice, not a failure — it keeps local
	development and the test suite working without a model download. But it
	produces materially worse role matches, and a deployment that silently
	switches to it looks completely healthy: the process starts, ``/health``
	returns 200, and only the ``semantic_matching.active_backend`` field says
	anything is wrong.

	That is exactly what used to happen. ``backend/Dockerfile`` sets
	``HF_HUB_OFFLINE=1`` and claimed that changing the model name without
	updating the pre-fetch step meant "startup will fail rather than silently
	download". It did not: the load raised, the exception was logged and
	swallowed, and the app served degraded matches indefinitely.

	So the degradation is now logged as a warning that names the consequence,
	and a deployment can opt into making it fatal with
	``REQUIRE_SEMANTIC_ENCODER=true``. The default stays permissive so that
	nothing about local development or CI changes.
	"""

	if get_semantic_encoder() is not None:
		return True

	if semantic_encoder_required():
		raise SemanticEncoderUnavailableError(
			f"The semantic encoder {_SBERT_MODEL_NAME!r} could not be loaded and "
			"REQUIRE_SEMANTIC_ENCODER is set. Role matching would silently fall "
			"back to token overlap, which produces materially worse matches. "
			"Either make the model available (it is pre-fetched into the image "
			"by backend/Dockerfile — check the name matches) or unset "
			"REQUIRE_SEMANTIC_ENCODER to accept the fallback."
		)

	_LOGGER.warning(
		"Semantic encoder %r is unavailable; role matching is running on the "
		"token-overlap fallback, which produces materially worse matches. "
		"Set REQUIRE_SEMANTIC_ENCODER=true to make this fatal instead.",
		_SBERT_MODEL_NAME,
	)
	return False


def get_semantic_backend_status() -> dict[str, Any]:
	"""Report whether semantic matching is running on SBERT or token fallback."""

	encoder = get_semantic_encoder()
	bm25_okapi = _load_bm25_okapi()
	return {
		"active_backend": "sbert" if encoder is not None else "token_overlap",
		"sbert_ready": encoder is not None,
		"model": _SBERT_MODEL_NAME,
		"fallback_backend": "token_overlap",
		"bm25_enabled": True,
		"bm25_backend": "rank_bm25" if bm25_okapi is not None else "bm25_builtin",
		"bm25_library_available": bm25_okapi is not None,
	}


class RoleMatcher:
	"""Rank resume data against curated role profiles."""

	def __init__(
		self,
		*,
		role_profiles: Sequence[Mapping[str, Any] | RoleProfile] | None = None,
	) -> None:
		# role_profiles=None means "decide at rank time" (Groq or fallback)
		self._explicit_profiles = role_profiles
		self._semantic_backend = "token_overlap"
		self._bm25_backend = "bm25_builtin"
		self._profiles_source = "explicit" if role_profiles is not None else "pending"

	def _resolve_profiles(
		self,
		parsed_resume: Mapping[str, Any],
		use_groq: bool,
	) -> tuple[list[RoleProfile], str]:
		"""Resolve which role profiles to use and return them with a source label."""
		if self._explicit_profiles is not None:
			return (
				[_coerce_role_profile(p) for p in self._explicit_profiles],
				"curated_explicit",
			)

		if use_groq:
			generated = generate_role_profiles_with_groq(parsed_resume)
			if generated:
				return (
					[_coerce_role_profile(p) for p in generated],
					"groq_generated",
				)

		return (
			[_coerce_role_profile(p) for p in DEFAULT_ROLE_PROFILES],
			"curated_fallback",
		)

	def rank_roles(
		self,
		*,
		session_id: str,
		parsed_resume: Mapping[str, Any],
		max_roles: int = 5,
		persist: bool = False,
		use_groq_profiles: bool = True,
	) -> RoleMatchResult:
		role_profiles, profiles_source = self._resolve_profiles(parsed_resume, use_groq=use_groq_profiles)
		candidate = self._build_candidate(parsed_resume)
		bm25_scores = self._bm25_similarity_scores(
			candidate_text=str(candidate["candidate_text"]),
			role_profiles=role_profiles,
		)
		matches = [self._score_profile(candidate, profile, bm25_scores=bm25_scores) for profile in role_profiles]
		matches.sort(key=lambda item: item.match_percent, reverse=True)
		limited_matches = matches[: max(1, max_roles)]
		warning = self._build_signal_warning(limited_matches)
		if profiles_source == "groq_generated" and warning:
			warning = f"[Groq-generated roles] {warning}"
		elif profiles_source == "curated_fallback":
			warning = (warning + " " if warning else "") + "(Using curated fallback profiles — Groq role generation unavailable.)"

		if persist and limited_matches:
			save_role_matches(
				session_id,
				[
					{
						"role_key": match.role_key,
						"match_percent": match.match_percent,
						"skill_gaps": match.skill_gaps,
						"score_breakdown": match.score_breakdown,
					}
					for match in limited_matches
				],
			)

		return RoleMatchResult(
			session_id=session_id,
			matches=limited_matches,
			signal_warning=warning or None,
		)

	def match_and_store_roles(
		self,
		*,
		session_id: str,
		parsed_resume: Mapping[str, Any],
		max_roles: int = 5,
		use_groq_profiles: bool = True,
	) -> RoleMatchResult:
		return self.rank_roles(
			session_id=session_id,
			parsed_resume=parsed_resume,
			max_roles=max_roles,
			persist=True,
			use_groq_profiles=use_groq_profiles,
		)

	def _build_candidate(self, parsed_resume: Mapping[str, Any]) -> dict[str, Any]:
		skills = _normalize_skill_set(parsed_resume.get("skills"))
		technologies = _normalize_skill_set(parsed_resume.get("technologies"))
		combined_skills = skills | technologies

		projects = parsed_resume.get("projects") if isinstance(parsed_resume.get("projects"), list) else []
		project_text_parts: list[str] = []
		for project in projects:
			if not isinstance(project, Mapping):
				continue
			project_text_parts.extend(
				[
					_normalize_text(project.get("title")),
					_normalize_text(project.get("description")),
					" ".join(_normalize_string_tuple(project.get("tech_stack"))),
					" ".join(_normalize_string_tuple(project.get("outcomes"))),
					_normalize_text(project.get("role")),
				]
			)

		candidate_text = " ".join(
			part
			for part in [
				_normalize_text(parsed_resume.get("summary")),
				" ".join(sorted(combined_skills)),
				" ".join(project_text_parts),
				" ".join(_normalize_string_tuple(parsed_resume.get("interests"))),
			]
			if part
		)

		return {
			"skills": combined_skills,
			"candidate_text": candidate_text,
			"project_tokens": _tokenize(" ".join(project_text_parts)),
		}

	def _score_profile(
		self,
		candidate: Mapping[str, Any],
		profile: RoleProfile,
		*,
		bm25_scores: Mapping[str, float],
	) -> RoleMatch:
		candidate_skills = set(candidate["skills"])
		required_skills = set(profile.required_skills)
		bonus_skills = set(profile.bonus_skills)
		project_signals = set(profile.project_signals) | required_skills | bonus_skills

		required_overlap = len(candidate_skills & required_skills)
		bonus_overlap = len(candidate_skills & bonus_skills)
		project_overlap = len(set(candidate["project_tokens"]) & project_signals)
		skill_overlap_score = _safe_ratio(required_overlap, len(required_skills))
		bonus_skills_score = _safe_ratio(bonus_overlap, len(bonus_skills))
		project_relevance_score = _safe_ratio(project_overlap, len(project_signals))
		semantic_match_score = self._semantic_similarity(
			candidate_text=str(candidate["candidate_text"]),
			profile=profile,
		)
		bm25_match_score = max(0.0, min(1.0, float(bm25_scores.get(profile.key, 0.0))))

		final_score = (
			skill_overlap_score * _ROLE_SKILL_WEIGHT
			+ semantic_match_score * _ROLE_SEMANTIC_WEIGHT
			+ bm25_match_score * _ROLE_BM25_WEIGHT
			+ project_relevance_score * _ROLE_PROJECT_WEIGHT
			+ bonus_skills_score * _ROLE_BONUS_WEIGHT
		) * 100

		return RoleMatch(
			role_key=profile.key,
			title=profile.title,
			description=profile.description,
			match_percent=round(final_score, 2),
			skill_gaps=sorted(required_skills - candidate_skills),
			score_breakdown={
				"skill_overlap": round(skill_overlap_score * 100, 2),
				"semantic_match": round(semantic_match_score * 100, 2),
				"bm25_match": round(bm25_match_score * 100, 2),
				"project_relevance": round(project_relevance_score * 100, 2),
				"bonus_skills": round(bonus_skills_score * 100, 2),
				"semantic_backend": self._semantic_backend,
				"bm25_backend": self._bm25_backend,
			},
		)

	def _bm25_similarity_scores(
		self,
		*,
		candidate_text: str,
		role_profiles: Sequence[RoleProfile],
	) -> dict[str, float]:
		query_tokens = _tokenize_sequence(candidate_text)
		if not query_tokens or not role_profiles:
			return {profile.key: 0.0 for profile in role_profiles}

		documents = [_tokenize_sequence(_build_profile_text(profile)) for profile in role_profiles]
		bm25_okapi = _load_bm25_okapi()
		raw_scores: list[float]
		if bm25_okapi is not None:
			try:
				model = bm25_okapi(documents, k1=_BM25_K1, b=_BM25_B)
				raw_scores = [float(score) for score in model.get_scores(query_tokens)]
				self._bm25_backend = "rank_bm25"
			except Exception:
				_LOGGER.exception("rank_bm25 scoring failed; falling back to built-in BM25.")
				raw_scores = _compute_builtin_bm25_scores(query_tokens=query_tokens, documents=documents)
				self._bm25_backend = "bm25_builtin"
		else:
			raw_scores = _compute_builtin_bm25_scores(query_tokens=query_tokens, documents=documents)
			self._bm25_backend = "bm25_builtin"

		positive_scores = [max(0.0, score) for score in raw_scores]
		max_score = max(positive_scores, default=0.0)
		if max_score <= 0.0:
			return {profile.key: 0.0 for profile in role_profiles}
		return {
			profile.key: round(positive_scores[index] / max_score, 4)
			for index, profile in enumerate(role_profiles)
		}

	def _semantic_similarity(self, *, candidate_text: str, profile: RoleProfile) -> float:
		profile_text = _build_profile_text(profile)
		encoder = self._load_semantic_encoder()
		if encoder is not None:
			try:
				embeddings = encoder.encode(
					[candidate_text, profile_text],
					normalize_embeddings=True,
				)
				self._semantic_backend = "sbert"
				return max(0.0, min(1.0, float(embeddings[0] @ embeddings[1])))
			except Exception:
				self._semantic_backend = "token_overlap"

		candidate_tokens = _tokenize(candidate_text)
		profile_tokens = _tokenize(profile_text)
		if not candidate_tokens or not profile_tokens:
			return 0.0
		intersection = len(candidate_tokens & profile_tokens)
		union = len(candidate_tokens | profile_tokens)
		self._semantic_backend = "token_overlap"
		return _safe_ratio(intersection, union)

	def _load_semantic_encoder(self) -> Any | None:
		return get_semantic_encoder()

	@staticmethod
	def _build_signal_warning(matches: Sequence[RoleMatch]) -> str | None:
		if len(matches) < 2:
			return None
		if abs(matches[0].match_percent - matches[1].match_percent) <= 3.0:
			return (
				"Top role scores are tightly clustered. The resume may not contain enough "
				"signal for a confident ranking."
			)
		return None


def rank_roles(
	*,
	session_id: str,
	parsed_resume: Mapping[str, Any],
	role_profiles: Sequence[Mapping[str, Any] | RoleProfile] | None = None,
	max_roles: int = 5,
	persist: bool = False,
	use_groq_profiles: bool = True,
) -> RoleMatchResult:
	"""Convenience wrapper for role ranking in service and route layers."""

	matcher = RoleMatcher(role_profiles=role_profiles)
	# If explicit profiles were supplied, never hit Groq regardless of the flag.
	resolved_use_groq = use_groq_profiles if role_profiles is None else False
	return matcher.rank_roles(
		session_id=session_id,
		parsed_resume=parsed_resume,
		max_roles=max_roles,
		persist=persist,
		use_groq_profiles=resolved_use_groq,
	)


def match_and_store_roles(
	*,
	session_id: str,
	parsed_resume: Mapping[str, Any],
	role_profiles: Sequence[Mapping[str, Any] | RoleProfile] | None = None,
	max_roles: int = 5,
	use_groq_profiles: bool | None = None,
) -> RoleMatchResult:
	"""Convenience wrapper that persists ranked role matches.

	`use_groq_profiles` is forwarded from the caller when supplied; explicit
	role_profiles still suppress Groq generation by default.
	"""

	matcher = RoleMatcher(role_profiles=role_profiles)
	resolved_use_groq = (role_profiles is None) if use_groq_profiles is None else bool(use_groq_profiles)
	return matcher.match_and_store_roles(
		session_id=session_id,
		parsed_resume=parsed_resume,
		max_roles=max_roles,
		use_groq_profiles=resolved_use_groq,
	)


def serialize_role_match_result(result: RoleMatchResult) -> dict[str, Any]:
	"""Convert role match output to a JSON-serializable dictionary."""

	return {
		"session_id": result.session_id,
		"signal_warning": result.signal_warning,
		"matches": [asdict(match) for match in result.matches],
	}


__all__ = [
	"DEFAULT_ROLE_PROFILES",
	"generate_role_profiles_with_groq",
	"RoleMatch",
	"RoleMatchResult",
	"RoleMatcher",
	"RoleMatcherError",
	"RoleProfile",
	"RoleProfileSchemaError",
	"get_semantic_backend_status",
	"get_semantic_encoder",
	"match_and_store_roles",
	"rank_roles",
	"serialize_role_match_result",
	"warmup_semantic_encoder",
]
