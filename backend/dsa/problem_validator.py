"""Sandboxed validation helpers for generated DSA problems."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.dsa.code_executor import CodeExecutionError, run_submission
from backend.dsa.problem_selector import (
	CertifiedProblem,
	HiddenTestCase,
	serialize_problem,
	validate_problem_payload,
	validate_role_set_map,
)

_GENERATED_DRAFT_DIR = Path(__file__).resolve().parent.parent / "data" / "problems" / "drafts"
_GENERATOR_MODE = "local_numeric_mutation_v1"


class GeneratedProblemValidationError(RuntimeError):
	"""Raised when a generated DSA problem cannot be validated safely."""


@dataclass(frozen=True, slots=True)
class ValidationCase:
	label: str
	kind: str
	input_text: str
	expected_output: str | None = None


def _utcnow_iso() -> str:
	return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _coerce_mapping(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}


def _coerce_string(value: Any) -> str:
	return str(value or "")


def _normalize_problem(
	raw_problem: Mapping[str, Any] | CertifiedProblem,
	*,
	role_sets: Mapping[str, Sequence[str]] | None = None,
) -> CertifiedProblem:
	if isinstance(raw_problem, CertifiedProblem):
		return raw_problem
	validated_role_sets = validate_role_set_map(role_sets or {})
	return validate_problem_payload(raw_problem, role_sets=validated_role_sets)


def _build_explicit_validation_cases(problem: CertifiedProblem) -> list[ValidationCase]:
	validation_cases: list[ValidationCase] = []
	for index, example in enumerate(problem.examples, start=1):
		validation_cases.append(
			ValidationCase(
				label=f"example_{index}",
				kind="example",
				input_text=example.input_text,
				expected_output=example.output_text,
			)
		)
	for index, hidden_case in enumerate(problem.hidden_test_cases, start=1):
		validation_cases.append(
			ValidationCase(
				label=f"hidden_{index}",
				kind="hidden",
				input_text=hidden_case.input_text,
				expected_output=hidden_case.output_text,
			)
		)
	return validation_cases


def _parse_numeric_lines(input_text: str) -> list[list[int]] | None:
	lines = [line.strip() for line in str(input_text or "").strip().splitlines() if line.strip()]
	if not lines:
		return None
	parsed: list[list[int]] = []
	for line in lines:
		tokens = line.split()
		if not tokens or any(not re.fullmatch(r"-?\d+", token) for token in tokens):
			return None
		parsed.append([int(token) for token in tokens])
	return parsed


def _stringify_numeric_lines(lines: Sequence[Sequence[int]]) -> str:
	return "\n".join(" ".join(str(value) for value in line) for line in lines)


def _infer_preserved_positions(lines: Sequence[Sequence[int]]) -> dict[int, set[int]]:
	preserved_positions: dict[int, set[int]] = {}
	for index, line in enumerate(lines[:-1]):
		if not line:
			continue
		next_line = lines[index + 1]
		if next_line and line[0] == len(next_line):
			preserved_positions.setdefault(index, set()).add(0)
	return preserved_positions


def _mutate_numeric_lines(
	parsed_lines: Sequence[Sequence[int]],
	*,
	mode: str,
	rng: random.Random,
) -> str | None:
	mutated_lines = [list(line) for line in parsed_lines]
	preserved_positions = _infer_preserved_positions(parsed_lines)
	magnitude = max((abs(value) for line in parsed_lines for value in line), default=3)
	upper_bound = max(magnitude + 3, 5)
	mutated = False

	for line_index, line in enumerate(mutated_lines):
		preserved = preserved_positions.get(line_index, set())
		mutable_indexes = [index for index in range(len(line)) if index not in preserved]
		if not mutable_indexes:
			continue

		if mode == "zeros":
			for token_index in mutable_indexes:
				if line[token_index] != 0:
					line[token_index] = 0
					mutated = True
		elif mode == "ones":
			for token_index in mutable_indexes:
				next_value = 1 if (token_index + line_index) % 2 == 0 else -1
				if line[token_index] != next_value:
					line[token_index] = next_value
					mutated = True
		elif mode == "extremes":
			for token_index in mutable_indexes:
				next_value = upper_bound if (token_index + line_index) % 2 == 0 else -upper_bound
				if line[token_index] != next_value:
					line[token_index] = next_value
					mutated = True
		elif mode == "reversed" and len(mutable_indexes) > 1:
			reversed_values = list(reversed([line[token_index] for token_index in mutable_indexes]))
			for token_index, next_value in zip(mutable_indexes, reversed_values):
				if line[token_index] != next_value:
					line[token_index] = next_value
					mutated = True
		else:
			for token_index in mutable_indexes:
				next_value = rng.randint(-upper_bound, upper_bound)
				if line[token_index] != next_value:
					line[token_index] = next_value
					mutated = True

	if not mutated:
		return None
	return _stringify_numeric_lines(mutated_lines)


def build_local_validation_cases(
	problem: CertifiedProblem,
	*,
	random_seed: int = 11,
	target_case_count: int = 8,
) -> tuple[ValidationCase, ...]:
	"""Generate deterministic local probe inputs for draft-problem agreement checks."""

	seed_inputs = []
	seen_inputs: set[str] = set()
	for case in _build_explicit_validation_cases(problem):
		input_text = case.input_text.strip()
		if not input_text or input_text in seen_inputs:
			continue
		seen_inputs.add(input_text)
		seed_inputs.append(input_text)

	rng = random.Random(random_seed)
	generated_cases: list[ValidationCase] = []
	seen_generated: set[str] = set(seed_inputs)
	mode_cycle = ("zeros", "ones", "extremes", "reversed", "random", "random")

	for seed_index, seed_input in enumerate(seed_inputs, start=1):
		parsed_lines = _parse_numeric_lines(seed_input)
		if not parsed_lines:
			continue
		for mode_index, mode in enumerate(mode_cycle, start=1):
			candidate_input = _mutate_numeric_lines(parsed_lines, mode=mode, rng=rng)
			if not candidate_input or candidate_input in seen_generated:
				continue
			seen_generated.add(candidate_input)
			generated_cases.append(
				ValidationCase(
					label=f"generated_{seed_index}_{mode_index}",
					kind="generated",
					input_text=candidate_input,
					expected_output=None,
				)
			)
			if len(generated_cases) >= target_case_count:
				return tuple(generated_cases)
	return tuple(generated_cases)


def _to_hidden_test_cases(validation_cases: Sequence[ValidationCase]) -> tuple[HiddenTestCase, ...]:
	return tuple(
		HiddenTestCase(input_text=case.input_text, output_text=_coerce_string(case.expected_output))
		for case in validation_cases
	)


def _summarize_execution(result: Mapping[str, Any]) -> dict[str, Any]:
	return {
		"status": result.get("status"),
		"passed_count": result.get("passed_count"),
		"total_count": result.get("total_count"),
		"time_seconds": result.get("time_seconds"),
		"memory_kb": result.get("memory_kb"),
		"stderr": result.get("stderr"),
		"compile_output": result.get("compile_output"),
	}


def _build_case_reports(
	validation_cases: Sequence[ValidationCase],
	primary_result: Mapping[str, Any],
	alternate_result: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
	primary_case_results = list(primary_result.get("case_results") or [])
	alternate_case_results = list(alternate_result.get("case_results") or [])
	if len(primary_case_results) != len(validation_cases) or len(alternate_case_results) != len(validation_cases):
		raise GeneratedProblemValidationError("Judge0 returned an unexpected number of validation-case results.")

	case_reports: list[dict[str, Any]] = []
	mismatches: list[dict[str, Any]] = []
	for index, validation_case in enumerate(validation_cases):
		primary_case = _coerce_mapping(primary_case_results[index])
		alternate_case = _coerce_mapping(alternate_case_results[index])
		primary_output = _coerce_string(primary_case.get("actual_output"))
		alternate_output = _coerce_string(alternate_case.get("actual_output"))
		primary_error = _coerce_string(primary_case.get("error")) or None
		alternate_error = _coerce_string(alternate_case.get("error")) or None
		outputs_match = not primary_error and not alternate_error and primary_output == alternate_output
		expected_match = None
		validation_passed = outputs_match
		if validation_case.expected_output is not None:
			expected_output = _coerce_string(validation_case.expected_output)
			expected_match = bool(primary_case.get("passed")) and bool(alternate_case.get("passed"))
			validation_passed = validation_passed and expected_match and primary_output == expected_output

		case_report = {
			"label": validation_case.label,
			"kind": validation_case.kind,
			"input_text": validation_case.input_text,
			"expected_output": validation_case.expected_output,
			"primary_output": primary_output,
			"alternate_output": alternate_output,
			"primary_error": primary_error,
			"alternate_error": alternate_error,
			"outputs_match": outputs_match,
			"expected_match": expected_match,
			"validation_passed": validation_passed,
		}
		case_reports.append(case_report)
		if not validation_passed:
			reason = "solutions_disagreed"
			if primary_error or alternate_error:
				reason = "sandbox_error"
			elif validation_case.expected_output is not None and not expected_match:
				reason = "expected_output_mismatch"
			mismatches.append({**case_report, "reason": reason})
	return case_reports, mismatches


def build_generated_problem_draft_payload(
	problem: CertifiedProblem,
	*,
	alternate_solution: str,
	validation_result: Mapping[str, Any],
) -> dict[str, Any]:
	"""Serialize a validated generated problem into a draft-pool payload."""

	problem_payload = serialize_problem(problem, include_private=True)
	primary_solution = _coerce_string(problem_payload.pop("reference_solution", ""))
	return {
		"schema_version": 1,
		"draft_kind": "generated_problem",
		"validated_at": _utcnow_iso(),
		"status": validation_result.get("status"),
		"promotion_state": validation_result.get("promotion_state"),
		"problem": {
			**problem_payload,
			"certified": False,
		},
		"reference_solution_primary": primary_solution,
		"reference_solution_alternate": _coerce_string(alternate_solution),
		"validation_report": {
			"validation_passed": validation_result.get("validation_passed"),
			"generator_mode": validation_result.get("generator_mode"),
			"explicit_case_count": validation_result.get("explicit_case_count"),
			"generated_case_count": validation_result.get("generated_case_count"),
			"total_case_count": validation_result.get("total_case_count"),
			"mismatch_count": len(validation_result.get("mismatches") or []),
			"promotion_requirements": list(validation_result.get("promotion_requirements") or []),
			"primary_execution": validation_result.get("primary_execution"),
			"alternate_execution": validation_result.get("alternate_execution"),
			"case_reports": list(validation_result.get("case_reports") or []),
		},
	}


def write_generated_problem_draft(
	validation_result: Mapping[str, Any],
	*,
	output_path: str | Path | None = None,
) -> Path:
	"""Write a generated-problem draft payload into the local draft pool."""

	draft_payload = _coerce_mapping(validation_result.get("draft_payload"))
	problem_payload = _coerce_mapping(draft_payload.get("problem"))
	problem_id = _coerce_string(problem_payload.get("problem_id")).strip()
	if not draft_payload or not problem_id:
		raise GeneratedProblemValidationError("Validation result does not contain a writable draft payload.")
	resolved_output_path = Path(output_path).expanduser().resolve() if output_path else (_GENERATED_DRAFT_DIR / f"{problem_id}_draft.json")
	resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
	resolved_output_path.write_text(json.dumps(draft_payload, indent=2) + "\n", encoding="utf-8")
	return resolved_output_path


def validate_generated_problem_candidate(
	raw_problem: Mapping[str, Any] | CertifiedProblem,
	*,
	alternate_solution: str,
	role_sets: Mapping[str, Sequence[str]] | None = None,
	language: str = "python",
	random_seed: int = 11,
	target_generated_case_count: int = 8,
) -> dict[str, Any]:
	"""Validate a generated problem by comparing two sandboxed reference solutions."""

	problem = _normalize_problem(raw_problem, role_sets=role_sets)
	resolved_alternate_solution = _coerce_string(alternate_solution).strip()
	if not resolved_alternate_solution:
		raise GeneratedProblemValidationError("Generated-problem validation requires a non-empty alternate solution.")

	explicit_validation_cases = _build_explicit_validation_cases(problem)
	generated_validation_cases = list(
		build_local_validation_cases(
			problem,
			random_seed=random_seed,
			target_case_count=target_generated_case_count,
		)
	)
	if not generated_validation_cases:
		raise GeneratedProblemValidationError(
			"Could not derive any local random or adversarial validation cases from the provided examples and hidden tests."
		)

	validation_cases = [*explicit_validation_cases, *generated_validation_cases]
	harness_cases = _to_hidden_test_cases(validation_cases)
	try:
		primary_result = run_submission(problem.reference_solution, harness_cases, language=language)
		alternate_result = run_submission(resolved_alternate_solution, harness_cases, language=language)
	except CodeExecutionError as exc:
		raise GeneratedProblemValidationError(str(exc)) from exc

	case_reports, mismatches = _build_case_reports(validation_cases, primary_result, alternate_result)
	primary_execution = _summarize_execution(primary_result)
	alternate_execution = _summarize_execution(alternate_result)
	validation_passed = not primary_execution.get("stderr") and not primary_execution.get("compile_output")
	validation_passed = validation_passed and not alternate_execution.get("stderr") and not alternate_execution.get("compile_output")
	validation_passed = validation_passed and not mismatches
	status = "draft" if validation_passed else "rejected"
	promotion_state = "human_review_required" if validation_passed else "rejected"
	promotion_requirements = [
		"Human review is still required before promotion to the certified pool.",
	]
	result = {
		"problem_id": problem.problem_id,
		"language": language,
		"validation_passed": validation_passed,
		"status": status,
		"promotion_state": promotion_state,
		"generator_mode": _GENERATOR_MODE,
		"explicit_case_count": len(explicit_validation_cases),
		"generated_case_count": len(generated_validation_cases),
		"total_case_count": len(validation_cases),
		"primary_execution": primary_execution,
		"alternate_execution": alternate_execution,
		"case_reports": case_reports,
		"mismatches": mismatches,
		"promotion_requirements": promotion_requirements,
	}
	result["draft_payload"] = build_generated_problem_draft_payload(
		problem,
		alternate_solution=resolved_alternate_solution,
		validation_result=result,
	)
	return result


__all__ = [
	"GeneratedProblemValidationError",
	"ValidationCase",
	"build_generated_problem_draft_payload",
	"build_local_validation_cases",
	"validate_generated_problem_candidate",
	"write_generated_problem_draft",
]
