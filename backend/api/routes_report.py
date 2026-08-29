"""Final report REST routes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.auth import AuthenticatedUser, ensure_session_access, require_current_user
from backend.database.queries import (
	get_assessment_session,
	get_final_report,
	get_interview_round_session,
	get_session,
	list_dsa_sessions,
	list_interview_responses,
	upsert_final_report,
)
from backend.database.db_errors import DatabaseClientError
from backend.dsa.report_engine import build_dsa_round_report

router = APIRouter(prefix="/report", tags=["report"])
_LOGGER = logging.getLogger(__name__)

INTERVIEW_ROUNDS = ("technical", "project_discussion", "hr")
ROUND_ORDER = ("assessment", "technical", "dsa", "project_discussion", "hr")
ROUND_LABELS = {
	"assessment": "Assessment",
	"technical": "Technical Interview",
	"dsa": "DSA Round",
	"project_discussion": "Project Discussion",
	"hr": "HR Interview",
}
LOW_SCORE_GUIDANCE = {
	"assessment": "Revisit core fundamentals and timed MCQ practice so the screening round becomes more consistent.",
	"technical": "Focus on clearer technical explanations with direct tradeoffs, examples, and terminology.",
	"dsa": "Practice problem setup, edge cases, and complexity explanations before the next coding round.",
	"project_discussion": "Strengthen project storytelling around architecture choices, metrics, and tradeoffs.",
	"hr": "Tighten HR answers around ownership, motivation, and concise communication.",
}


def _utcnow_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _coerce_mapping(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}


def _coerce_numeric_score(value: Any) -> float | None:
	try:
		if value is None:
			return None
		return float(value)
	except (TypeError, ValueError):
		return None


def _average(values: list[float]) -> float | None:
	if not values:
		return None
	return round(sum(values) / len(values), 4)


def _unique_text(items: list[str], *, limit: int = 3) -> list[str]:
	unique_items: list[str] = []
	for item in items:
		text = str(item or "").strip()
		if not text or text in unique_items:
			continue
		unique_items.append(text)
		if len(unique_items) >= limit:
			break
	return unique_items


def _build_report_section(
	key: str,
	*,
	status: str,
	score: float | None,
	summary: str,
	detail: str,
	metrics: Mapping[str, Any] | None = None,
	strengths: list[str] | None = None,
	risks: list[str] | None = None,
	recommendations: list[str] | None = None,
	evidence: list[str] | None = None,
	extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	report_section = {
		"key": key,
		"label": ROUND_LABELS[key],
		"status": status,
		"score": score,
		"summary": summary,
		"detail": detail,
		"metrics": _coerce_mapping(metrics),
		"strengths": _unique_text(strengths or [], limit=4),
		"risks": _unique_text(risks or [], limit=4),
		"recommendations": _unique_text(recommendations or [], limit=4),
		"evidence": _unique_text(evidence or [], limit=5),
	}
	for extra_key, extra_value in _coerce_mapping(extras).items():
		if extra_key not in report_section:
			report_section[extra_key] = extra_value
	return report_section


def _extract_concept_coverage(response: Mapping[str, Any]) -> dict[str, Any]:
	concept_coverage = _coerce_mapping(response.get("concept_coverage"))
	if concept_coverage:
		return concept_coverage
	dimension_scores = _coerce_mapping(response.get("dimension_scores"))
	return _coerce_mapping(dimension_scores.get("concept_coverage"))


def _extract_scoring_profile(response: Mapping[str, Any]) -> dict[str, Any]:
	scoring_profile = _coerce_mapping(response.get("scoring_profile"))
	if scoring_profile:
		return scoring_profile
	dimension_scores = _coerce_mapping(response.get("dimension_scores"))
	return _coerce_mapping(dimension_scores.get("scoring_profile"))


def _extract_communication_detail(response: Mapping[str, Any]) -> dict[str, Any]:
	communication = _coerce_mapping(response.get("communication"))
	if communication:
		return communication
	dimension_scores = _coerce_mapping(response.get("dimension_scores"))
	return _coerce_mapping(dimension_scores.get("communication_detail") or dimension_scores.get("communication"))


def _dominant_label(labels: list[str]) -> str | None:
	counts: dict[str, int] = {}
	for raw_label in labels:
		label = str(raw_label or "").strip()
		if not label:
			continue
		counts[label] = counts.get(label, 0) + 1
	if not counts:
		return None
	return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def _build_concept_coverage_summary(responses: list[Mapping[str, Any]]) -> dict[str, Any]:
	coverage_ratios: list[float] = []
	tfidf_scores: list[float] = []
	thresholds: list[float] = []
	covered_points: list[str] = []
	missing_points: list[str] = []
	covered_count = 0
	total_concepts = 0
	available_responses = 0
	scoring_profile: dict[str, Any] = {}

	for response in responses:
		concept_coverage = _extract_concept_coverage(response)
		if not concept_coverage:
			continue
		if concept_coverage.get("available") is False:
			continue
		available_responses += 1
		ratio = _coerce_numeric_score(concept_coverage.get("coverage_ratio"))
		if ratio is not None:
			coverage_ratios.append(ratio)
		tfidf_score = _coerce_numeric_score(concept_coverage.get("tfidf_score"))
		if tfidf_score is not None:
			tfidf_scores.append(tfidf_score)
		threshold = _coerce_numeric_score(concept_coverage.get("threshold"))
		if threshold is not None:
			thresholds.append(threshold)
		covered_count += int(concept_coverage.get("covered_count") or 0)
		total_concepts += int(concept_coverage.get("total_concepts") or 0)
		for detail in concept_coverage.get("concept_details") or []:
			if not isinstance(detail, Mapping):
				continue
			point = str(detail.get("point") or "").strip()
			if not point:
				continue
			if bool(detail.get("covered")):
				covered_points.append(point)
			else:
				missing_points.append(point)
		profile = _extract_scoring_profile(response)
		if profile:
			scoring_profile = profile

	if available_responses == 0 and not scoring_profile:
		return {}

	return {
		"available_responses": available_responses,
		"average_ratio": _average(coverage_ratios),
		"average_tfidf": _average(tfidf_scores),
		"covered_count": covered_count,
		"total_concepts": total_concepts,
		"threshold": _average(thresholds),
		"sample_covered_points": _unique_text(covered_points),
		"sample_missing_points": _unique_text(missing_points),
		"scoring_profile": scoring_profile,
	}


def _build_confidence_signal_summary(responses: list[Mapping[str, Any]]) -> dict[str, Any]:
	confidence_scores: list[float] = []
	sentiment_compounds: list[float] = []
	hedging_ratios: list[float] = []
	filler_ratios: list[float] = []
	confidence_labels: list[str] = []
	sentiment_labels: list[str] = []
	analysis_modes: list[str] = []
	available_responses = 0
	sentiment_backed_responses = 0

	for response in responses:
		communication = _extract_communication_detail(response)
		if not communication:
			continue
		available_responses += 1
		confidence_score = _coerce_numeric_score(communication.get("confidence_score"))
		if confidence_score is not None:
			confidence_scores.append(confidence_score)
		sentiment_compound = _coerce_numeric_score(communication.get("sentiment_compound"))
		if sentiment_compound is not None:
			sentiment_compounds.append(sentiment_compound)
		hedging_ratio = _coerce_numeric_score(communication.get("hedging_ratio"))
		if hedging_ratio is not None:
			hedging_ratios.append(hedging_ratio)
		filler_ratio = _coerce_numeric_score(communication.get("filler_ratio"))
		if filler_ratio is not None:
			filler_ratios.append(filler_ratio)
		confidence_label = str(communication.get("confidence_label") or "").strip()
		if confidence_label:
			confidence_labels.append(confidence_label)
		sentiment_label = str(communication.get("sentiment_label") or "").strip()
		if sentiment_label:
			sentiment_labels.append(sentiment_label)
		analysis_mode = str(communication.get("sentiment_analysis_mode") or "").strip()
		if analysis_mode:
			analysis_modes.append(analysis_mode)
		if communication.get("sentiment_available") is True:
			sentiment_backed_responses += 1

	if available_responses == 0:
		return {}

	return {
		"available_responses": available_responses,
		"sentiment_backed_responses": sentiment_backed_responses,
		"average_confidence": _average(confidence_scores),
		"average_sentiment_compound": _average(sentiment_compounds),
		"average_hedging_ratio": _average(hedging_ratios),
		"average_filler_ratio": _average(filler_ratios),
		"dominant_confidence_label": _dominant_label(confidence_labels),
		"dominant_sentiment_label": _dominant_label(sentiment_labels),
		"analysis_modes": sorted({mode for mode in analysis_modes if mode}),
	}


def _normalize_covered_topics(entries: Any) -> list[dict[str, Any]]:
	if not isinstance(entries, list):
		return []

	normalized: list[dict[str, Any]] = []
	for entry in entries:
		if not isinstance(entry, Mapping):
			continue
		normalized.append({
			"question_index": int(entry.get("question_index") or 0),
			"question_tier": str(entry.get("question_tier") or "general").strip().lower() or "general",
			"focus_skill": str(entry.get("focus_skill") or "").strip() or None,
			"question_text": str(entry.get("question_text") or "").strip(),
			"answered_at": entry.get("answered_at"),
		})
	return normalized


def _summarize_covered_topics(entries: list[Mapping[str, Any]]) -> dict[str, Any]:
	tier_counts = {
		"strong": 0,
		"familiar": 0,
		"mentioned": 0,
		"absent": 0,
		"general": 0,
	}
	skills_by_tier: dict[str, list[str]] = {
		"strong": [],
		"familiar": [],
		"mentioned": [],
		"absent": [],
		"general": [],
	}

	for entry in entries:
		tier = str(entry.get("question_tier") or "general").strip().lower()
		if tier not in tier_counts:
			tier = "general"
		tier_counts[tier] += 1
		focus_skill = str(entry.get("focus_skill") or "").strip()
		if focus_skill and focus_skill not in skills_by_tier[tier]:
			skills_by_tier[tier].append(focus_skill)

	covered_focus_skills: list[str] = []
	for tier in ("absent", "familiar", "mentioned", "strong", "general"):
		for focus_skill in skills_by_tier[tier]:
			if focus_skill not in covered_focus_skills:
				covered_focus_skills.append(focus_skill)

	return {
		"total_questions_covered": len(entries),
		"tier_counts": tier_counts,
		"skills_by_tier": skills_by_tier,
		"covered_focus_skills": covered_focus_skills,
	}


def _derive_covered_topics_from_questions_json(
	questions_json: Mapping[str, Any],
	*,
	response_count: int,
	current_question_index: int,
) -> list[dict[str, Any]]:
	questions = questions_json.get("questions") or []
	if not isinstance(questions, list):
		return []

	covered_count = min(max(response_count, current_question_index), len(questions))
	derived: list[dict[str, Any]] = []
	for index, question in enumerate(questions[:covered_count]):
		if not isinstance(question, Mapping):
			continue
		derived.append({
			"question_index": index,
			"question_tier": str(question.get("question_tier") or "general").strip().lower() or "general",
			"focus_skill": str(question.get("focus_skill") or "").strip() or None,
			"question_text": str(question.get("question") or "").strip(),
			"answered_at": None,
		})
	return derived


def _extract_skill_targeting_state(
	record: Mapping[str, Any],
	round_name: str,
	*,
	response_count: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
	if round_name != "technical":
		return {}, [], {}

	questions_json = _coerce_mapping(record.get("questions_json"))
	skill_profile_summary = _coerce_mapping(questions_json.get("skill_profile_summary"))
	covered_topics = _normalize_covered_topics(questions_json.get("covered_topics"))
	if not covered_topics:
		covered_topics = _derive_covered_topics_from_questions_json(
			questions_json,
			response_count=response_count,
			current_question_index=int(record.get("current_question_index") or 0),
		)
	covered_topic_summary = _coerce_mapping(questions_json.get("covered_topic_summary"))
	if not covered_topic_summary and covered_topics:
		covered_topic_summary = _summarize_covered_topics(covered_topics)
	return skill_profile_summary, covered_topics, covered_topic_summary


def _summarize_feedback(feedback: Any) -> str | None:
	text = str(feedback or "").strip()
	if not text:
		return None
	first_sentence = text.split(".", 1)[0].strip()
	if not first_sentence:
		return None
	if len(first_sentence) <= 140:
		return first_sentence
	return f"{first_sentence[:137].rstrip()}..."


def _count_round_questions(questions_json: Mapping[str, Any], round_name: str) -> int:
	if round_name == "project_discussion":
		projects = questions_json.get("projects") or []
		if not isinstance(projects, list):
			return 0
		return sum(
			len(project.get("questions") or [])
			for project in projects
			if isinstance(project, Mapping)
		)
	questions = questions_json.get("questions") or []
	return len(questions) if isinstance(questions, list) else 0


def _build_empty_summary(key: str, detail: str) -> dict[str, Any]:
	return {
		"key": key,
		"label": ROUND_LABELS[key],
		"status": "not_started",
		"score": None,
		"summary": detail,
		"detail": detail,
	}


def _build_assessment_snapshot(session_id: str) -> dict[str, Any]:
	record = get_assessment_session(session_id)
	if record is None:
		summary_text = "Assessment has not started yet."
		detail_text = "Complete the screening round to unlock assessment accuracy trends."
		metrics = {
			"answered_count": 0,
			"correct_count": 0,
			"total_questions": 0,
			"score_percent": None,
			"scoring_mode": "mcq_percent",
		}
		return {
			"score": None,
			"summary": {
				**_build_empty_summary("assessment", summary_text),
				"detail": detail_text,
			},
			"dimension_scores": {},
			"report_section": _build_report_section(
				"assessment",
				status="not_started",
				score=None,
				summary=summary_text,
				detail=detail_text,
				metrics=metrics,
				recommendations=["Complete the Assessment to strengthen the final report signal."],
				extras=metrics,
			),
		}

	status_value = str(record.get("status") or "in_progress").strip().lower()
	status_value = "complete" if status_value == "completed" else status_value
	total_questions = int(record.get("total_questions") or 0)
	answered_count = int(record.get("answered_count") or 0)
	correct_count = int(record.get("correct_count") or 0)
	score_percent = _coerce_numeric_score(record.get("score_percent"))
	score = round((score_percent or 0.0) / 100.0, 4) if score_percent is not None else None
	summary_text = f"{correct_count}/{total_questions} correct" if total_questions else "Awaiting assessment session."
	detail_text = f"{answered_count}/{total_questions} answered" if total_questions else "Assessment data unavailable."
	metrics = {
		"answered_count": answered_count,
		"correct_count": correct_count,
		"total_questions": total_questions,
		"score_percent": score_percent,
		"completed_at": record.get("completed_at"),
		"scoring_mode": "mcq_percent",
	}
	strengths: list[str] = []
	risks: list[str] = []
	recommendations: list[str] = []
	evidence: list[str] = []
	if total_questions:
		evidence.append(f"{correct_count} of {total_questions} screening questions were answered correctly.")
		evidence.append(f"{answered_count} of {total_questions} screening questions were attempted.")
	if total_questions and answered_count == total_questions:
		strengths.append("All assessment questions were attempted within the timed screening window.")
	elif total_questions and answered_count < total_questions:
		risks.append("Some assessment questions were left unanswered, which reduced the screening signal.")
	if score is not None and score >= 0.75:
		strengths.append("Assessment fundamentals held up well under timed screening conditions.")
	elif score is not None and score < 0.6:
		risks.append("Screening accuracy stayed below the target bar.")
	if status_value != "complete":
		recommendations.append("Complete the Assessment to strengthen the final report signal.")
	elif score is not None and score < 0.6:
		recommendations.append(LOW_SCORE_GUIDANCE["assessment"])
	else:
		recommendations.append("Keep revising core fundamentals with short timed MCQ sets before the next mock session.")
	return {
		"score": score,
		"summary": {
			"key": "assessment",
			"label": ROUND_LABELS["assessment"],
			"status": status_value,
			"score": score,
			"summary": summary_text,
			"detail": detail_text,
		},
		"dimension_scores": {
			"answered_count": answered_count,
			"correct_count": correct_count,
			"total_questions": total_questions,
			"scoring_mode": "mcq_percent",
		},
		"report_section": _build_report_section(
			"assessment",
			status=status_value,
			score=score,
			summary=summary_text,
			detail=detail_text,
			metrics=metrics,
			strengths=strengths,
			risks=risks,
			recommendations=recommendations,
			evidence=evidence,
			extras=metrics,
		),
	}


def _build_interview_round_snapshot(session_id: str, round_name: str) -> dict[str, Any]:
	record = get_interview_round_session(session_id, round_name)
	responses = list_interview_responses(session_id, round_name)
	if record is None:
		summary_text = f"{ROUND_LABELS[round_name]} has not started yet."
		detail_text = "Complete the round to unlock scored feedback and recommendations."
		metrics = {
			"response_count": 0,
			"total_questions": 0,
			"average_scores": {},
			"evaluation_modes": [],
		}
		return {
			"score": None,
			"summary": {
				**_build_empty_summary(round_name, summary_text),
				"detail": detail_text,
			},
			"dimension_scores": {},
			"report_section": _build_report_section(
				round_name,
				status="not_started",
				score=None,
				summary=summary_text,
				detail=detail_text,
				metrics=metrics,
				recommendations=[f"Complete the {ROUND_LABELS[round_name]} to strengthen the final report signal."],
				extras={
					**metrics,
					"feedback_samples": [],
				},
			),
		}

	questions_json = _coerce_mapping(record.get("questions_json"))
	total_questions = _count_round_questions(questions_json, round_name)
	response_count = len(responses)
	response_count_source = "responses"
	current_question_index = int(record.get("current_question_index") or 0)
	if response_count == 0:
		fallback_response_count = 0
		if round_name == "technical":
			covered_topic_summary = _coerce_mapping(questions_json.get("covered_topic_summary"))
			fallback_response_count = int(covered_topic_summary.get("total_questions_covered") or 0)
		if fallback_response_count <= 0 and current_question_index > 0:
			fallback_response_count = current_question_index
		if total_questions > 0:
			fallback_response_count = min(fallback_response_count, total_questions)
		if fallback_response_count > 0:
			response_count = fallback_response_count
			response_count_source = "progress_fallback"
	status_value = str(record.get("status") or "in_progress").strip().lower()
	if status_value != "complete":
		status_value = "in_progress" if response_count or total_questions else "not_started"

	total_score = _coerce_numeric_score(record.get("total_score"))
	if total_score == 0.0 and not responses and response_count_source == "progress_fallback":
		total_score = None
	if total_score is None and responses:
		total_score = round(
			sum(float(response.get("score") or 0.0) for response in responses) / len(responses),
			4,
		)

	groq_scores = [
		float(response.get("groq_score"))
		for response in responses
		if _coerce_numeric_score(response.get("groq_score")) is not None
	]
	sbert_scores = [
		float(response.get("sbert_score"))
		for response in responses
		if _coerce_numeric_score(response.get("sbert_score")) is not None
	]
	communication_scores = [
		float(response.get("communication_score"))
		for response in responses
		if _coerce_numeric_score(response.get("communication_score")) is not None
	]
	evaluation_modes = sorted({
		str(response.get("evaluation_mode") or "unknown")
		for response in responses
		if str(response.get("evaluation_mode") or "").strip()
	})
	feedback_samples = [
		feedback
		for feedback in (_summarize_feedback(response.get("feedback")) for response in responses)
		if feedback
	][:2]
	concept_coverage = _build_concept_coverage_summary(responses)
	confidence_signal = _build_confidence_signal_summary(responses)
	skill_profile_summary, covered_topics, covered_topic_summary = _extract_skill_targeting_state(
		record,
		round_name,
		response_count=response_count,
	)
	average_scores = {
		key: value
		for key, value in {
			"groq": _average(groq_scores),
			"sbert": _average(sbert_scores),
			"communication": _average(communication_scores),
		}.items()
		if value is not None
	}
	dimension_scores = {
		"response_count": response_count,
		"total_questions": total_questions,
		"evaluation_modes": evaluation_modes,
		**average_scores,
	}
	if concept_coverage:
		dimension_scores["concept_coverage"] = concept_coverage
	if confidence_signal:
		dimension_scores["confidence_signal"] = confidence_signal
	if skill_profile_summary:
		dimension_scores["skill_profile_summary"] = skill_profile_summary
	if covered_topic_summary:
		dimension_scores["covered_topic_summary"] = covered_topic_summary
	if covered_topics:
		dimension_scores["covered_topics"] = covered_topics
	if total_questions:
		summary_noun = "answers logged" if response_count_source == "responses" else "prompts completed"
		summary_text = f"{response_count}/{total_questions} {summary_noun}"
	else:
		summary_text = "Waiting for round questions."
	if status_value == "complete" and response_count_source == "progress_fallback":
		detail_text = "Round completed, but detailed per-answer analytics were not fully preserved for this session."
	else:
		detail_text = "Round complete." if status_value == "complete" else "Round still in progress."
	metrics = {
		"response_count": response_count,
		"response_count_source": response_count_source,
		"total_questions": total_questions,
		"average_scores": average_scores,
		"evaluation_modes": evaluation_modes,
		"completed_at": record.get("completed_at"),
		"current_question_index": current_question_index,
	}
	strengths: list[str] = []
	risks: list[str] = []
	recommendations: list[str] = []
	evidence = list(feedback_samples)
	average_ratio = _coerce_numeric_score(concept_coverage.get("average_ratio")) if concept_coverage else None
	average_confidence = _coerce_numeric_score(confidence_signal.get("average_confidence")) if confidence_signal else None
	dominant_confidence_label = str(confidence_signal.get("dominant_confidence_label") or "").strip().lower() if confidence_signal else ""
	if total_score is not None and total_score >= 0.75:
		strengths.append(f"{ROUND_LABELS[round_name]} stayed above the target bar with clear scored responses.")
	elif total_score is not None and total_score < 0.6:
		risks.append(f"{ROUND_LABELS[round_name]} scoring stayed below the target bar.")
	if average_ratio is not None and average_ratio >= 0.7:
		strengths.append("Expected answer concepts were covered consistently across saved responses.")
	elif average_ratio is not None and average_ratio < 0.5:
		risks.append("Saved answers missed too many of the expected answer concepts.")
	if average_confidence is not None and average_confidence >= 0.7:
		strengths.append("Delivery stayed mostly direct and low on uncertainty markers.")
	elif dominant_confidence_label == "hesitant" or (average_confidence is not None and average_confidence < 0.5):
		risks.append("Saved answers showed visible hesitation and need tighter delivery.")
	if round_name == "technical" and skill_profile_summary:
		strong_count = int(skill_profile_summary.get("strong_count") or 0)
		absent_count = int(skill_profile_summary.get("absent_count") or 0)
		if strong_count > 0:
			strengths.append("The technical round reached resume-backed strengths instead of staying generic.")
		if absent_count > 0:
			risks.append("Some role-critical skills still look under-developed relative to the target role.")
	if concept_coverage:
		sample_covered_points = _unique_text(list(concept_coverage.get("sample_covered_points") or []), limit=2)
		sample_missing_points = _unique_text(list(concept_coverage.get("sample_missing_points") or []), limit=2)
		if sample_covered_points:
			evidence.append(f"Covered concepts: {', '.join(sample_covered_points)}.")
		if sample_missing_points:
			evidence.append(f"Still missing: {', '.join(sample_missing_points)}.")
	if covered_topic_summary:
		covered_focus_skills = _unique_text(list(covered_topic_summary.get("covered_focus_skills") or []), limit=3)
		if covered_focus_skills:
			evidence.append(f"Tracked focus skills: {', '.join(covered_focus_skills)}.")
	if round_name == "technical" and skill_profile_summary:
		absent_skills = _unique_text(list(skill_profile_summary.get("absent_skills") or []), limit=3)
		if absent_skills:
			evidence.append(f"Role-skill gaps: {', '.join(absent_skills)}.")
	if status_value != "complete":
		recommendations.append(f"Complete the {ROUND_LABELS[round_name]} to strengthen the final report signal.")
	elif total_score is not None and total_score < 0.6:
		recommendations.append(LOW_SCORE_GUIDANCE[round_name])
	else:
		if average_ratio is not None and average_ratio < 0.7:
			recommendations.append("Cover the expected answer points earlier before expanding into examples.")
		if dominant_confidence_label == "hesitant" or (average_confidence is not None and average_confidence < 0.6):
			recommendations.append("Reduce hedging and filler phrases so the first answer lands more directly.")
	if not recommendations:
		recommendations.append("Preserve this answer structure and keep pairing concise explanations with concrete examples.")
	report_section_extras = {
		**metrics,
		"feedback_samples": feedback_samples,
	}
	if concept_coverage:
		report_section_extras["concept_coverage"] = concept_coverage
	if confidence_signal:
		report_section_extras["confidence_signal"] = confidence_signal
	if skill_profile_summary:
		report_section_extras["skill_profile_summary"] = skill_profile_summary
	if covered_topic_summary:
		report_section_extras["covered_topic_summary"] = covered_topic_summary
	if covered_topics:
		report_section_extras["covered_topics"] = covered_topics
	report_section = _build_report_section(
		round_name,
		status=status_value,
		score=total_score,
		summary=summary_text,
		detail=detail_text,
		metrics=metrics,
		strengths=strengths,
		risks=risks,
		recommendations=recommendations,
		evidence=evidence,
		extras=report_section_extras,
	)
	return {
		"score": total_score,
		"summary": {
			"key": round_name,
			"label": ROUND_LABELS[round_name],
			"status": status_value,
			"score": total_score,
			"summary": summary_text,
			"detail": detail_text,
		},
		"dimension_scores": dimension_scores,
		"report_section": report_section,
	}


def _has_saved_debrief(record: Mapping[str, Any]) -> bool:
	state = _coerce_mapping(record.get("state_json"))
	debrief_messages = state.get("debrief_messages") or []
	return isinstance(debrief_messages, list) and len(debrief_messages) > 0


def _dsa_hidden_tests_passed(record: Mapping[str, Any]) -> bool:
	execution_results = _coerce_mapping(record.get("execution_results"))
	total_count = int(execution_results.get("total_count") or 0)
	passed_count = int(execution_results.get("passed_count") or 0)
	return total_count > 0 and passed_count == total_count


def _dsa_question_counts_as_complete(record: Mapping[str, Any]) -> bool:
	stage = str(record.get("stage") or "problem_setup")
	if bool(record.get("completed_at")) or stage == "complete" or _has_saved_debrief(record):
		return True
	return stage == "debrief" and _dsa_hidden_tests_passed(record)


def _derive_dsa_question_score(record: Mapping[str, Any]) -> float | None:
	persisted_score = _coerce_numeric_score(record.get("total_score"))
	if persisted_score is not None:
		return round(persisted_score, 4)
	execution_results = _coerce_mapping(record.get("execution_results"))
	total_count = int(execution_results.get("total_count") or 0)
	passed_count = int(execution_results.get("passed_count") or 0)
	if total_count <= 0:
		return None
	return round(passed_count / total_count, 4)


def _build_dsa_progress_summary_from_records(records: list[Mapping[str, Any]]) -> dict[str, Any]:
	records_by_question = {
		int(record.get("question_number") or 0): record
		for record in records
		if int(record.get("question_number") or 0) in {1, 2}
	}
	question_entries: list[dict[str, Any]] = []
	for question_number in (1, 2):
		record = records_by_question.get(question_number)
		if record is None:
			question_entries.append({
				"question_number": question_number,
				"started": False,
				"status": "not_started",
				"completed": False,
				"score": None,
			})
			continue
		stage = str(record.get("stage") or "problem_setup")
		completed = _dsa_question_counts_as_complete(record)
		question_entries.append({
			"question_number": question_number,
			"started": True,
			"status": "complete" if completed else stage,
			"completed": completed,
			"score": _derive_dsa_question_score(record),
		})
	question_one_score = question_entries[0].get("score")
	question_two_score = question_entries[1].get("score")
	round_completed = bool(question_entries[0]["completed"] and question_entries[1]["completed"])
	aggregate_score = None
	if round_completed:
		aggregate_score = round(
			(float(question_one_score or 0.0) * 0.45) + (float(question_two_score or 0.0) * 0.55),
			4,
		)
	return {
		"completed_question_count": sum(1 for entry in question_entries if entry["completed"]),
		"round_completed": round_completed,
		"aggregate_score": aggregate_score,
		"questions": question_entries,
	}


def _build_dsa_snapshot(session_id: str) -> dict[str, Any]:
	records = list_dsa_sessions(session_id)
	progress_summary = _build_dsa_progress_summary_from_records(records)
	has_started = any(entry.get("started") for entry in progress_summary["questions"])
	status_value = "complete" if progress_summary["round_completed"] else "in_progress" if has_started else "not_started"
	question_scores = {
		f"q{entry['question_number']}": entry.get("score")
		for entry in progress_summary["questions"]
		if entry.get("score") is not None
	}
	if not records:
		summary_text = "DSA Round has not started yet."
		detail_text = "Complete both DSA questions to unlock the weighted aggregate score."
		metrics = {
			"aggregate_score": None,
			"completed_question_count": 0,
			"question_scores": {},
			"scoring_mode": "weighted_execution_pass_ratio",
		}
		return {
			"score": None,
			"summary": {
				"key": "dsa",
				"label": ROUND_LABELS["dsa"],
				"status": "not_started",
				"score": None,
				"summary": summary_text,
				"detail": detail_text,
			},
			"dimension_scores": {
				"question_scores": {},
				"completed_question_count": 0,
				"scoring_mode": "weighted_execution_pass_ratio",
			},
			"report_section": _build_report_section(
				"dsa",
				status="not_started",
				score=None,
				summary=summary_text,
				detail=detail_text,
				metrics=metrics,
				recommendations=["Complete the DSA Round to strengthen the final report signal."],
				extras={
					**metrics,
					"questions": progress_summary.get("questions") or [],
				},
			),
		}
	dsa_round_report = build_dsa_round_report(records=records)
	question_reports = list(dsa_round_report.get("question_reports") or [])
	strengths = list(dsa_round_report.get("strengths") or [])
	risks = _unique_text(
		[
			str(item)
			for report in question_reports
			for item in (report.get("risks") or [])
		],
		limit=4,
	)
	recommendations = list(dsa_round_report.get("recommendations") or [])
	evidence: list[str] = []
	if progress_summary.get("aggregate_score") is not None and float(progress_summary["aggregate_score"] or 0.0) >= 0.75:
		strengths.insert(0, "DSA performance stayed strong across both questions with the weighted aggregate above target.")
	elif has_started and not progress_summary["round_completed"]:
		risks.append("Only part of the DSA round is complete, so the weighted aggregate is not final yet.")
	for report in question_reports:
		question_number = int(report.get("question_number") or 0)
		summary_text = str(report.get("strategy_summary") or report.get("analysis_summary") or "").strip()
		if summary_text:
			evidence.append(f"Q{question_number}: {summary_text}")
		coding_journey = _coerce_mapping(report.get("coding_journey"))
		submission_count = int(coding_journey.get("submission_count") or 0)
		if submission_count > 0:
			evidence.append(f"Q{question_number}: {submission_count} saved submissions before the final evaluation.")
	if not progress_summary["round_completed"]:
		recommendations.insert(0, "Complete both DSA questions to unlock the full weighted round score.")
	elif progress_summary.get("aggregate_score") is not None and float(progress_summary["aggregate_score"] or 0.0) < 0.6:
		recommendations.append(LOW_SCORE_GUIDANCE["dsa"])
	if not recommendations:
		recommendations.append("Keep explaining complexity, edge cases, and tradeoffs explicitly during the debrief.")
	summary_text = f"{progress_summary['completed_question_count']}/2 questions complete"
	detail_text = "Weighted Q1/Q2 aggregate is available." if progress_summary.get("aggregate_score") is not None else "Complete both DSA questions to unlock the aggregate score."
	metrics = {
		"aggregate_score": progress_summary.get("aggregate_score"),
		"completed_question_count": progress_summary.get("completed_question_count"),
		"question_scores": question_scores,
		"scoring_mode": "weighted_execution_pass_ratio",
		"question_report_count": len(question_reports),
	}
	return {
		"score": progress_summary.get("aggregate_score"),
		"summary": {
			"key": "dsa",
			"label": ROUND_LABELS["dsa"],
			"status": status_value,
			"score": progress_summary.get("aggregate_score"),
			"summary": summary_text,
			"detail": detail_text,
		},
		"dimension_scores": {
			"question_scores": question_scores,
			"completed_question_count": progress_summary.get("completed_question_count"),
			"scoring_mode": "weighted_execution_pass_ratio",
			"question_reports": question_reports,
		},
		"report_section": _build_report_section(
			"dsa",
			status=status_value,
			score=progress_summary.get("aggregate_score"),
			summary=summary_text,
			detail=detail_text,
			metrics=metrics,
			strengths=strengths,
			risks=risks,
			recommendations=recommendations,
			evidence=evidence,
			extras={
				**metrics,
				"questions": progress_summary.get("questions") or [],
				"question_reports": question_reports,
			},
		),
	}


def _build_highlights(round_summaries: list[dict[str, Any]]) -> list[str]:
	highlights: list[str] = []
	for entry in round_summaries:
		score = _coerce_numeric_score(entry.get("score"))
		if entry.get("status") != "complete" or score is None or score < 0.75:
			continue
		if entry.get("key") == "assessment":
			highlights.append("Assessment fundamentals held up well under timed screening conditions.")
		elif entry.get("key") == "dsa":
			highlights.append("DSA performance stayed strong across both questions with the weighted aggregate above target.")
		else:
			highlights.append(f"{entry.get('label')} stayed above the target bar with clear scored responses.")
	if highlights:
		return highlights[:3]
	if any(entry.get("status") == "complete" for entry in round_summaries):
		return ["Persisted scores exist, but stronger highlight signals will emerge once more rounds are completed."]
	return ["No completed stages are available yet for highlight extraction."]


def _build_recommendations(round_summaries: list[dict[str, Any]], persistence_detail: str | None = None) -> list[str]:
	recommendations: list[str] = []
	for entry in round_summaries:
		status_value = str(entry.get("status") or "not_started")
		score = _coerce_numeric_score(entry.get("score"))
		if status_value != "complete":
			recommendations.append(f"Complete the {entry.get('label')} to strengthen the final report signal.")
			continue
		if score is not None and score < 0.6:
			recommendations.append(LOW_SCORE_GUIDANCE.get(str(entry.get("key")) or "", "Revisit this stage before the next mock session."))
	if persistence_detail:
		recommendations.append("This report was generated but could not be saved. Check the database connection so report snapshots persist across sessions.")
	if not recommendations:
		recommendations.append("No critical gaps were flagged from the currently persisted stages.")
	unique_recommendations: list[str] = []
	for item in recommendations:
		if item not in unique_recommendations:
			unique_recommendations.append(item)
	return unique_recommendations[:4]


def _compute_overall_score(round_scores: Mapping[str, Any]) -> float | None:
	values = [
		float(value)
		for value in round_scores.values()
		if _coerce_numeric_score(value) is not None
	]
	if not values:
		return None
	return round(sum(values) / len(values), 4)


def _load_persisted_report(session_id: str) -> tuple[dict[str, Any] | None, bool, str | None]:
	"""Read the saved report snapshot, if one exists.

	A session with no saved report yet is not an error: MongoDB simply returns no
	document and ``get_final_report`` yields ``None``, and the caller rebuilds the
	snapshot from the round collections. A raised ``DatabaseClientError`` therefore
	means the database itself is unreachable, which is a genuine outage.
	"""
	try:
		return get_final_report(session_id), True, None
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc


def _build_report_snapshot(
	*,
	session_id: str,
	parent_session: Mapping[str, Any],
	existing_report: Mapping[str, Any] | None,
	persistence_detail: str | None = None,
) -> dict[str, Any]:
	try:
		assessment_snapshot = _build_assessment_snapshot(session_id)
		technical_snapshot = _build_interview_round_snapshot(session_id, "technical")
		dsa_snapshot = _build_dsa_snapshot(session_id)
		project_snapshot = _build_interview_round_snapshot(session_id, "project_discussion")
		hr_snapshot = _build_interview_round_snapshot(session_id, "hr")
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc

	stage_snapshots = {
		"assessment": assessment_snapshot,
		"technical": technical_snapshot,
		"dsa": dsa_snapshot,
		"project_discussion": project_snapshot,
		"hr": hr_snapshot,
	}
	round_summaries = [
		stage_snapshots[key]["summary"]
		for key in ROUND_ORDER
	]
	base_round_scores = _coerce_mapping(existing_report.get("round_scores")) if isinstance(existing_report, Mapping) else {}
	round_scores = dict(base_round_scores)
	for key, snapshot in stage_snapshots.items():
		score = snapshot.get("score")
		if _coerce_numeric_score(score) is not None:
			round_scores[key] = float(score)
	base_dimension_scores = _coerce_mapping(existing_report.get("dimension_scores")) if isinstance(existing_report, Mapping) else {}
	dimension_scores = {
		**base_dimension_scores,
		**{
			key: snapshot.get("dimension_scores") or {}
			for key, snapshot in stage_snapshots.items()
		},
	}
	highlights = _build_highlights(round_summaries)
	recommendations = _build_recommendations(round_summaries, persistence_detail=persistence_detail)
	overview = {
		"session_status": str(parent_session.get("status") or ""),
		"role_selected": str(parent_session.get("role_selected") or ""),
		"generated_at": _utcnow_iso(),
		"completed_stage_count": sum(1 for entry in round_summaries if entry.get("status") == "complete"),
		"scored_stage_count": sum(1 for entry in round_summaries if _coerce_numeric_score(entry.get("score")) is not None),
	}
	base_report_json = _coerce_mapping(existing_report.get("report_json")) if isinstance(existing_report, Mapping) else {}
	report_json = {
		**base_report_json,
		"overview": overview,
		"round_summaries": round_summaries,
		"highlights": highlights,
		"recommendations": recommendations,
		"assessment": assessment_snapshot["report_section"],
		"technical": technical_snapshot["report_section"],
		"dsa": dsa_snapshot["report_section"],
		"project_discussion": project_snapshot["report_section"],
		"hr": hr_snapshot["report_section"],
	}
	timestamp = _utcnow_iso()
	return {
		"session_id": session_id,
		"overall_score": _compute_overall_score(round_scores),
		"round_scores": round_scores,
		"dimension_scores": dimension_scores,
		"report_json": report_json,
		"created_at": (existing_report.get("created_at") if isinstance(existing_report, Mapping) else None) or timestamp,
		"updated_at": timestamp,
	}


def _persist_report_snapshot(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], bool, bool, str | None]:
	"""Save the report snapshot, degrading gracefully when the write fails.

	The snapshot is derived from the round collections that were just read, so a
	failure to *store* it must not block *showing* it. The caller receives the
	computed snapshot with ``persisted=False`` plus the failure detail, which is
	surfaced to the user as a recommendation rather than as a 5xx.
	"""
	try:
		persisted = upsert_final_report(
			session_id=str(snapshot.get("session_id") or ""),
			overall_score=snapshot.get("overall_score"),
			round_scores=_coerce_mapping(snapshot.get("round_scores")),
			dimension_scores=_coerce_mapping(snapshot.get("dimension_scores")),
			report_json=_coerce_mapping(snapshot.get("report_json")),
		)
		return persisted, True, True, None
	except DatabaseClientError as exc:
		_LOGGER.warning(
			"Failed to persist final report for session %s: %s",
			snapshot.get("session_id"),
			exc,
		)
		return dict(snapshot), False, False, str(exc)


def _require_parent_session(
	session_id: str,
	current_user: AuthenticatedUser,
) -> Mapping[str, Any]:
	try:
		parent_session = get_session(session_id)
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail=str(exc),
		) from exc
	if parent_session is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="The supplied session_id does not exist.",
		)
	return ensure_session_access(parent_session, current_user)


@router.get("/session/{session_id}")
def get_report_session(
	session_id: str,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	parent_session = _require_parent_session(session_id, current_user)
	existing_report, persistence_supported, persistence_detail = _load_persisted_report(session_id)
	snapshot = _build_report_snapshot(
		session_id=session_id,
		parent_session=parent_session,
		existing_report=existing_report,
		persistence_detail=persistence_detail,
	)
	return {
		"session_id": session_id,
		"role_selected": str(parent_session.get("role_selected") or ""),
		"session_status": str(parent_session.get("status") or ""),
		"report_exists": existing_report is not None,
		"persistence_supported": persistence_supported,
		"persistence_detail": persistence_detail,
		"report": existing_report,
		"snapshot": snapshot,
	}


@router.post("/session/{session_id}/generate")
def generate_report_session(
	session_id: str,
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, Any]:
	parent_session = _require_parent_session(session_id, current_user)
	existing_report, persistence_supported, persistence_detail = _load_persisted_report(session_id)
	snapshot = _build_report_snapshot(
		session_id=session_id,
		parent_session=parent_session,
		existing_report=existing_report,
		persistence_detail=persistence_detail,
	)
	report_record, persisted, resolved_persistence_supported, resolved_persistence_detail = _persist_report_snapshot(snapshot)
	return {
		"session_id": session_id,
		"role_selected": str(parent_session.get("role_selected") or ""),
		"session_status": str(parent_session.get("status") or ""),
		"persisted": persisted,
		"persistence_supported": resolved_persistence_supported,
		"persistence_detail": resolved_persistence_detail or persistence_detail,
		"report": report_record,
		"snapshot": snapshot,
	}


__all__ = ["router", "get_report_session", "generate_report_session"]
