"""Deterministic DSA interview-brain helpers for state and coaching signals."""

from __future__ import annotations

import re
from typing import Any, Mapping

from backend.database.db_errors import (
	DSAStage,
	ExplanationQuality,
	OptimalityStatus,
	UnderstandingLevel,
	build_default_candidate_model,
)


def _problem_value(problem: Any, name: str, default: Any = None) -> Any:
	if isinstance(problem, Mapping):
		return problem.get(name, default)
	return getattr(problem, name, default)


def _coerce_mapping(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
	if value is None:
		return ()
	if isinstance(value, (list, tuple, set)):
		return tuple(str(item or "").strip() for item in value if str(item or "").strip())
	text = str(value or "").strip()
	return (text,) if text else ()


def _keyword_set(problem: Any) -> set[str]:
	fragments = [
		*(_coerce_string_tuple(_problem_value(problem, "tags"))),
		*(_coerce_string_tuple(_problem_value(problem, "hints"))),
		str(_problem_value(problem, "title", "") or ""),
	]
	keywords: set[str] = set()
	for fragment in fragments:
		keywords.update(re.findall(r"[a-zA-Z_]{3,}", fragment.casefold().replace("-", "_")))
	return keywords


def normalize_candidate_model(value: Any) -> dict[str, Any]:
	model = build_default_candidate_model()
	if not isinstance(value, Mapping):
		return model
	for key in model:
		if key in value:
			model[key] = value[key]
	return model


def _understanding_rank(value: str) -> int:
	return {
		UnderstandingLevel.LOW.value: 0,
		UnderstandingLevel.MEDIUM.value: 1,
		UnderstandingLevel.HIGH.value: 2,
	}.get(str(value or UnderstandingLevel.LOW.value), 0)


def _quality_rank(value: str) -> int:
	return {
		ExplanationQuality.VAGUE.value: 0,
		ExplanationQuality.PARTIAL.value: 1,
		ExplanationQuality.CLEAR.value: 2,
	}.get(str(value or ExplanationQuality.VAGUE.value), 0)


def _max_understanding(left: str, right: str) -> str:
	return left if _understanding_rank(left) >= _understanding_rank(right) else right


def _max_quality(left: str, right: str) -> str:
	return left if _quality_rank(left) >= _quality_rank(right) else right


def _derive_message_insights(message_text: str, problem: Any) -> dict[str, Any]:
	lowered = str(message_text or "").strip().casefold()
	word_count = len(re.findall(r"\b\w+\b", lowered))
	problem_keywords = _keyword_set(problem)
	algorithm_terms = {
		"binary search",
		"two pointers",
		"sliding window",
		"greedy",
		"dp",
		"dynamic programming",
		"hash",
		"set",
		"map",
		"queue",
		"stack",
		"heap",
		"sort",
		"prefix",
		"recursion",
	}
	mentions_complexity = bool(re.search(r"\bo\([^)]*\)", lowered))
	mentions_edge_cases = any(token in lowered for token in ("edge case", "empty", "single", "duplicate", "negative", "zero", "overflow"))
	mentions_data_structure = any(token in lowered for token in ("set", "map", "hash", "queue", "stack", "heap", "deque", "array"))
	identified_correct_approach = any(term in lowered for term in algorithm_terms) or any(keyword in lowered for keyword in problem_keywords if len(keyword) > 3)
	seems_stuck = any(token in lowered for token in ("stuck", "not sure", "don't know", "confused", "hint", "unsure", "can't"))
	time_pressure_visible = any(token in lowered for token in ("time", "quick", "hurry", "timeout", "running out"))
	understands_problem = UnderstandingLevel.LOW.value
	if any(token in lowered for token in ("input", "output", "constraint", "goal", "need to", "we have to")) or word_count >= 18:
		understands_problem = UnderstandingLevel.MEDIUM.value
	if mentions_edge_cases and identified_correct_approach and word_count >= 28:
		understands_problem = UnderstandingLevel.HIGH.value
	explanation_quality = ExplanationQuality.VAGUE.value
	if word_count >= 20:
		explanation_quality = ExplanationQuality.PARTIAL.value
	if word_count >= 45 and (mentions_complexity or mentions_edge_cases):
		explanation_quality = ExplanationQuality.CLEAR.value
	return {
		"word_count": word_count,
		"mentions_complexity": mentions_complexity,
		"mentions_edge_cases": mentions_edge_cases,
		"mentions_data_structure": mentions_data_structure,
		"identified_correct_approach": identified_correct_approach,
		"seems_stuck": seems_stuck,
		"time_pressure_visible": time_pressure_visible,
		"understands_problem": understands_problem,
		"explanation_quality": explanation_quality,
	}


def _problem_hint(problem: Any, hint_level: int) -> str | None:
	hints = _coerce_string_tuple(_problem_value(problem, "hints"))
	if not hints:
		return None
	index = max(0, min(len(hints) - 1, max(hint_level, 1) - 1))
	return hints[index]


def apply_message_intelligence(
	*,
	state: Mapping[str, Any] | None,
	current_stage: str,
	message_text: str,
	message_kind: str,
	problem: Any | None = None,
) -> dict[str, Any]:
	resolved_state = _coerce_mapping(state)
	candidate_model = normalize_candidate_model(resolved_state.get("candidate_model"))
	insights = _derive_message_insights(message_text, problem)

	candidate_model["understands_problem"] = _max_understanding(
		str(candidate_model.get("understands_problem") or UnderstandingLevel.LOW.value),
		insights["understands_problem"],
	)
	candidate_model["identified_correct_approach"] = bool(candidate_model.get("identified_correct_approach") or insights["identified_correct_approach"])
	candidate_model["mentioned_optimal_data_structure"] = bool(candidate_model.get("mentioned_optimal_data_structure") or insights["mentions_data_structure"])
	candidate_model["considered_edge_cases_in_approach"] = bool(candidate_model.get("considered_edge_cases_in_approach") or insights["mentions_edge_cases"])
	candidate_model["complexity_stated_correctly"] = bool(candidate_model.get("complexity_stated_correctly") or insights["mentions_complexity"])
	candidate_model["explanation_quality"] = _max_quality(
		str(candidate_model.get("explanation_quality") or ExplanationQuality.VAGUE.value),
		insights["explanation_quality"],
	)
	candidate_model["seems_stuck"] = bool(insights["seems_stuck"])
	candidate_model["time_pressure_visible"] = bool(candidate_model.get("time_pressure_visible") or insights["time_pressure_visible"])

	resolved_state["candidate_model"] = candidate_model
	resolved_state["last_message_insights"] = insights

	hint_level = int(resolved_state.get("hint_level") or 0)
	approach_exchange_count = int(resolved_state.get("approach_exchange_count") or 0)
	clarification_count = int(resolved_state.get("clarification_count") or 0)
	next_stage = str(current_stage or DSAStage.PROBLEM_SETUP.value)
	coaching_note = None

	if message_kind == "clarification" and clarification_count >= 3:
		next_stage = DSAStage.APPROACH_DISCUSSION.value
		coaching_note = "Clarification budget reached. Move into the approach discussion and outline your plan."

	if message_kind == "approach":
		if next_stage == DSAStage.PROBLEM_SETUP.value:
			next_stage = DSAStage.APPROACH_DISCUSSION.value
		if approach_exchange_count >= 2 and not candidate_model["identified_correct_approach"]:
			hint_level = max(hint_level, 1)
			coaching_note = _problem_hint(problem, hint_level) or "Start from the core constraint and describe the smallest workable data structure first."
		if (approach_exchange_count >= 3 or insights["seems_stuck"]) and not candidate_model["identified_correct_approach"]:
			hint_level = max(hint_level, 2)
			next_stage = DSAStage.CODING.value
			coaching_note = _problem_hint(problem, hint_level) or "Move into coding with the best current approach instead of looping in discussion."

	resolved_state["hint_level"] = hint_level
	if coaching_note:
		resolved_state["last_coaching_note"] = coaching_note

	return {
		"state": resolved_state,
		"candidate_model": candidate_model,
		"hint_level": hint_level,
		"next_stage": next_stage,
		"coaching_note": coaching_note,
		"message_insights": insights,
	}


def apply_submission_intelligence(
	*,
	state: Mapping[str, Any] | None,
	current_stage: str,
	analysis: Mapping[str, Any],
	execution_results: Mapping[str, Any],
	problem: Any | None = None,
) -> dict[str, Any]:
	resolved_state = _coerce_mapping(state)
	candidate_model = normalize_candidate_model(resolved_state.get("candidate_model"))
	passed_count = int(execution_results.get("passed_count") or 0)
	total_count = int(execution_results.get("total_count") or 0)
	pass_ratio = (passed_count / total_count) if total_count > 0 else 0.0
	analysis_mapping = _coerce_mapping(analysis)

	candidate_model["actual_approach_from_code"] = analysis_mapping.get("strategy_summary")
	candidate_model["mentioned_optimal_data_structure"] = bool(candidate_model.get("mentioned_optimal_data_structure") or analysis_mapping.get("efficient_data_structure_signal"))
	candidate_model["considered_edge_cases_in_approach"] = bool(candidate_model.get("considered_edge_cases_in_approach") or analysis_mapping.get("handles_empty_input"))
	candidate_model["code_handles_edge_cases"] = bool(analysis_mapping.get("handles_empty_input") or analysis_mapping.get("has_early_return"))
	candidate_model["identified_correct_approach"] = bool(candidate_model.get("identified_correct_approach") or pass_ratio >= 0.5 or analysis_mapping.get("optimality_guess") == OptimalityStatus.YES.value)
	candidate_model["is_solution_optimal"] = str(analysis_mapping.get("optimality_guess") or OptimalityStatus.UNKNOWN.value)
	candidate_model["seems_stuck"] = pass_ratio < 0.5
	candidate_model["understands_problem"] = _max_understanding(
		str(candidate_model.get("understands_problem") or UnderstandingLevel.LOW.value),
		UnderstandingLevel.HIGH.value if pass_ratio >= 1.0 else UnderstandingLevel.MEDIUM.value if pass_ratio > 0 else UnderstandingLevel.LOW.value,
	)

	resolved_state["candidate_model"] = candidate_model
	resolved_state["latest_analysis"] = analysis_mapping

	hint_level = int(resolved_state.get("hint_level") or 0)
	optimization_used = bool(resolved_state.get("optimization_used"))
	current_stage_value = str(current_stage or resolved_state.get("stage") or DSAStage.CODING.value)
	next_stage = DSAStage.CODING.value
	coaching_note = None

	if current_stage_value == DSAStage.OPTIMIZATION.value:
		resolved_state["optimization_used"] = True
		next_stage = DSAStage.DEBRIEF.value
		coaching_note = "Use the debrief to explain what changed in the optimization pass and which tradeoffs remain."
	elif pass_ratio >= 1.0:
		if not optimization_used and analysis_mapping.get("brute_force_likelihood") == "high":
			resolved_state["optimization_used"] = True
			next_stage = DSAStage.OPTIMIZATION.value
			hint_level = max(hint_level, 1)
			coaching_note = _problem_hint(problem, hint_level) or "The code passes, but it still looks brute-force. Use one optimization pass before the debrief."
		else:
			next_stage = DSAStage.DEBRIEF.value
			coaching_note = "Hidden tests passed. Move to the debrief and explain the final approach and complexity."
	else:
		failed_count = max(total_count - passed_count, 0)
		next_stage = DSAStage.CODING.value
		if total_count > 0:
			coaching_note = (
				f"{failed_count} hidden test{'s' if failed_count != 1 else ''} still failing. "
				"Re-check edge cases, constraints, and output format before resubmitting."
			)
		else:
			# total_count == 0 means Judge0 ran but markers were not found in stdout.
			# Derive a specific message from the execution status so the candidate
			# knows exactly what went wrong rather than seeing a generic message.
			exec_status = str(execution_results.get("status") or "").casefold()
			compile_output = str(execution_results.get("compile_output") or "").strip()
			stderr = str(execution_results.get("stderr") or "").strip()
			if compile_output:
				short_error = compile_output[:200].strip()
				coaching_note = f"Compilation failed. Fix the error and resubmit. Compiler output: {short_error}"
			elif "time limit" in exec_status:
				coaching_note = (
					"Time limit exceeded on the hidden test suite. "
					"The solution is too slow — look for a more efficient algorithm before resubmitting."
				)
			elif "memory" in exec_status:
				coaching_note = (
					"Memory limit exceeded on the hidden test suite. "
					"Reduce data structure size or avoid storing all inputs at once."
				)
			elif stderr or "runtime" in exec_status or "nzec" in exec_status or "signal" in exec_status:
				short_err = stderr[:200].strip() if stderr else exec_status
				coaching_note = f"Runtime error on the hidden tests. Fix the crash and resubmit. Details: {short_err}"
			elif "internal error" in exec_status:
				coaching_note = (
					"Judge0 reported an internal error. The harness could not be executed. "
					"Try resubmitting — if the error persists, contact support."
				)
			else:
				coaching_note = (
					"Judge0 ran the submission but returned no test results. "
					"Check compile and runtime output in the results panel, fix any errors, and resubmit."
				)

	resolved_state["hint_level"] = hint_level
	resolved_state["last_coaching_note"] = coaching_note
	return {
		"state": resolved_state,
		"candidate_model": candidate_model,
		"next_stage": next_stage,
		"coaching_note": coaching_note,
		"pass_ratio": round(pass_ratio, 4),
	}


__all__ = [
	"apply_message_intelligence",
	"apply_submission_intelligence",
	"normalize_candidate_model",
]
