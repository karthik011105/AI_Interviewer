"""Certified DSA problem bank loading and deterministic selection helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


_PROBLEM_BANK_DIR = Path(__file__).resolve().parent.parent / "data" / "problems"
_PROBLEM_BANK_GLOB = "*_bank.json"

_DSA_REQUIRED_ROLE_KEYS = frozenset(
	{
		"software_engineer",
		"backend_python_developer",
		"backend_java_developer",
		"backend_node_developer",
		"frontend_react_developer",
		"full_stack_developer",
		"mobile_app_developer",
		"data_engineer",
		"machine_learning_engineer",
		"ai_engineer",
		"embedded_systems_engineer",
		"iot_engineer",
		"robotics_software_engineer",
		"site_reliability_engineer",
		"firmware_engineer",
	}
)

_ROLE_KEY_ALIASES: dict[str, str] = {
	"backend_developer": "backend_python_developer",
	"frontend_developer": "frontend_react_developer",
	"web_developer": "frontend_react_developer",
	"fullstack_developer": "full_stack_developer",
	"platform_engineer": "site_reliability_engineer",
	"sre": "site_reliability_engineer",
	"ml_engineer": "machine_learning_engineer",
	"ai_ml_engineer": "machine_learning_engineer",
	"embedded_engineer": "embedded_systems_engineer",
	"robotics_engineer": "robotics_software_engineer",
}

_Q1_DIFFICULTY_ORDER = ("easy", "easy_medium", "medium")
_Q2_DIFFICULTY_ORDER = ("medium", "medium_hard", "hard", "easy_medium")


class CertifiedProblemBankError(RuntimeError):
	"""Raised when the certified DSA bank is missing or invalid."""


class ProblemSelectionError(RuntimeError):
	"""Raised when no certified DSA problem can be selected."""


@dataclass(frozen=True, slots=True)
class ProblemExample:
	input_text: str
	output_text: str
	explanation: str = ""


@dataclass(frozen=True, slots=True)
class HiddenTestCase:
	input_text: str
	output_text: str


@dataclass(frozen=True, slots=True)
class CertifiedProblem:
	problem_id: str
	title: str
	statement: str
	constraints: tuple[str, ...]
	source: str
	source_problem_id: str
	source_url: str
	rating: int | None
	tags: tuple[str, ...]
	roles: tuple[str, ...]
	difficulty: str
	starter_code: str
	reference_solution: str
	brute_force_solution: str | None
	examples: tuple[ProblemExample, ...]
	hidden_test_cases: tuple[HiddenTestCase, ...]
	hints: tuple[str, ...]
	followup_questions: tuple[str, ...]


def _coerce_string_tuple(values: object) -> tuple[str, ...]:
	if values is None:
		return ()
	if not isinstance(values, list):
		values = [values]
	result: list[str] = []
	seen: set[str] = set()
	for value in values:
		text = str(value or "").strip()
		if not text or text in seen:
			continue
		seen.add(text)
		result.append(text)
	return tuple(result)


def _normalize_role_key(value: object) -> str:
	text = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
	text = "_".join(part for part in text.split("_") if part)
	return _ROLE_KEY_ALIASES.get(text, text)


def _validate_role_set_map(raw_role_sets: object) -> dict[str, tuple[str, ...]]:
	if raw_role_sets is None:
		return {}
	if not isinstance(raw_role_sets, dict):
		raise CertifiedProblemBankError("role_sets must be a JSON object keyed by role-set name.")
	validated: dict[str, tuple[str, ...]] = {}
	for role_set_name, role_values in raw_role_sets.items():
		name = str(role_set_name or "").strip()
		if not name:
			raise CertifiedProblemBankError("role_sets cannot contain blank names.")
		roles = tuple(_normalize_role_key(role) for role in _coerce_string_tuple(role_values))
		if not roles:
			raise CertifiedProblemBankError(f"Role set {name} must contain at least one role.")
		validated[name] = roles
	return validated


def _validate_example(raw_example: object, *, problem_id: str, collection_name: str) -> ProblemExample:
	if not isinstance(raw_example, dict):
		raise CertifiedProblemBankError(f"Problem {problem_id} has a non-object entry in {collection_name}.")
	input_text = str(raw_example.get("input_text") or "")
	output_text = str(raw_example.get("output_text") or "")
	if not input_text or not output_text:
		raise CertifiedProblemBankError(
			f"Problem {problem_id} requires non-empty input_text and output_text in {collection_name}."
		)
	return ProblemExample(
		input_text=input_text,
		output_text=output_text,
		explanation=str(raw_example.get("explanation") or ""),
	)


def _validate_hidden_test_case(raw_case: object, *, problem_id: str) -> HiddenTestCase:
	if not isinstance(raw_case, dict):
		raise CertifiedProblemBankError(f"Problem {problem_id} has a non-object hidden test case.")
	input_text = str(raw_case.get("input_text") or "")
	output_text = str(raw_case.get("output_text") or "")
	if not input_text or not output_text:
		raise CertifiedProblemBankError(
			f"Problem {problem_id} requires non-empty input_text and output_text for hidden tests."
		)
	return HiddenTestCase(input_text=input_text, output_text=output_text)


def _resolve_problem_roles(
	raw_problem: Mapping[str, Any],
	*,
	problem_id: str,
	role_sets: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
	roles = {_normalize_role_key(role) for role in _coerce_string_tuple(raw_problem.get("roles"))}
	role_set_name = str(raw_problem.get("role_set") or "").strip()
	if role_set_name:
		if role_set_name not in role_sets:
			raise CertifiedProblemBankError(
				f"Problem {problem_id} references unknown role_set {role_set_name}."
			)
		roles.update(role_sets[role_set_name])
	if not roles:
		raise CertifiedProblemBankError(f"Problem {problem_id} must define roles or role_set.")
	unknown_roles = sorted(role for role in roles if role not in _DSA_REQUIRED_ROLE_KEYS)
	if unknown_roles:
		raise CertifiedProblemBankError(
			f"Problem {problem_id} contains unsupported DSA role keys: {', '.join(unknown_roles)}."
		)
	return tuple(sorted(roles))


def _validate_problem(
	raw_problem: object,
	*,
	role_sets: Mapping[str, tuple[str, ...]],
) -> CertifiedProblem:
	if not isinstance(raw_problem, dict):
		raise CertifiedProblemBankError("Each certified problem must be a JSON object.")

	problem_id = str(raw_problem.get("problem_id") or "").strip()
	title = str(raw_problem.get("title") or "").strip()
	statement = str(raw_problem.get("statement") or "").strip()
	constraints = _coerce_string_tuple(raw_problem.get("constraints"))
	source = str(raw_problem.get("source") or "").strip()
	source_problem_id = str(raw_problem.get("source_problem_id") or "").strip()
	source_url = str(raw_problem.get("source_url") or "").strip()
	tags = _coerce_string_tuple(raw_problem.get("tags"))
	difficulty = str(raw_problem.get("difficulty") or "").strip()
	starter_code = str(raw_problem.get("starter_code") or "")
	reference_solution = str(raw_problem.get("reference_solution") or "")
	brute_force_solution = raw_problem.get("brute_force_solution")
	hints = _coerce_string_tuple(raw_problem.get("hints"))
	followup_questions = _coerce_string_tuple(raw_problem.get("followup_questions"))
	raw_examples = raw_problem.get("examples")
	raw_hidden_cases = raw_problem.get("hidden_test_cases")

	if not all([problem_id, title, statement, source, source_problem_id, source_url, difficulty]):
		raise CertifiedProblemBankError(
			"Each certified problem must define problem_id, title, statement, source, source_problem_id, source_url, and difficulty."
		)
	if not constraints:
		raise CertifiedProblemBankError(f"Problem {problem_id} must define at least one constraint.")
	if not tags:
		raise CertifiedProblemBankError(f"Problem {problem_id} must define at least one tag.")
	if not starter_code.strip():
		raise CertifiedProblemBankError(f"Problem {problem_id} must define starter_code.")
	if not reference_solution.strip():
		raise CertifiedProblemBankError(f"Problem {problem_id} must define reference_solution.")
	if len(hints) < 3:
		raise CertifiedProblemBankError(f"Problem {problem_id} must provide at least 3 hints.")
	if not followup_questions:
		raise CertifiedProblemBankError(f"Problem {problem_id} must provide at least one follow-up question.")
	if not isinstance(raw_examples, list) or not raw_examples:
		raise CertifiedProblemBankError(f"Problem {problem_id} must provide at least one example.")
	if not isinstance(raw_hidden_cases, list) or len(raw_hidden_cases) < 3:
		raise CertifiedProblemBankError(f"Problem {problem_id} must provide at least 3 hidden test cases.")

	roles = _resolve_problem_roles(raw_problem, problem_id=problem_id, role_sets=role_sets)
	examples = tuple(
		_validate_example(raw_example, problem_id=problem_id, collection_name="examples")
		for raw_example in raw_examples
	)
	hidden_test_cases = tuple(
		_validate_hidden_test_case(raw_case, problem_id=problem_id)
		for raw_case in raw_hidden_cases
	)

	rating: int | None = None
	raw_rating = raw_problem.get("rating")
	if raw_rating is not None and raw_rating != "":
		if not isinstance(raw_rating, int):
			raise CertifiedProblemBankError(f"Problem {problem_id} rating must be an integer when provided.")
		rating = raw_rating

	if brute_force_solution is not None:
		brute_force_solution = str(brute_force_solution or "").strip() or None

	return CertifiedProblem(
		problem_id=problem_id,
		title=title,
		statement=statement,
		constraints=constraints,
		source=source,
		source_problem_id=source_problem_id,
		source_url=source_url,
		rating=rating,
		tags=tags,
		roles=roles,
		difficulty=difficulty,
		starter_code=starter_code,
		reference_solution=reference_solution,
		brute_force_solution=brute_force_solution,
		examples=examples,
		hidden_test_cases=hidden_test_cases,
		hints=hints,
		followup_questions=followup_questions,
	)


def validate_role_set_map(raw_role_sets: object) -> dict[str, tuple[str, ...]]:
	"""Validate reusable role-set definitions for a problem pack or draft."""

	return _validate_role_set_map(raw_role_sets)


def validate_problem_payload(
	raw_problem: object,
	*,
	role_sets: Mapping[str, tuple[str, ...]] | None = None,
) -> CertifiedProblem:
	"""Validate one problem payload using the same rules as the certified bank loader."""

	return _validate_problem(raw_problem, role_sets=role_sets or {})


def list_problem_bank_files(problem_bank_dir: Path | None = None) -> tuple[Path, ...]:
	bank_dir = Path(problem_bank_dir) if problem_bank_dir is not None else _PROBLEM_BANK_DIR
	bank_paths = tuple(sorted(bank_dir.glob(_PROBLEM_BANK_GLOB)))
	if not bank_paths:
		raise CertifiedProblemBankError(
			f"No DSA problem bank files matched {_PROBLEM_BANK_GLOB!r} in {bank_dir.as_posix()}."
		)
	return bank_paths


def _read_problem_bank_payload(problem_bank_path: Path) -> dict[str, Any]:
	if not problem_bank_path.exists():
		raise CertifiedProblemBankError(
			f"DSA problem bank is missing at {problem_bank_path.as_posix()}."
		)

	try:
		payload = json.loads(problem_bank_path.read_text(encoding="utf-8"))
	except json.JSONDecodeError as exc:
		raise CertifiedProblemBankError(
			f"DSA problem bank {problem_bank_path.name} contains invalid JSON."
		) from exc

	if not isinstance(payload, dict):
		raise CertifiedProblemBankError(
			f"DSA problem bank {problem_bank_path.name} root must be a JSON object."
		)
	return payload


def _extract_problem_pack_sections(
	payload: Mapping[str, Any],
	*,
	problem_bank_name: str,
) -> tuple[dict[str, tuple[str, ...]], tuple[object, ...]]:
	role_sets = _validate_role_set_map(payload.get("role_sets"))
	raw_problems = payload.get("problems")
	if not isinstance(raw_problems, list) or not raw_problems:
		raise CertifiedProblemBankError(
			f"DSA problem bank {problem_bank_name} must contain a non-empty problems array."
		)
	return role_sets, tuple(raw_problems)


def load_problem_banks_from_paths(problem_bank_paths: Sequence[Path]) -> tuple[CertifiedProblem, ...]:
	paths = tuple(Path(problem_bank_path) for problem_bank_path in problem_bank_paths)
	if not paths:
		raise CertifiedProblemBankError("At least one DSA problem bank path is required.")

	aggregated_role_sets: dict[str, tuple[str, ...]] = {}
	raw_problem_groups: list[tuple[str, tuple[object, ...]]] = []

	for problem_bank_path in paths:
		payload = _read_problem_bank_payload(problem_bank_path)
		role_sets, raw_problems = _extract_problem_pack_sections(
			payload,
			problem_bank_name=problem_bank_path.name,
		)
		for role_set_name, roles in role_sets.items():
			existing_roles = aggregated_role_sets.get(role_set_name)
			if existing_roles is not None and existing_roles != roles:
				raise CertifiedProblemBankError(
					f"Role set {role_set_name} is defined inconsistently across DSA problem banks."
				)
			aggregated_role_sets[role_set_name] = roles
		raw_problem_groups.append((problem_bank_path.name, raw_problems))

	problems: list[CertifiedProblem] = []
	seen_problem_ids: set[str] = set()
	for problem_bank_name, raw_problems in raw_problem_groups:
		for raw_problem in raw_problems:
			problem = _validate_problem(raw_problem, role_sets=aggregated_role_sets)
			if problem.problem_id in seen_problem_ids:
				raise CertifiedProblemBankError(
					f"Duplicate problem_id {problem.problem_id!r} detected while loading {problem_bank_name}."
				)
			seen_problem_ids.add(problem.problem_id)
			problems.append(problem)

	if not problems:
		raise CertifiedProblemBankError("No DSA problems were loaded from the configured bank files.")
	return tuple(problems)


@lru_cache(maxsize=1)
def load_certified_problem_bank() -> tuple[CertifiedProblem, ...]:
	return load_problem_banks_from_paths(list_problem_bank_files())


def clear_problem_bank_cache() -> None:
	load_certified_problem_bank.cache_clear()


def list_certified_problems() -> tuple[CertifiedProblem, ...]:
	return load_certified_problem_bank()


def list_supported_dsa_role_keys() -> tuple[str, ...]:
	return tuple(sorted(_DSA_REQUIRED_ROLE_KEYS))


def get_certified_problem(problem_id: str) -> CertifiedProblem:
	lookup_key = str(problem_id or "").strip()
	for problem in load_certified_problem_bank():
		if problem.problem_id == lookup_key:
			return problem
	raise ProblemSelectionError(f"Certified DSA problem {lookup_key!r} was not found in the local bank.")


def _sort_candidates(candidates: Sequence[CertifiedProblem], *, seed: str) -> list[CertifiedProblem]:
	def stable_rank(problem: CertifiedProblem) -> tuple[str, str]:
		digest = hashlib.sha256(f"{seed}:{problem.problem_id}".encode("utf-8")).hexdigest()
		return digest, problem.problem_id

	return sorted(candidates, key=stable_rank)


def _filter_candidates(
	candidates: Sequence[CertifiedProblem],
	*,
	allowed_difficulties: Sequence[str],
	disallowed_tags: Sequence[str] = (),
) -> list[CertifiedProblem]:
	filtered: list[CertifiedProblem] = []
	for difficulty in allowed_difficulties:
		matching_difficulty = [
			problem for problem in candidates if problem.difficulty == difficulty
		]
		if matching_difficulty:
			filtered = matching_difficulty
			break
	if not filtered:
		filtered = list(candidates)
	if not filtered:
		return []
	if not disallowed_tags:
		return filtered
	disallowed = set(disallowed_tags)
	without_tag_overlap = [problem for problem in filtered if not set(problem.tags) & disallowed]
	return without_tag_overlap or filtered


def _problems_for_role(role_key: str) -> list[CertifiedProblem]:
	normalized_role = _normalize_role_key(role_key)
	problems = [problem for problem in load_certified_problem_bank() if normalized_role in problem.roles]
	return problems or list(load_certified_problem_bank())


def select_problem(
	role_key: str,
	*,
	session_id: str,
	question_number: int,
	excluded_problem_ids: Sequence[str] | None = None,
	allowed_difficulties: Sequence[str],
	disallowed_tags: Sequence[str] = (),
) -> CertifiedProblem:
	excluded = {str(problem_id).strip() for problem_id in excluded_problem_ids or () if str(problem_id).strip()}
	candidates = [
		problem
		for problem in _problems_for_role(role_key)
		if problem.problem_id not in excluded
	]
	if not candidates:
		raise ProblemSelectionError(
			f"No certified DSA problems remain for role {role_key!r} after applying exclusions."
		)

	filtered_candidates = _filter_candidates(
		candidates,
		allowed_difficulties=allowed_difficulties,
		disallowed_tags=disallowed_tags,
	)
	if not filtered_candidates:
		raise ProblemSelectionError(
			f"No certified DSA problems match the requested difficulty for role {role_key!r}."
		)

	seed = f"{session_id or 'local'}:{_normalize_role_key(role_key)}:q{question_number}"
	return _sort_candidates(filtered_candidates, seed=seed)[0]


def select_problem_pair(
	role_key: str,
	*,
	session_id: str,
	excluded_problem_ids: Sequence[str] | None = None,
) -> tuple[CertifiedProblem, CertifiedProblem]:
	q1_problem = select_problem(
		role_key,
		session_id=session_id,
		question_number=1,
		excluded_problem_ids=excluded_problem_ids,
		allowed_difficulties=_Q1_DIFFICULTY_ORDER,
	)
	q2_problem = select_problem(
		role_key,
		session_id=session_id,
		question_number=2,
		excluded_problem_ids=(*(excluded_problem_ids or ()), q1_problem.problem_id),
		allowed_difficulties=_Q2_DIFFICULTY_ORDER,
		disallowed_tags=q1_problem.tags,
	)
	return q1_problem, q2_problem


def serialize_problem(problem: CertifiedProblem, *, include_private: bool = False) -> dict[str, Any]:
	payload: dict[str, Any] = {
		"problem_id": problem.problem_id,
		"title": problem.title,
		"statement": problem.statement,
		"constraints": list(problem.constraints),
		"source": problem.source,
		"source_problem_id": problem.source_problem_id,
		"source_url": problem.source_url,
		"rating": problem.rating,
		"tags": list(problem.tags),
		"roles": list(problem.roles),
		"difficulty": problem.difficulty,
		"starter_code": problem.starter_code,
		"examples": [
			{
				"input_text": example.input_text,
				"output_text": example.output_text,
				"explanation": example.explanation,
			}
			for example in problem.examples
		],
		"hints": list(problem.hints),
		"followup_questions": list(problem.followup_questions),
	}
	if include_private:
		payload["reference_solution"] = problem.reference_solution
		payload["brute_force_solution"] = problem.brute_force_solution
		payload["hidden_test_cases"] = [
			{"input_text": test_case.input_text, "output_text": test_case.output_text}
			for test_case in problem.hidden_test_cases
		]
	return payload


def select_problem_pair_payloads(
	role_key: str,
	*,
	session_id: str,
	excluded_problem_ids: Sequence[str] | None = None,
	include_private: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
	q1_problem, q2_problem = select_problem_pair(
		role_key,
		session_id=session_id,
		excluded_problem_ids=excluded_problem_ids,
	)
	return (
		serialize_problem(q1_problem, include_private=include_private),
		serialize_problem(q2_problem, include_private=include_private),
	)
