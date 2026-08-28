"""Predefined assessment bank and deterministic batch selection helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import random


class AssessmentBankError(RuntimeError):
	"""Raised when the curated assessment bank or role catalog is invalid."""


@dataclass(frozen=True, slots=True)
class AssessmentOption:
	id: str
	text: str


@dataclass(frozen=True, slots=True)
class AssessmentQuestion:
	question_id: str
	prompt: str
	options: tuple[AssessmentOption, ...]
	correct_option_id: str
	explanation: str
	role_keys: tuple[str, ...]
	role_families: tuple[str, ...]
	domains: tuple[str, ...]
	topic: str
	difficulty: str
	skill_tag: str
	question_type: str
	is_core: bool
	active: bool


@dataclass(frozen=True, slots=True)
class TopicRequirement:
	topic: str
	count: int
	difficulties: tuple[str, ...] = ("easy", "medium")


@dataclass(frozen=True, slots=True)
class AssessmentBlueprint:
	role_key: str
	title: str
	role_families: tuple[str, ...]
	domains: tuple[str, ...]
	core_count: int
	targeted_requirements: tuple[TopicRequirement, ...]


@dataclass(frozen=True, slots=True)
class AssessmentBatchQuestion:
	question_id: str
	prompt: str
	question_type: str
	topic: str
	difficulty: str
	skill_tag: str
	options: tuple[dict[str, str], ...]
	correct_option_id: str
	option_order: tuple[str, ...]
	selection_source: str
	is_core: bool


@dataclass(frozen=True, slots=True)
class AssessmentBatch:
	session_id: str
	role_key: str
	role_families: tuple[str, ...]
	domains: tuple[str, ...]
	total_questions: int
	questions: tuple[AssessmentBatchQuestion, ...]


_QUESTION_BANK_PATH = (
	Path(__file__).resolve().parent.parent / "data" / "assessments" / "question_bank.json"
)
_ROLE_CATALOG_PATH = (
	Path(__file__).resolve().parent.parent / "data" / "assessments" / "role_catalog.json"
)

_ROLE_KEY_ALIASES: dict[str, str] = {
	"ai_ml_engineer": "machine_learning_engineer",
	"ml_engineer": "machine_learning_engineer",
	"machine_learning": "machine_learning_engineer",
	"ai_ml": "machine_learning_engineer",
	"ai engineer": "ai_engineer",
	"machine learning engineer": "machine_learning_engineer",
	"analytics_engineer": "data_engineer",
	"business_intelligence_developer": "business_intelligence_analyst",
	"cloud_data_engineer": "data_engineer",
	"real_time_analytics_engineer": "data_engineer",
	"realtime_analytics_engineer": "data_engineer",
	# AI / intelligent-systems variants
	"intelligent_systems_developer": "machine_learning_engineer",
	"intelligent_systems_engineer": "machine_learning_engineer",
	"applied_ai_engineer": "ai_engineer",
	"applied_ml_engineer": "machine_learning_engineer",
	"generative_ai_engineer": "ai_engineer",
	"llm_engineer": "ai_engineer",
	# NLP / NLU variants
	"nlp_engineer": "machine_learning_engineer",
	"nlp_developer": "machine_learning_engineer",
	"natural_language_processing_engineer": "machine_learning_engineer",
	# Computer-vision variants
	"computer_vision_engineer": "machine_learning_engineer",
	"cv_engineer": "machine_learning_engineer",
	"image_processing_engineer": "machine_learning_engineer",
	# Data-science variants
	"data_science_engineer": "data_scientist",
	"junior_data_scientist": "data_scientist",
	# Frontend variants
	"frontend_developer": "frontend_react_developer",
	"ui_developer": "frontend_react_developer",
	"web_developer": "frontend_react_developer",
	# Backend variants
	"backend_developer": "backend_python_developer",
	"api_developer": "backend_python_developer",
	# DevOps / cloud variants
	"cloud_infrastructure_engineer": "cloud_engineer",
	"platform_engineer": "devops_engineer",
	"infrastructure_engineer": "devops_engineer",
	"mlops_engineer": "devops_engineer",
	# Security variants
	"security_engineer": "cybersecurity_analyst",
	"application_security_engineer": "cybersecurity_analyst",
}

_ROLE_TITLE_ALIASES: dict[str, str] = {
	"ai ml engineer": "machine_learning_engineer",
	"ml engineer": "machine_learning_engineer",
	"machine learning engineer": "machine_learning_engineer",
	"ai engineer": "ai_engineer",
	"analytics engineer": "data_engineer",
	"business intelligence developer": "business_intelligence_analyst",
	"cloud data engineer": "data_engineer",
	"real time analytics engineer": "data_engineer",
	"realtime analytics engineer": "data_engineer",
	"intelligent systems developer": "machine_learning_engineer",
	"intelligent systems engineer": "machine_learning_engineer",
	"applied ai engineer": "ai_engineer",
	"applied ml engineer": "machine_learning_engineer",
	"generative ai engineer": "ai_engineer",
	"llm engineer": "ai_engineer",
	"nlp engineer": "machine_learning_engineer",
	"natural language processing engineer": "machine_learning_engineer",
	"computer vision engineer": "machine_learning_engineer",
	"cv engineer": "machine_learning_engineer",
	"data science engineer": "data_scientist",
	"frontend developer": "frontend_react_developer",
	"ui developer": "frontend_react_developer",
	"web developer": "frontend_react_developer",
	"backend developer": "backend_python_developer",
	"cloud infrastructure engineer": "cloud_engineer",
	"platform engineer": "devops_engineer",
	"mlops engineer": "devops_engineer",
	"security engineer": "cybersecurity_analyst",
}

_GENERIC_ROLE_TOKENS = {
	"analyst",
	"associate",
	"developer",
	"engineer",
	"entry",
	"fresher",
	"intern",
	"junior",
	"level",
	"specialist",
}


def _coerce_string_tuple(values: object) -> tuple[str, ...]:
	if values is None:
		return ()
	if not isinstance(values, list):
		values = [values]
	result: list[str] = []
	seen: set[str] = set()
	for value in values:
		text = str(value).strip()
		if not text or text in seen:
			continue
		seen.add(text)
		result.append(text)
	return tuple(result)


def _normalize_role_lookup_key(value: object) -> str:
	text = str(value or "").strip().casefold().replace("-", "_")
	return "_".join(part for part in text.split("_") if part)


def _normalize_role_lookup_title(value: object) -> str:
	text = str(value or "").strip().casefold()
	characters: list[str] = []
	for char in text:
		characters.append(char if char.isalnum() else " ")
	return " ".join(part for part in "".join(characters).split())


def _tokenize_role_lookup(value: object) -> set[str]:
	return set(_normalize_role_lookup_title(value).split())


def _infer_role_key_from_tokens(catalog: Mapping[str, AssessmentBlueprint], candidate_tokens: set[str]) -> str | None:
	if not candidate_tokens:
		return None

	if {"business", "intelligence"}.issubset(candidate_tokens):
		return "business_intelligence_analyst"

	if "analytics" in candidate_tokens:
		if (
			"engineer" in candidate_tokens
			or {"real", "time"}.issubset(candidate_tokens)
			or "realtime" in candidate_tokens
			or "streaming" in candidate_tokens
			or "pipeline" in candidate_tokens
			or "pipelines" in candidate_tokens
		):
			return "data_engineer"
		return "data_analyst"

	if (
		"nlp" in candidate_tokens
		or "nlp_engineer" in candidate_tokens
		or {"natural", "language"}.issubset(candidate_tokens)
		or {"computer", "vision"}.issubset(candidate_tokens)
		or {"intelligent", "systems"}.issubset(candidate_tokens)
	):
		return "machine_learning_engineer"

	if (
		"llm" in candidate_tokens
		or {"generative", "ai"}.issubset(candidate_tokens)
		or {"applied", "ai"}.issubset(candidate_tokens)
	):
		return "ai_engineer"

	if (
		{"cloud", "data"}.issubset(candidate_tokens)
		or {"data", "engineer"}.issubset(candidate_tokens)
		or "etl" in candidate_tokens
		or "warehouse" in candidate_tokens
		or "warehousing" in candidate_tokens
		or "pipeline" in candidate_tokens
		or "pipelines" in candidate_tokens
	):
		return "data_engineer"

	if {"cloud", "engineer"}.issubset(candidate_tokens):
		return "cloud_engineer"

	best_role_key: str | None = None
	best_score = 0
	for blueprint in catalog.values():
		blueprint_tokens = _tokenize_role_lookup(blueprint.role_key) | _tokenize_role_lookup(blueprint.title)
		overlap = candidate_tokens & blueprint_tokens
		if not overlap:
			continue
		informative_overlap = overlap - _GENERIC_ROLE_TOKENS
		score = (len(informative_overlap) * 2) + len(overlap)
		if score > best_score:
			best_role_key = blueprint.role_key
			best_score = score

	return best_role_key if best_score >= 4 else None


def _intersects(left: Sequence[str], right: Sequence[str]) -> bool:
	return bool(set(left) & set(right))


def _validate_topic_requirement(raw_requirement: object, *, role_key: str) -> TopicRequirement:
	if not isinstance(raw_requirement, dict):
		raise AssessmentBankError(
			f"Role blueprint {role_key} contains an invalid topic requirement payload."
		)
	topic = str(raw_requirement.get("topic") or "").strip()
	count = raw_requirement.get("count")
	if not topic or not isinstance(count, int) or count <= 0:
		raise AssessmentBankError(
			f"Role blueprint {role_key} must define topic requirements with non-empty topic and positive count."
		)
	difficulties = _coerce_string_tuple(raw_requirement.get("difficulties") or ["easy", "medium"])
	if not difficulties:
		raise AssessmentBankError(
			f"Role blueprint {role_key} topic requirement {topic} must define at least one difficulty."
		)
	return TopicRequirement(topic=topic, count=count, difficulties=difficulties)


def _validate_blueprint(raw_blueprint: object) -> AssessmentBlueprint:
	if not isinstance(raw_blueprint, dict):
		raise AssessmentBankError("Each role in the role catalog must be a JSON object.")

	role_key = str(raw_blueprint.get("role_key") or "").strip()
	title = str(raw_blueprint.get("title") or "").strip()
	core_count = raw_blueprint.get("core_count")
	role_families = _coerce_string_tuple(raw_blueprint.get("role_families"))
	domains = _coerce_string_tuple(raw_blueprint.get("domains"))
	raw_requirements = raw_blueprint.get("targeted_requirements")

	if not role_key or not title:
		raise AssessmentBankError("Every role blueprint must define role_key and title.")
	if not isinstance(core_count, int) or core_count < 1:
		raise AssessmentBankError(
			f"Role blueprint {role_key} must define a positive integer core_count."
		)
	if not role_families:
		raise AssessmentBankError(
			f"Role blueprint {role_key} must define at least one role family."
		)
	if not domains:
		raise AssessmentBankError(
			f"Role blueprint {role_key} must define at least one domain."
		)
	if not isinstance(raw_requirements, list) or not raw_requirements:
		raise AssessmentBankError(
			f"Role blueprint {role_key} must define at least one targeted requirement."
		)

	requirements = tuple(
		_validate_topic_requirement(requirement, role_key=role_key)
		for requirement in raw_requirements
	)

	return AssessmentBlueprint(
		role_key=role_key,
		title=title,
		role_families=role_families,
		domains=domains,
		core_count=core_count,
		targeted_requirements=requirements,
	)


@lru_cache(maxsize=1)
def load_role_catalog() -> dict[str, AssessmentBlueprint]:
	"""Load and validate the external role catalog used by the assessment selector."""

	try:
		raw_payload = json.loads(_ROLE_CATALOG_PATH.read_text(encoding="utf-8"))
	except FileNotFoundError as exc:
		raise AssessmentBankError(
			f"Role catalog file not found at {_ROLE_CATALOG_PATH}."
		) from exc
	except json.JSONDecodeError as exc:
		raise AssessmentBankError("Role catalog JSON is invalid.") from exc

	raw_roles = raw_payload.get("roles") if isinstance(raw_payload, dict) else None
	if not isinstance(raw_roles, list) or not raw_roles:
		raise AssessmentBankError("Role catalog must define a non-empty 'roles' list.")

	roles: dict[str, AssessmentBlueprint] = {}
	for raw_role in raw_roles:
		blueprint = _validate_blueprint(raw_role)
		if blueprint.role_key in roles:
			raise AssessmentBankError(
				f"Duplicate role_key detected in role catalog: {blueprint.role_key}."
			)
		roles[blueprint.role_key] = blueprint

	return roles


def _validate_question(raw_question: object) -> AssessmentQuestion:
	if not isinstance(raw_question, dict):
		raise AssessmentBankError("Each assessment question must be a JSON object.")

	question_id = str(raw_question.get("question_id") or "").strip()
	prompt = str(raw_question.get("prompt") or "").strip()
	correct_option_id = str(raw_question.get("correct_option_id") or "").strip()
	explanation = str(raw_question.get("explanation") or "").strip()
	topic = str(raw_question.get("topic") or "").strip()
	difficulty = str(raw_question.get("difficulty") or "").strip()
	skill_tag = str(raw_question.get("skill_tag") or "").strip()
	question_type = str(raw_question.get("question_type") or "").strip()

	if not all(
		[
			question_id,
			prompt,
			correct_option_id,
			explanation,
			topic,
			difficulty,
			skill_tag,
			question_type,
		]
	):
		raise AssessmentBankError(
			f"Assessment question {question_id or '<unknown>'} is missing required fields."
		)

	raw_options = raw_question.get("options")
	if not isinstance(raw_options, list) or len(raw_options) < 2:
		raise AssessmentBankError(
			f"Assessment question {question_id} must define at least two options."
		)

	options: list[AssessmentOption] = []
	option_ids: set[str] = set()
	for raw_option in raw_options:
		if not isinstance(raw_option, dict):
			raise AssessmentBankError(
				f"Assessment question {question_id} has an invalid option payload."
			)
		option_id = str(raw_option.get("id") or "").strip()
		text = str(raw_option.get("text") or "").strip()
		if not option_id or not text:
			raise AssessmentBankError(
				f"Assessment question {question_id} has an option with missing id/text."
			)
		if option_id in option_ids:
			raise AssessmentBankError(
				f"Assessment question {question_id} has duplicate option id {option_id}."
			)
		option_ids.add(option_id)
		options.append(AssessmentOption(id=option_id, text=text))

	if correct_option_id not in option_ids:
		raise AssessmentBankError(
			f"Assessment question {question_id} has a correct_option_id that is not in options."
		)

	return AssessmentQuestion(
		question_id=question_id,
		prompt=prompt,
		options=tuple(options),
		correct_option_id=correct_option_id,
		explanation=explanation,
		role_keys=_coerce_string_tuple(raw_question.get("role_keys")),
		role_families=_coerce_string_tuple(raw_question.get("role_families")),
		domains=_coerce_string_tuple(raw_question.get("domains")),
		topic=topic,
		difficulty=difficulty,
		skill_tag=skill_tag,
		question_type=question_type,
		is_core=bool(raw_question.get("is_core", False)),
		active=bool(raw_question.get("active", True)),
	)


@lru_cache(maxsize=1)
def load_question_bank() -> tuple[AssessmentQuestion, ...]:
	"""Load and validate the curated shared question pool."""

	try:
		raw_payload = json.loads(_QUESTION_BANK_PATH.read_text(encoding="utf-8"))
	except FileNotFoundError as exc:
		raise AssessmentBankError(
			f"Assessment bank file not found at {_QUESTION_BANK_PATH}."
		) from exc
	except json.JSONDecodeError as exc:
		raise AssessmentBankError("Assessment bank JSON is invalid.") from exc

	raw_questions = raw_payload.get("questions") if isinstance(raw_payload, dict) else None
	if not isinstance(raw_questions, list) or not raw_questions:
		raise AssessmentBankError("Assessment bank must define a non-empty 'questions' list.")

	questions: list[AssessmentQuestion] = []
	seen_ids: set[str] = set()
	for raw_question in raw_questions:
		question = _validate_question(raw_question)
		if question.question_id in seen_ids:
			raise AssessmentBankError(
				f"Duplicate assessment question_id detected: {question.question_id}."
			)
		seen_ids.add(question.question_id)
		questions.append(question)

	return tuple(question for question in questions if question.active)


def get_assessment_blueprint(role_key: str) -> AssessmentBlueprint:
	"""Return the role profile used to assemble an assessment batch."""

	resolved_role_key = normalize_assessment_role_key(role_key)
	if not resolved_role_key:
		raise AssessmentBankError("role_key is required to build an assessment batch.")
	try:
		return load_role_catalog()[resolved_role_key]
	except KeyError as exc:
		raise AssessmentBankError(
			f"No assessment blueprint exists for role {resolved_role_key}."
		) from exc


def normalize_assessment_role_key(role_key: str, role_title: str | None = None) -> str:
	"""Resolve a raw role key/title to a supported assessment catalog key."""

	catalog = load_role_catalog()
	direct_key = str(role_key or "").strip()
	if direct_key in catalog:
		return direct_key

	normalized_key = _normalize_role_lookup_key(direct_key)
	aliased_key = _ROLE_KEY_ALIASES.get(normalized_key)
	if aliased_key:
		return aliased_key

	normalized_title = _normalize_role_lookup_title(role_title)
	if normalized_title:
		aliased_title = _ROLE_TITLE_ALIASES.get(normalized_title)
		if aliased_title:
			return aliased_title

	for blueprint in catalog.values():
		if _normalize_role_lookup_title(blueprint.title) == normalized_title:
			return blueprint.role_key
		if _normalize_role_lookup_key(blueprint.role_key) == normalized_key:
			return blueprint.role_key

	candidate_tokens = _tokenize_role_lookup(direct_key) | _tokenize_role_lookup(role_title)
	inferred_role_key = _infer_role_key_from_tokens(catalog, candidate_tokens)
	if inferred_role_key:
		return inferred_role_key

	return direct_key


def _shuffle_pool(
	questions: Iterable[AssessmentQuestion],
	*,
	rng: random.Random,
) -> list[AssessmentQuestion]:
	pool = list(questions)
	rng.shuffle(pool)
	return pool


def _matches_role(question: AssessmentQuestion, role_key: str) -> bool:
	return role_key in question.role_keys


def _matches_family(question: AssessmentQuestion, role_families: Sequence[str]) -> bool:
	return _intersects(question.role_families, role_families)


def _matches_domain(question: AssessmentQuestion, domains: Sequence[str]) -> bool:
	return _intersects(question.domains, domains)


def _pick_from_pool(
	pool: Sequence[AssessmentQuestion],
	*,
	count: int,
	selected_ids: set[str],
	rng: random.Random,
	selection_source: str,
) -> list[tuple[AssessmentQuestion, str]]:
	ordered_pool = _shuffle_pool(
		(question for question in pool if question.question_id not in selected_ids),
		rng=rng,
	)
	chosen: list[tuple[AssessmentQuestion, str]] = []
	for question in ordered_pool:
		if len(chosen) >= count:
			break
		selected_ids.add(question.question_id)
		chosen.append((question, selection_source))
	return chosen


def _build_tiered_pool(
	questions: Sequence[AssessmentQuestion],
	*,
	topic: str | None,
	difficulties: Sequence[str],
	role_key: str,
	role_families: Sequence[str],
	domains: Sequence[str],
	include_core: bool,
) -> tuple[list[AssessmentQuestion], list[AssessmentQuestion], list[AssessmentQuestion], list[AssessmentQuestion]]:
	matching_questions = [
		question
		for question in questions
		if question.difficulty in difficulties and (topic is None or question.topic == topic)
	]
	role_pool = [
		question
		for question in matching_questions
		if not question.is_core and _matches_role(question, role_key)
	]
	family_pool = [
		question
		for question in matching_questions
		if not question.is_core
		and not _matches_role(question, role_key)
		and _matches_family(question, role_families)
	]
	domain_pool = [
		question
		for question in matching_questions
		if not question.is_core
		and not _matches_role(question, role_key)
		and not _matches_family(question, role_families)
		and _matches_domain(question, domains)
	]
	core_pool = [question for question in matching_questions if include_core and question.is_core]
	return role_pool, family_pool, domain_pool, core_pool


def _fill_requirement(
	questions: Sequence[AssessmentQuestion],
	*,
	requirement: TopicRequirement,
	blueprint: AssessmentBlueprint,
	selected_ids: set[str],
	rng: random.Random,
) -> list[tuple[AssessmentQuestion, str]]:
	role_pool, family_pool, domain_pool, core_pool = _build_tiered_pool(
		questions,
		topic=requirement.topic,
		difficulties=requirement.difficulties,
		role_key=blueprint.role_key,
		role_families=blueprint.role_families,
		domains=blueprint.domains,
		include_core=True,
	)

	selected: list[tuple[AssessmentQuestion, str]] = []
	remaining = requirement.count
	for pool, source in (
		(role_pool, "role"),
		(family_pool, "family"),
		(domain_pool, "domain"),
		(core_pool, "core_fallback"),
	):
		if remaining <= 0:
			break
		chosen = _pick_from_pool(
			pool,
			count=remaining,
			selected_ids=selected_ids,
			rng=rng,
			selection_source=source,
		)
		selected.extend(chosen)
		remaining -= len(chosen)

	return selected


def _shuffle_options(
	question: AssessmentQuestion,
	*,
	session_id: str,
	role_key: str,
) -> tuple[tuple[dict[str, str], ...], tuple[str, ...]]:
	option_rng = random.Random(f"{session_id}:{role_key}:{question.question_id}:options")
	options = list(question.options)
	option_rng.shuffle(options)
	return (
		tuple({"id": option.id, "text": option.text} for option in options),
		tuple(option.id for option in options),
	)


def build_assessment_batch(
	*,
	session_id: str,
	role_key: str,
	total_questions: int = 8,
) -> AssessmentBatch:
	"""Build a deterministic role-aware assessment batch for one session."""

	resolved_session_id = str(session_id or "").strip()
	if not resolved_session_id:
		raise AssessmentBankError("session_id is required to build an assessment batch.")

	blueprint = get_assessment_blueprint(role_key)
	questions = load_question_bank()
	if total_questions <= blueprint.core_count:
		raise AssessmentBankError("total_questions must be greater than the blueprint core_count.")

	selector_rng = random.Random(f"{resolved_session_id}:{blueprint.role_key}:{total_questions}")
	selected_ids: set[str] = set()
	selected_pairs: list[tuple[AssessmentQuestion, str]] = []

	core_pool = [question for question in questions if question.is_core]
	selected_pairs.extend(
		_pick_from_pool(
			core_pool,
			count=blueprint.core_count,
			selected_ids=selected_ids,
			rng=selector_rng,
			selection_source="core",
		)
	)

	for requirement in blueprint.targeted_requirements:
		remaining_slots = total_questions - len(selected_pairs)
		if remaining_slots <= 0:
			break

		effective_requirement = requirement
		if requirement.count > remaining_slots:
			effective_requirement = TopicRequirement(
				topic=requirement.topic,
				count=remaining_slots,
				difficulties=requirement.difficulties,
			)

		selected_pairs.extend(
			_fill_requirement(
				questions,
				requirement=effective_requirement,
				blueprint=blueprint,
				selected_ids=selected_ids,
				rng=selector_rng,
			)
		)

	remaining = total_questions - len(selected_pairs)
	if remaining > 0:
		role_pool, family_pool, domain_pool, core_fallback_pool = _build_tiered_pool(
			questions,
			topic=None,
			difficulties=("easy", "medium", "hard"),
			role_key=blueprint.role_key,
			role_families=blueprint.role_families,
			domains=blueprint.domains,
			include_core=True,
		)
		for pool, source in (
			(role_pool, "role_fill"),
			(family_pool, "family_fill"),
			(domain_pool, "domain_fill"),
			(core_fallback_pool, "core_fill"),
		):
			if remaining <= 0:
				break
			chosen = _pick_from_pool(
				pool,
				count=remaining,
				selected_ids=selected_ids,
				rng=selector_rng,
				selection_source=source,
			)
			selected_pairs.extend(chosen)
			remaining -= len(chosen)

	if len(selected_pairs) != total_questions:
		raise AssessmentBankError(
			f"Assessment batch for role {blueprint.role_key} could not reach {total_questions} questions."
		)

	batch_questions: list[AssessmentBatchQuestion] = []
	for question, selection_source in selected_pairs:
		shuffled_options, option_order = _shuffle_options(
			question,
			session_id=resolved_session_id,
			role_key=blueprint.role_key,
		)
		batch_questions.append(
			AssessmentBatchQuestion(
				question_id=question.question_id,
				prompt=question.prompt,
				question_type=question.question_type,
				topic=question.topic,
				difficulty=question.difficulty,
				skill_tag=question.skill_tag,
				options=shuffled_options,
				correct_option_id=question.correct_option_id,
				option_order=option_order,
				selection_source=selection_source,
				is_core=question.is_core,
			)
		)

	return AssessmentBatch(
		session_id=resolved_session_id,
		role_key=blueprint.role_key,
		role_families=blueprint.role_families,
		domains=blueprint.domains,
		total_questions=total_questions,
		questions=tuple(batch_questions),
	)


__all__ = [
	"AssessmentBankError",
	"AssessmentBatch",
	"AssessmentBatchQuestion",
	"AssessmentBlueprint",
	"AssessmentQuestion",
	"build_assessment_batch",
	"get_assessment_blueprint",
	"load_question_bank",
	"load_role_catalog",
	"normalize_assessment_role_key",
]