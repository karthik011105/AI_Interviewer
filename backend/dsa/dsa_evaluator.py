"""Heuristic multi-dimension DSA scoring helpers."""

from __future__ import annotations

import re
from typing import Any, Mapping

from backend.database.db_errors import ExplanationQuality, OptimalityStatus, UnderstandingLevel


def _coerce_mapping(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}


def _coerce_numeric(value: Any) -> float | None:
	if value in {None, ""}:
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
	return max(lower, min(upper, value))


def _message_texts(state: Mapping[str, Any], key: str) -> list[str]:
	messages = state.get(key) or []
	if not isinstance(messages, list):
		return []
	return [str(entry.get("text") or "").strip() for entry in messages if isinstance(entry, Mapping) and str(entry.get("text") or "").strip()]


def _quality_score(value: str) -> float:
	return {
		ExplanationQuality.VAGUE.value: 0.35,
		ExplanationQuality.PARTIAL.value: 0.65,
		ExplanationQuality.CLEAR.value: 0.9,
	}.get(str(value or ExplanationQuality.VAGUE.value), 0.35)


def _understanding_score(value: str) -> float:
	return {
		UnderstandingLevel.LOW.value: 0.4,
		UnderstandingLevel.MEDIUM.value: 0.68,
		UnderstandingLevel.HIGH.value: 0.9,
	}.get(str(value or UnderstandingLevel.LOW.value), 0.4)


def _optimality_score(value: str) -> float:
	return {
		OptimalityStatus.YES.value: 0.9,
		OptimalityStatus.NO.value: 0.35,
		OptimalityStatus.UNKNOWN.value: 0.6,
	}.get(str(value or OptimalityStatus.UNKNOWN.value), 0.6)


def _build_feedback_summary(scores: Mapping[str, float]) -> str:
	strengths: list[str] = []
	if scores.get("code_correctness", 0.0) >= 0.85:
		strengths.append("correctness held up on hidden tests")
	if scores.get("approach_quality", 0.0) >= 0.75:
		strengths.append("the approach was mostly sound")
	if scores.get("complexity_awareness", 0.0) >= 0.75:
		strengths.append("complexity awareness was strong")

	gaps: list[str] = []
	if scores.get("code_correctness", 0.0) < 0.6:
		gaps.append("correctness needs more work")
	if scores.get("followup_depth", 0.0) < 0.6:
		gaps.append("the debrief should go deeper")
	if scores.get("communication", 0.0) < 0.6:
		gaps.append("communication should be clearer")

	if strengths and gaps:
		return f"Strengths: {', '.join(strengths[:2])}. Gaps: {', '.join(gaps[:2])}."
	if strengths:
		return f"Strengths: {', '.join(strengths[:3])}."
	if gaps:
		return f"Main gaps: {', '.join(gaps[:3])}."
	return "The submission needs more signal before a strong DSA evaluation summary can be generated."


def evaluate_dsa_question(
	*,
	record: Mapping[str, Any],
	analysis: Mapping[str, Any],
	candidate_model: Mapping[str, Any],
	problem: Any | None = None,
) -> dict[str, Any]:
	"""Score one DSA question using deterministic multi-factor heuristics."""

	del problem  # kept for signature stability and future use.
	state = _coerce_mapping(record.get("state_json"))
	execution_results = _coerce_mapping(record.get("execution_results"))
	passed_count = int(execution_results.get("passed_count") or 0)
	total_count = int(execution_results.get("total_count") or 0)
	pass_ratio = round((passed_count / total_count), 4) if total_count > 0 else 0.0
	hint_level = int(state.get("hint_level") or 0)
	judge_status = str(record.get("last_judge_status") or state.get("last_judge_status") or "").casefold()
	timed_out = judge_status == "tle" or "time limit" in str(execution_results.get("status") or "").casefold()
	compile_or_runtime_error = bool(str(execution_results.get("compile_output") or "").strip() or str(execution_results.get("stderr") or "").strip())
	approach_messages = _message_texts(state, "approach_messages")
	debrief_messages = _message_texts(state, "debrief_messages")
	all_message_text = " ".join([*approach_messages, *debrief_messages]).casefold()
	reflection_signals = sum(
		1
		for token in ("improve", "tradeoff", "complexity", "optimiz", "edge case", "next time", "because")
		if token in all_message_text
	)

	approach_score = 0.2
	approach_score += _understanding_score(str(candidate_model.get("understands_problem"))) * 0.35
	approach_score += 0.2 if candidate_model.get("identified_correct_approach") else 0.0
	approach_score += 0.1 if candidate_model.get("mentioned_optimal_data_structure") else 0.0
	approach_score += 0.1 if candidate_model.get("considered_edge_cases_in_approach") else 0.0
	approach_score += 0.05 if candidate_model.get("actual_approach_from_code") else 0.0
	approach_score = _clamp(approach_score)

	code_correctness = pass_ratio
	if compile_or_runtime_error and pass_ratio <= 0.0:
		code_correctness = 0.0

	complexity_awareness = _optimality_score(str(candidate_model.get("is_solution_optimal")))
	if candidate_model.get("complexity_stated_correctly"):
		complexity_awareness += 0.1
	if analysis.get("brute_force_likelihood") == "high":
		complexity_awareness -= 0.15
	elif analysis.get("brute_force_likelihood") == "low":
		complexity_awareness += 0.05
	complexity_awareness = _clamp(complexity_awareness)

	followup_depth = 0.25
	followup_depth += min(len(debrief_messages), 2) * 0.15
	followup_depth += min(reflection_signals, 3) * 0.1
	if len(" ".join(debrief_messages).split()) >= 40:
		followup_depth += 0.15
	followup_depth = _clamp(followup_depth)

	communication = _quality_score(str(candidate_model.get("explanation_quality")))
	if re.search(r"\bo\([^)]*\)", all_message_text):
		communication += 0.05
	communication = _clamp(communication)

	scores = {
		"approach_quality": round(approach_score, 4),
		"code_correctness": round(code_correctness, 4),
		"complexity_awareness": round(complexity_awareness, 4),
		"followup_depth": round(followup_depth, 4),
		"communication": round(communication, 4),
	}
	total_score = (
		scores["approach_quality"] * 0.25
		+ scores["code_correctness"] * 0.35
		+ scores["complexity_awareness"] * 0.20
		+ scores["followup_depth"] * 0.15
		+ scores["communication"] * 0.05
	)
	penalties = {
		"correctness_cap_applied": False,
		"hint_penalty_applied": False,
		"timeout_penalty_applied": False,
	}
	if scores["code_correctness"] == 0:
		total_score = min(total_score, 0.60)
		penalties["correctness_cap_applied"] = True
	if hint_level >= 2:
		total_score *= 0.90
		penalties["hint_penalty_applied"] = True
	if timed_out:
		total_score *= 0.95
		penalties["timeout_penalty_applied"] = True
	total_score = round(_clamp(total_score), 4)

	return {
		**scores,
		"total_score": total_score,
		"pass_ratio": pass_ratio,
		"penalties": penalties,
		"scoring_mode": "heuristic_five_dimension_v1",
		"feedback_summary": _build_feedback_summary(scores),
	}


__all__ = ["evaluate_dsa_question"]
