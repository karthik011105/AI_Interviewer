"""DSA-specific report helpers built from persisted scoring signals."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def _coerce_mapping(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}


def _coerce_numeric(value: Any) -> float | None:
	if value in {None, ""}:
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _problem_value(problem: Any, name: str, default: Any = None) -> Any:
	if isinstance(problem, Mapping):
		return problem.get(name, default)
	return getattr(problem, name, default)


_QUESTION_DIMENSION_KEYS = (
	"approach_quality",
	"code_correctness",
	"complexity_awareness",
	"followup_depth",
	"communication",
)


def _question_report_has_dimension_values(report: Mapping[str, Any]) -> bool:
	dimension_scores = _coerce_mapping(report.get("dimension_scores"))
	return any(_coerce_numeric(dimension_scores.get(key)) is not None for key in _QUESTION_DIMENSION_KEYS)


def _numeric_matches(left: Any, right: Any, *, tolerance: float = 1e-4) -> bool:
	left_value = _coerce_numeric(left)
	right_value = _coerce_numeric(right)
	if left_value is None and right_value is None:
		return True
	if left_value is None or right_value is None:
		return False
	return abs(left_value - right_value) <= tolerance


def _question_report_needs_rebuild(existing_report: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
	record_question_number = int(record.get("question_number") or 0)
	report_question_number = int(existing_report.get("question_number") or 0)
	if report_question_number not in {0, record_question_number}:
		return True

	persisted_score = _coerce_numeric(record.get("total_score"))
	record_dimension_scores = _coerce_mapping(record.get("dimension_scores"))
	report_dimension_scores = _coerce_mapping(existing_report.get("dimension_scores"))
	expected_score = persisted_score
	if expected_score is None:
		expected_score = _coerce_numeric(record_dimension_scores.get("total_score"))
	if expected_score is None:
		expected_score = _coerce_numeric(record_dimension_scores.get("pass_ratio"))
	if expected_score is not None and not _numeric_matches(expected_score, existing_report.get("score")):
		return True

	if any(_coerce_numeric(record_dimension_scores.get(key)) is not None for key in _QUESTION_DIMENSION_KEYS):
		if not _question_report_has_dimension_values(existing_report):
			return True
		for key in _QUESTION_DIMENSION_KEYS:
			if not _numeric_matches(record_dimension_scores.get(key), report_dimension_scores.get(key)):
				return True

	return False


def build_dsa_question_report(
	*,
	record: Mapping[str, Any],
	problem: Any | None = None,
	analysis: Mapping[str, Any] | None = None,
	evaluation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	resolved_analysis = _coerce_mapping(analysis) or _coerce_mapping(_coerce_mapping(record.get("state_json")).get("latest_analysis"))
	resolved_evaluation = _coerce_mapping(evaluation) or _coerce_mapping(record.get("dimension_scores"))
	candidate_model = _coerce_mapping(resolved_evaluation.get("candidate_model") or _coerce_mapping(record.get("state_json")).get("candidate_model"))
	question_number = int(record.get("question_number") or 0)
	question_score = _coerce_numeric(record.get("total_score"))
	if question_score is None:
		question_score = _coerce_numeric(resolved_evaluation.get("total_score"))
	if question_score is None:
		question_score = _coerce_numeric(resolved_evaluation.get("pass_ratio"))
	if question_score is not None:
		question_score = round(question_score, 4)
	submission_count = len(record.get("all_code_submissions") or [])
	strengths: list[str] = []
	risks: list[str] = []
	recommendations: list[str] = []

	if _coerce_numeric(resolved_evaluation.get("code_correctness")) is not None and float(resolved_evaluation.get("code_correctness") or 0.0) >= 0.85:
		strengths.append("Hidden-test correctness remained strong.")
	else:
		risks.append("Correctness still needs work on hidden cases.")

	if resolved_analysis.get("brute_force_likelihood") == "high":
		risks.append("The code still looks brute-force and may not scale to stronger constraints.")
		recommendations.append("Reduce nested work or move to a stronger data structure before the next attempt.")
	elif resolved_analysis.get("optimality_guess") == "yes":
		strengths.append("The detected strategy looks close to the intended optimal shape.")

	if candidate_model.get("code_handles_edge_cases"):
		strengths.append("Edge-case handling is visible in the submitted code.")
	else:
		risks.append("Edge-case handling is not strongly signaled in the final code.")

	if _coerce_numeric(resolved_evaluation.get("followup_depth")) is not None and float(resolved_evaluation.get("followup_depth") or 0.0) >= 0.7:
		strengths.append("The debrief showed solid reflection and follow-up depth.")
	else:
		recommendations.append("Use the debrief to explain tradeoffs, failed ideas, and what would change next time.")

	if not recommendations:
		recommendations.append("Preserve this reasoning style and keep explaining both complexity and edge cases explicitly.")

	return {
		"question_number": question_number,
		"problem_id": str(record.get("problem_id") or "") or None,
		"problem_title": str(_problem_value(problem, "title", "") or "") or None,
		"score": question_score,
		"language": resolved_analysis.get("language") or _coerce_mapping(record.get("state_json")).get("current_language"),
		"analysis_summary": resolved_analysis.get("analysis_summary"),
		"strategy_summary": resolved_analysis.get("strategy_summary"),
		"dimension_scores": {
			"approach_quality": resolved_evaluation.get("approach_quality"),
			"code_correctness": resolved_evaluation.get("code_correctness"),
			"complexity_awareness": resolved_evaluation.get("complexity_awareness"),
			"followup_depth": resolved_evaluation.get("followup_depth"),
			"communication": resolved_evaluation.get("communication"),
		},
		"strengths": strengths[:3],
		"risks": risks[:3],
		"recommendations": recommendations[:3],
		"coding_journey": {
			"submission_count": submission_count,
			"last_judge_status": record.get("last_judge_status"),
			"completed_at": record.get("completed_at"),
		},
	}


def build_dsa_round_report(
	*,
	records: Sequence[Mapping[str, Any]],
	problem_lookup: Mapping[int, Any] | None = None,
) -> dict[str, Any]:
	problem_lookup = problem_lookup or {}
	question_reports: list[dict[str, Any]] = []
	for record in sorted(records, key=lambda item: int(item.get("question_number") or 0)):
		existing_dimension_scores = _coerce_mapping(record.get("dimension_scores"))
		existing_report = _coerce_mapping(existing_dimension_scores.get("question_report"))
		if existing_report and not _question_report_needs_rebuild(existing_report, record):
			question_reports.append(existing_report)
			continue
		rebuilt_report = build_dsa_question_report(
			record=record,
			problem=problem_lookup.get(int(record.get("question_number") or 0)),
			analysis=_coerce_mapping(existing_dimension_scores.get("analysis")),
			evaluation=existing_dimension_scores,
		)
		if existing_report:
			for field_name in ("problem_title", "analysis_summary", "strategy_summary"):
				if rebuilt_report.get(field_name) in {None, ""} and existing_report.get(field_name) not in {None, ""}:
					rebuilt_report[field_name] = existing_report.get(field_name)
		question_reports.append(rebuilt_report)

	question_scores = {
		report["question_number"]: report.get("score")
		for report in question_reports
		if report.get("question_number")
	}
	question_one_score = _coerce_numeric(question_scores.get(1))
	question_two_score = _coerce_numeric(question_scores.get(2))
	aggregate_score = None
	if question_one_score is not None and question_two_score is not None:
		aggregate_score = round((question_one_score * 0.45) + (question_two_score * 0.55), 4)

	strengths: list[str] = []
	recommendations: list[str] = []
	for report in question_reports:
		for item in report.get("strengths") or []:
			if item not in strengths:
				strengths.append(item)
		for item in report.get("recommendations") or []:
			if item not in recommendations:
				recommendations.append(item)

	return {
		"status": "complete" if all(_coerce_numeric(report.get("score")) is not None for report in question_reports[:2]) else "in_progress",
		"aggregate_score": aggregate_score,
		"question_reports": question_reports,
		"strengths": strengths[:4],
		"recommendations": recommendations[:4],
	}


__all__ = ["build_dsa_question_report", "build_dsa_round_report"]
