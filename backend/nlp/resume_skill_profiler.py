"""Resume skill depth profiling for adaptive technical question generation.

Phase 1 stays local to resume analysis: the profiler reads parsed resume JSON,
looks up the selected role's required and bonus skills, and assigns a depth
score per skill using lightweight NLP signals already available in the repo.

Signals blended per skill:
  - TF-IDF prominence across resume segments
  - Exact skill / technology matches from structured fields and NER
  - Project proximity to action verbs as a hands-on depth proxy
  - Optional SBERT similarity for soft-gap detection
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import import_module
import logging
import math
import re
from typing import Any

from backend.nlp.ner_extractor import extract_entities
from backend.nlp.role_matcher import DEFAULT_ROLE_PROFILES, get_semantic_encoder

_LOGGER = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")
_SENTENCE_RE = re.compile(r"[.!?;\n]+")
_ACTION_VERB_RE = re.compile(
		r"\b(built|build|designed|implemented|developed|deployed|created|optimized|improved|"
		r"integrated|automated|engineered|delivered|trained|evaluated|analyzed)\b",
		re.IGNORECASE,
)
_SOFT_GAP_THRESHOLD = 0.45


@dataclass(frozen=True)
class SkillDepthScore:
	"""Depth breakdown for one role-relevant skill."""

	skill: str
	depth_score: float
	tier: str
	tfidf_score: float
	exact_match_score: float
	proximity_score: float
	sbert_score: float
	evidence: list[str]

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass(frozen=True)
class SkillProfile:
	"""Resume-conditioned skill profile for the selected role."""

	role_key: str
	role_title: str
	skill_scores: dict[str, SkillDepthScore]
	strong_skills: list[str]
	familiar_skills: list[str]
	mentioned_skills: list[str]
	absent_skills: list[str]
	soft_gap_skills: list[str]
	priority_focus_areas: list[str]
	tfidf_backend: str
	semantic_backend: str
	ner_mode: str

	def to_dict(self) -> dict[str, Any]:
		return {
			"role_key": self.role_key,
			"role_title": self.role_title,
			"skill_scores": {
				skill: score.to_dict()
				for skill, score in self.skill_scores.items()
			},
			"strong_skills": list(self.strong_skills),
			"familiar_skills": list(self.familiar_skills),
			"mentioned_skills": list(self.mentioned_skills),
			"absent_skills": list(self.absent_skills),
			"soft_gap_skills": list(self.soft_gap_skills),
			"priority_focus_areas": list(self.priority_focus_areas),
			"tfidf_backend": self.tfidf_backend,
			"semantic_backend": self.semantic_backend,
			"ner_mode": self.ner_mode,
		}


def _normalize_phrase(value: str) -> str:
	return " ".join(_TOKEN_RE.findall((value or "").casefold()))


def _tokenize(value: str) -> tuple[str, ...]:
	return tuple(_TOKEN_RE.findall((value or "").casefold()))


def _coerce_text_list(value: Any) -> list[str]:
	if not isinstance(value, list):
		return []
		
	items: list[str] = []
	for raw_item in value:
		text = str(raw_item or "").strip()
		if text:
			items.append(text)
	return items


def _unique_preserve_order(values: Sequence[str]) -> list[str]:
	seen: set[str] = set()
	result: list[str] = []
	for value in values:
		normalized = _normalize_phrase(value)
		if not normalized or normalized in seen:
			continue
		seen.add(normalized)
		result.append(value)
	return result


def _build_candidate_segments(parsed_resume: Mapping[str, Any]) -> tuple[list[str], list[str]]:
	segments: list[str] = []
	project_segments: list[str] = []

	for field in ("summary", "location"):
		text = str(parsed_resume.get(field) or "").strip()
		if text:
			segments.append(text)

	for field in ("skills", "technologies", "interests"):
		items = _coerce_text_list(parsed_resume.get(field))
		if items:
			segments.append(" ".join(items))

	projects = parsed_resume.get("projects")
	if isinstance(projects, list):
		for project in projects:
			if not isinstance(project, Mapping):
				continue
			project_bits = [
				str(project.get("title") or "").strip(),
				str(project.get("description") or "").strip(),
				str(project.get("role") or "").strip(),
			]
			project_bits.extend(_coerce_text_list(project.get("tech_stack")))
			project_bits.extend(_coerce_text_list(project.get("outcomes")))
			project_text = " ".join(bit for bit in project_bits if bit)
			if project_text:
				segments.append(project_text)
				project_segments.append(project_text)

	certifications = parsed_resume.get("certifications")
	if isinstance(certifications, list):
		for certification in certifications:
			if not isinstance(certification, Mapping):
				continue
			cert_text = " ".join(
				part
				for part in (
					str(certification.get("title") or "").strip(),
					str(certification.get("platform") or "").strip(),
					str(certification.get("status") or "").strip(),
				)
				if part
			)
			if cert_text:
				segments.append(cert_text)

	return segments, project_segments


def _resolve_role_profile(role_key: str) -> Mapping[str, Any]:
	normalized_role_key = _normalize_phrase(role_key.replace("_", " "))
	for profile in DEFAULT_ROLE_PROFILES:
		if _normalize_phrase(str(profile.get("key") or "").replace("_", " ")) == normalized_role_key:
			return profile

	for profile in DEFAULT_ROLE_PROFILES:
		profile_key = _normalize_phrase(str(profile.get("key") or "").replace("_", " "))
		if normalized_role_key and normalized_role_key in profile_key:
			return profile

	return DEFAULT_ROLE_PROFILES[0]


@lru_cache(maxsize=1)
def _load_tfidf_vectorizer() -> Any | None:
	try:
		text_module = import_module("sklearn.feature_extraction.text")
	except ModuleNotFoundError:
		return None
	return getattr(text_module, "TfidfVectorizer", None)


def _vectorizer_tokenizer(text: str) -> list[str]:
	return list(_tokenize(text))


def _fallback_lexical_skill_score(skill: str, documents: Sequence[str]) -> float:
	skill_norm = _normalize_phrase(skill)
	skill_tokens = set(_tokenize(skill))
	if not skill_norm or not documents:
		return 0.0

	best = 0.0
	for document in documents:
		document_norm = _normalize_phrase(document)
		if skill_norm and skill_norm in document_norm:
			return 1.0
		doc_tokens = set(_tokenize(document))
		if not doc_tokens or not skill_tokens:
			continue
		overlap = len(skill_tokens & doc_tokens) / max(len(skill_tokens), 1)
		best = max(best, overlap)
	return max(0.0, min(1.0, best))


def _compute_tfidf_skill_score(skill: str, documents: Sequence[str]) -> tuple[float, str]:
	vectorizer_cls = _load_tfidf_vectorizer()
	if vectorizer_cls is None or not documents:
		return _fallback_lexical_skill_score(skill, documents), "lexical_overlap"

	try:
		vectorizer = vectorizer_cls(
			lowercase=False,
			tokenizer=_vectorizer_tokenizer,
			token_pattern=None,
			ngram_range=(1, 3),
		)
		doc_matrix = vectorizer.fit_transform(documents)
		skill_matrix = vectorizer.transform([skill])
		if getattr(skill_matrix, "nnz", 0) == 0:
			return 0.0, "tfidf"
		similarities = doc_matrix @ skill_matrix.T
		best = float(similarities.max()) if similarities.shape[0] else 0.0
		return max(0.0, min(1.0, best)), "tfidf"
	except Exception:
		_LOGGER.exception("TF-IDF skill profiling failed; falling back to lexical overlap.")
		return _fallback_lexical_skill_score(skill, documents), "lexical_overlap"


def _build_signal_sources(
	parsed_resume: Mapping[str, Any],
	candidate_text: str,
) -> tuple[dict[str, set[str]], str]:
	sources: dict[str, set[str]] = {}

	def add_values(values: Sequence[str], source: str) -> None:
		for value in values:
			normalized = _normalize_phrase(value)
			if not normalized:
				continue
			sources.setdefault(normalized, set()).add(source)

	add_values(_coerce_text_list(parsed_resume.get("skills")), "skills")
	add_values(_coerce_text_list(parsed_resume.get("technologies")), "technologies")

	projects = parsed_resume.get("projects")
	if isinstance(projects, list):
		for project in projects:
			if not isinstance(project, Mapping):
				continue
			add_values(_coerce_text_list(project.get("tech_stack")), "project_tech_stack")
			add_values(_coerce_text_list(project.get("outcomes")), "project_outcomes")

	ner_result = extract_entities(candidate_text)
	add_values(ner_result.technologies, f"ner:{ner_result.ner_mode}:technology")
	add_values(ner_result.skills, f"ner:{ner_result.ner_mode}:skill")
	return sources, ner_result.ner_mode


def _compute_exact_signal(
	skill: str,
	signal_sources: Mapping[str, set[str]],
	candidate_text_norm: str,
) -> tuple[float, list[str]]:
	skill_norm = _normalize_phrase(skill)
	if not skill_norm:
		return 0.0, []

	if skill_norm in signal_sources:
		return 1.0, sorted(signal_sources[skill_norm])

	if skill_norm in candidate_text_norm:
		return 0.75, ["resume_text"]

	skill_tokens = set(skill_norm.split())
	for candidate_signal, sources in signal_sources.items():
		candidate_tokens = set(candidate_signal.split())
		if skill_tokens and skill_tokens.issubset(candidate_tokens):
			return 0.65, sorted(sources)

	return 0.0, []


def _compute_proximity_signal(skill: str, project_segments: Sequence[str]) -> float:
	skill_norm = _normalize_phrase(skill)
	skill_tokens = set(_tokenize(skill))
	if not skill_norm or not project_segments:
		return 0.0

	best = 0.0
	for project_text in project_segments:
		project_norm = _normalize_phrase(project_text)
		if not project_norm:
			continue
		for sentence in _SENTENCE_RE.split(project_text):
			sentence_norm = _normalize_phrase(sentence)
			if not sentence_norm:
				continue
			sentence_tokens = set(_tokenize(sentence))
			contains_skill = skill_norm in sentence_norm or (
				bool(skill_tokens) and skill_tokens.issubset(sentence_tokens)
			)
			if not contains_skill:
				continue
			if _ACTION_VERB_RE.search(sentence):
				return 1.0
			best = max(best, 0.6)

		project_tokens = set(_tokenize(project_text))
		if skill_norm in project_norm or (skill_tokens and skill_tokens.issubset(project_tokens)):
			best = max(best, 0.45)

	return best


def _coerce_embedding(vector: Any) -> tuple[float, ...]:
	if hasattr(vector, "tolist"):
		vector = vector.tolist()
	return tuple(float(value) for value in vector)


def _cosine_similarity(left: Any, right: Any) -> float:
	left_vector = _coerce_embedding(left)
	right_vector = _coerce_embedding(right)
	if not left_vector or not right_vector or len(left_vector) != len(right_vector):
		return 0.0

	dot_product = sum(a * b for a, b in zip(left_vector, right_vector))
	left_norm = math.sqrt(sum(value * value for value in left_vector))
	right_norm = math.sqrt(sum(value * value for value in right_vector))
	if left_norm <= 0.0 or right_norm <= 0.0:
		return 0.0
	return max(0.0, min(1.0, dot_product / (left_norm * right_norm)))


def _compute_semantic_scores(
	role_skills: Sequence[str],
	candidate_text: str,
) -> tuple[dict[str, float], str]:
	encoder = get_semantic_encoder()
	if encoder is None or not candidate_text.strip() or not role_skills:
		return {skill: 0.0 for skill in role_skills}, "disabled"

	try:
		payload = [candidate_text, *role_skills]
		try:
			embeddings = encoder.encode(payload, normalize_embeddings=True)
		except TypeError:
			embeddings = encoder.encode(payload)
		candidate_embedding = embeddings[0]
		return {
			skill: _cosine_similarity(candidate_embedding, embeddings[index])
			for index, skill in enumerate(role_skills, start=1)
		}, "sbert"
	except Exception:
		_LOGGER.exception("SBERT skill profiling failed; semantic signal disabled.")
		return {skill: 0.0 for skill in role_skills}, "disabled"


def _tier_from_score(score: float) -> str:
	if score >= 0.65:
		return "strong"
	if score >= 0.35:
		return "familiar"
	if score >= 0.10:
		return "mentioned"
	return "absent"


def _build_priority_focus_areas(
	role_skills: Sequence[str],
	skill_scores: Mapping[str, SkillDepthScore],
) -> list[str]:
	grouped: dict[str, list[str]] = {
		"absent": [],
		"familiar": [],
		"mentioned": [],
		"strong": [],
	}
	for skill in role_skills:
		score = skill_scores.get(skill)
		if score is None:
			continue
		grouped.setdefault(score.tier, []).append(skill)
	return [
		*grouped["absent"],
		*grouped["familiar"],
		*grouped["mentioned"],
		*grouped["strong"],
	]


def profile_resume_skills(
	parsed_resume: Mapping[str, Any],
	role_key: str,
) -> SkillProfile:
	"""Profile resume skill depth against the selected role."""

	role_profile = _resolve_role_profile(role_key)
	role_title = str(role_profile.get("title") or role_key.replace("_", " ").title())
	role_skills = _unique_preserve_order([
		*list(role_profile.get("required_skills") or []),
		*list(role_profile.get("bonus_skills") or []),
	])

	segments, project_segments = _build_candidate_segments(parsed_resume)
	candidate_text = "\n".join(segment for segment in segments if segment)
	candidate_text_norm = _normalize_phrase(candidate_text)
	signal_sources, ner_mode = _build_signal_sources(parsed_resume, candidate_text)
	semantic_scores, semantic_backend = _compute_semantic_scores(role_skills, candidate_text)

	skill_scores: dict[str, SkillDepthScore] = {}
	strong_skills: list[str] = []
	familiar_skills: list[str] = []
	mentioned_skills: list[str] = []
	absent_skills: list[str] = []
	soft_gap_skills: list[str] = []
	tfidf_backend = "lexical_overlap"

	for skill in role_skills:
		tfidf_score, tfidf_backend = _compute_tfidf_skill_score(skill, segments)
		exact_match_score, evidence = _compute_exact_signal(skill, signal_sources, candidate_text_norm)
		proximity_score = _compute_proximity_signal(skill, project_segments)
		semantic_score = max(0.0, min(1.0, semantic_scores.get(skill, 0.0)))
		depth_score = max(
			0.0,
			min(
				1.0,
				(tfidf_score * 0.40)
				+ (exact_match_score * 0.30)
				+ (proximity_score * 0.20)
				+ (semantic_score * 0.10),
			),
		)
		tier = _tier_from_score(depth_score)
		score = SkillDepthScore(
			skill=skill,
			depth_score=depth_score,
			tier=tier,
			tfidf_score=tfidf_score,
			exact_match_score=exact_match_score,
			proximity_score=proximity_score,
			sbert_score=semantic_score,
			evidence=evidence,
		)
		skill_scores[skill] = score

		if tier == "strong":
			strong_skills.append(skill)
		elif tier == "familiar":
			familiar_skills.append(skill)
		elif tier == "mentioned":
			mentioned_skills.append(skill)
		else:
			absent_skills.append(skill)

		if exact_match_score <= 0.0 and semantic_score >= _SOFT_GAP_THRESHOLD:
			soft_gap_skills.append(skill)

	return SkillProfile(
		role_key=str(role_profile.get("key") or role_key),
		role_title=role_title,
		skill_scores=skill_scores,
		strong_skills=strong_skills,
		familiar_skills=familiar_skills,
		mentioned_skills=mentioned_skills,
		absent_skills=absent_skills,
		soft_gap_skills=soft_gap_skills,
		priority_focus_areas=_build_priority_focus_areas(role_skills, skill_scores),
		tfidf_backend=tfidf_backend,
		semantic_backend=semantic_backend,
		ner_mode=ner_mode,
	)


__all__ = [
	"SkillDepthScore",
	"SkillProfile",
	"profile_resume_skills",
]