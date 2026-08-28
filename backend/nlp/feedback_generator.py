"""Round-level and session-level feedback aggregation.

This module consumes the per-answer EvaluationResult records already stored in
Supabase and produces:

1. ``RoundFeedback`` — coaching summary for one round (hr / technical / project_discussion).
2. ``SessionFeedback`` — cross-round summary for the final report.

Both are generated in a single Groq call per object.
Local score aggregation runs before the Groq call so the prompt contains
accurate numbers rather than relying on the LLM to compute averages.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from backend.config import GroqSettings, get_settings
from backend.nlp.groq_client import (
	GroqCompletionError,
	GroqDependencyError,
	GroqRateLimitError,
	create_chat_completion,
)

_LOGGER = logging.getLogger(__name__)

_JSON_FENCE = re.compile(
	r"```(?:json)?\s*(?P<body>\{.*?\})\s*```",
	re.DOTALL,
)

_CONFIDENCE_THRESHOLD_DEFAULTS: dict[str, dict[str, float]] = {
	"hr": {
		"hesitant_max": 0.58,
		"confident_min": 0.74,
		"quality_floor": 0.68,
	},
	"technical": {
		"hesitant_max": 0.52,
		"confident_min": 0.70,
		"quality_floor": 0.62,
	},
	"project_discussion": {
		"hesitant_max": 0.55,
		"confident_min": 0.71,
		"quality_floor": 0.63,
	},
}

# ---------------------------------------------------------------------------
# Public TypedDicts
# ---------------------------------------------------------------------------


class RoundFeedback(TypedDict):
	"""Coaching feedback summary for one interview round."""

	round: str                      # hr | technical | project_discussion
	average_score: float            # 0.0 – 1.0
	question_count: int
	strengths: list[str]            # 2-4 bullet points
	improvement_areas: list[str]    # 2-4 bullet points
	coaching_note: str              # 2-3 sentence narrative
	confidence_signal: "ConfidenceSignalSummary"
	dimension_averages: dict[str, float]  # groq / sbert / communication
	feedback_mode: str              # groq | local_fallback
	fallback_reason: str | None


class ConfidenceThresholdProfile(TypedDict):
	"""Round-specific thresholds used for confidence narration."""

	hesitant_max: float
	confident_min: float
	quality_floor: float
	calibration_source: str
	sample_size: int
	high_quality_sample_size: int
	low_quality_sample_size: int


class ConfidenceSignalSummary(TypedDict):
	"""Aggregated confidence and hesitation signal for one round."""

	available_responses: int
	sentiment_backed_responses: int
	average_confidence: float
	average_sentiment_compound: float
	average_hedging_ratio: float
	average_filler_ratio: float
	dominant_confidence_label: str | None
	dominant_sentiment_label: str | None
	hesitation_detected: bool
	confident_delivery: bool
	threshold_profile: ConfidenceThresholdProfile


class SessionFeedback(TypedDict):
	"""Cross-round summary for the final report."""

	overall_score: float
	round_scores: dict[str, float]  # round_name -> score
	top_strengths: list[str]
	critical_gaps: list[str]
	priority_recommendations: list[str]
	readiness_label: str            # strong | ready | developing | early


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FeedbackGeneratorError(RuntimeError):
	"""Base error for feedback generation."""


class FeedbackGeneratorLlmError(FeedbackGeneratorError):
	"""Raised when Groq returns an invalid or unparseable response."""


class FeedbackGeneratorQuotaError(FeedbackGeneratorLlmError):
	"""Raised when Groq feedback generation cannot run because quota or rate limits were exceeded."""


class FeedbackGeneratorUnavailableError(FeedbackGeneratorError):
	"""Raised when Groq is not configured."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_json_object(raw: str) -> dict[str, Any]:
	text = raw.strip()
	fence = _JSON_FENCE.search(text)
	if fence:
		text = fence.group("body")

	start = text.find("{")
	end = text.rfind("}")
	if start == -1 or end == -1:
		raise FeedbackGeneratorLlmError(
			f"Feedback response did not contain a JSON object: {raw[:200]}"
		)
	try:
		obj = json.loads(text[start : end + 1])
	except json.JSONDecodeError as exc:
		raise FeedbackGeneratorLlmError(f"Feedback JSON was malformed: {exc}") from exc
	if not isinstance(obj, dict):
		raise FeedbackGeneratorLlmError("Feedback JSON top-level value was not a dict.")
	return obj


def _str_list(value: Any, limit: int = 8) -> list[str]:
	if not isinstance(value, list):
		return []
	return [str(v).strip() for v in value[:limit] if str(v).strip()]


def _coerce_mapping(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, Mapping) else {}


def _clamp(val: Any) -> float:
	try:
		return round(max(0.0, min(1.0, float(val))), 4)
	except (TypeError, ValueError):
		return 0.0


def _maybe_float(value: Any) -> float | None:
	try:
		if value is None:
			return None
		return float(value)
	except (TypeError, ValueError):
		return None


def _average(values: Sequence[float]) -> float:
	if not values:
		return 0.0
	return round(sum(values) / len(values), 4)


def _percentile(sorted_values: Sequence[float], quantile: float) -> float:
	if not sorted_values:
		return 0.0
	if len(sorted_values) == 1:
		return round(float(sorted_values[0]), 4)
	position = max(0.0, min(1.0, quantile)) * (len(sorted_values) - 1)
	lower_index = int(position)
	upper_index = min(lower_index + 1, len(sorted_values) - 1)
	weight = position - lower_index
	lower_value = float(sorted_values[lower_index])
	upper_value = float(sorted_values[upper_index])
	return round(lower_value + (upper_value - lower_value) * weight, 4)


def _dominant_label(labels: Sequence[str]) -> str | None:
	counts: dict[str, int] = {}
	for raw_label in labels:
		label = str(raw_label or "").strip()
		if not label:
			continue
		counts[label] = counts.get(label, 0) + 1
	if not counts:
		return None
	return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def _extract_or_compute_communication_detail(record: Mapping[str, Any]) -> dict[str, Any]:
	communication = _coerce_mapping(record.get("communication"))
	if communication and communication.get("confidence_score") is not None:
		return communication
	dimension_scores = _coerce_mapping(record.get("dimension_scores"))
	stored_communication = _coerce_mapping(
		dimension_scores.get("communication_detail") or dimension_scores.get("communication")
	)
	if stored_communication.get("confidence_score") is not None:
		return stored_communication
	answer_text = str(record.get("user_answer_text") or record.get("answer") or "").strip()
	if not answer_text:
		return communication or stored_communication
	try:
		from backend.nlp.answer_evaluator import _compute_communication_score
		computed = dict(_compute_communication_score(answer_text))
		merged = dict(stored_communication or communication)
		merged.update(computed)
		return merged
	except Exception:
		_LOGGER.debug("Could not compute communication detail from transcript for feedback generation.", exc_info=True)
		return communication or stored_communication


def _default_confidence_thresholds(round_name: str) -> ConfidenceThresholdProfile:
	defaults = _CONFIDENCE_THRESHOLD_DEFAULTS.get(round_name, _CONFIDENCE_THRESHOLD_DEFAULTS["technical"])
	return ConfidenceThresholdProfile(
		hesitant_max=round(float(defaults["hesitant_max"]), 4),
		confident_min=round(float(defaults["confident_min"]), 4),
		quality_floor=round(float(defaults["quality_floor"]), 4),
		calibration_source="bootstrap_defaults",
		sample_size=0,
		high_quality_sample_size=0,
		low_quality_sample_size=0,
	)


def calibrate_confidence_thresholds(
	round_name: str,
	evaluation_records: Sequence[Mapping[str, Any]],
	*,
	min_samples: int = 12,
	min_quality_samples: int | None = None,
) -> ConfidenceThresholdProfile:
	"""Estimate round-specific hesitation thresholds from saved transcripts."""

	profile = _default_confidence_thresholds(round_name)
	pairs: list[tuple[float, float]] = []
	for record in evaluation_records:
		communication = _extract_or_compute_communication_detail(record)
		confidence_score = _maybe_float(communication.get("confidence_score"))
		if confidence_score is None:
			continue
		final_score = _clamp(record.get("final_score", record.get("score", 0.0)))
		pairs.append((max(0.0, min(1.0, confidence_score)), final_score))

	if len(pairs) < min_samples:
		profile["sample_size"] = len(pairs)
		return profile

	quality_floor = float(profile["quality_floor"])
	high_quality = sorted(confidence for confidence, score in pairs if score >= quality_floor)
	low_quality = sorted(confidence for confidence, score in pairs if score < quality_floor)
	profile["sample_size"] = len(pairs)
	profile["high_quality_sample_size"] = len(high_quality)
	profile["low_quality_sample_size"] = len(low_quality)
	required_quality_samples = max(1, int(min_quality_samples or max(3, min_samples // 4)))
	if len(high_quality) < required_quality_samples or len(low_quality) < required_quality_samples:
		profile["calibration_source"] = "bootstrap_defaults_insufficient_quality_split"
		return profile
	all_confidences = sorted(confidence for confidence, _ in pairs)

	hesitant_max = float(profile["hesitant_max"])
	if high_quality and low_quality:
		boundary = (_percentile(low_quality, 0.7) + _percentile(high_quality, 0.35)) / 2
		hesitant_max = boundary
	elif low_quality:
		hesitant_max = _percentile(low_quality, 0.7)
	else:
		hesitant_max = _percentile(all_confidences, 0.35)

	if high_quality:
		confident_min = _percentile(high_quality, 0.6)
	else:
		confident_min = _percentile(all_confidences, 0.75)

	hesitant_max = max(0.0, min(hesitant_max, 0.92))
	confident_min = max(hesitant_max + 0.06, min(1.0, confident_min))
	if confident_min > 0.95:
		confident_min = 0.95
		hesitant_max = min(hesitant_max, confident_min - 0.06)

	return ConfidenceThresholdProfile(
		hesitant_max=round(hesitant_max, 4),
		confident_min=round(confident_min, 4),
		quality_floor=round(quality_floor, 4),
		calibration_source="transcript_percentiles",
		sample_size=len(pairs),
		high_quality_sample_size=len(high_quality),
		low_quality_sample_size=len(low_quality),
	)


def summarize_confidence_signal(
	round_name: str,
	evaluation_records: Sequence[Mapping[str, Any]],
	*,
	min_calibration_samples: int = 12,
) -> ConfidenceSignalSummary:
	"""Aggregate confidence and hesitation signals for one round."""

	confidence_scores: list[float] = []
	sentiment_compounds: list[float] = []
	hedging_ratios: list[float] = []
	filler_ratios: list[float] = []
	confidence_labels: list[str] = []
	sentiment_labels: list[str] = []
	available_responses = 0
	sentiment_backed_responses = 0

	for record in evaluation_records:
		communication = _extract_or_compute_communication_detail(record)
		if not communication:
			continue
		available_responses += 1
		confidence_score = _maybe_float(communication.get("confidence_score"))
		if confidence_score is not None:
			confidence_scores.append(max(0.0, min(1.0, confidence_score)))
		sentiment_compound = _maybe_float(communication.get("sentiment_compound"))
		if sentiment_compound is not None:
			sentiment_compounds.append(sentiment_compound)
		hedging_ratio = _maybe_float(communication.get("hedging_ratio"))
		if hedging_ratio is not None:
			hedging_ratios.append(max(0.0, min(1.0, hedging_ratio)))
		filler_ratio = _maybe_float(communication.get("filler_ratio"))
		if filler_ratio is not None:
			filler_ratios.append(max(0.0, min(1.0, filler_ratio)))
		confidence_label = str(communication.get("confidence_label") or "").strip()
		if confidence_label:
			confidence_labels.append(confidence_label)
		sentiment_label = str(communication.get("sentiment_label") or "").strip()
		if sentiment_label:
			sentiment_labels.append(sentiment_label)
		if communication.get("sentiment_available") is True:
			sentiment_backed_responses += 1

	threshold_profile = calibrate_confidence_thresholds(
		round_name,
		evaluation_records,
		min_samples=min_calibration_samples,
	)
	average_confidence = _average(confidence_scores)
	average_hedging = _average(hedging_ratios)
	hesitation_detected = (
		available_responses > 0
		and (
			average_confidence <= threshold_profile["hesitant_max"]
			or _dominant_label(confidence_labels) == "hesitant"
			or average_hedging >= (0.11 if round_name == "hr" else 0.08)
		)
	)
	confident_delivery = (
		available_responses > 0
		and average_confidence >= threshold_profile["confident_min"]
		and average_hedging <= (0.07 if round_name == "hr" else 0.06)
	)

	return ConfidenceSignalSummary(
		available_responses=available_responses,
		sentiment_backed_responses=sentiment_backed_responses,
		average_confidence=average_confidence,
		average_sentiment_compound=_average(sentiment_compounds),
		average_hedging_ratio=average_hedging,
		average_filler_ratio=_average(filler_ratios),
		dominant_confidence_label=_dominant_label(confidence_labels),
		dominant_sentiment_label=_dominant_label(sentiment_labels),
		hesitation_detected=hesitation_detected,
		confident_delivery=confident_delivery,
		threshold_profile=threshold_profile,
	)


def _aggregate_scores(
	evaluation_records: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
	"""Compute mean scores from a list of EvaluationResult-shaped dicts."""
	if not evaluation_records:
		return {"groq": 0.0, "sbert": 0.0, "communication": 0.0, "final": 0.0}

	totals: dict[str, float] = {"groq": 0.0, "sbert": 0.0, "communication": 0.0, "final": 0.0}
	count = len(evaluation_records)

	for rec in evaluation_records:
		totals["groq"] += _clamp(rec.get("groq_score", rec.get("score", 0.0)))
		totals["sbert"] += _clamp(rec.get("sbert_score", rec.get("sbert_similarity", 0.0)))
		totals["communication"] += _clamp(rec.get("communication_score", 0.0))
		totals["final"] += _clamp(rec.get("final_score", rec.get("score", 0.0)))

	return {k: round(v / count, 4) for k, v in totals.items()}


def _build_round_summary_block(
	round_name: str,
	evaluation_records: Sequence[Mapping[str, Any]],
) -> str:
	"""Format evaluation records into a compact text block for the Groq prompt."""
	lines: list[str] = [f"Round: {round_name}"]
	for idx, rec in enumerate(evaluation_records, start=1):
		q = str(rec.get("question_text") or rec.get("question") or "").strip()[:120]
		ans = str(rec.get("user_answer_text") or rec.get("answer") or "").strip()[:200]
		score = _clamp(rec.get("final_score", rec.get("score", 0.0)))
		fb = str(rec.get("feedback") or "").strip()[:180]
		lines.append(
			f"  Q{idx}: {q}\n"
			f"    Score: {score:.2f}  |  Feedback: {fb}\n"
			f"    Answer snippet: {ans}"
		)
	return "\n".join(lines)


def _build_confidence_signal_block(confidence_signal: ConfidenceSignalSummary) -> str:
	if confidence_signal["available_responses"] <= 0:
		return "Confidence signal: unavailable"
	thresholds = confidence_signal["threshold_profile"]
	return (
		"Confidence signal:\n"
		f"  Average confidence : {confidence_signal['average_confidence']:.2f} / 1.0\n"
		f"  Dominant label     : {confidence_signal.get('dominant_confidence_label') or 'unknown'}\n"
		f"  Avg hedging ratio  : {confidence_signal['average_hedging_ratio']:.2f}\n"
		f"  Avg filler ratio   : {confidence_signal['average_filler_ratio']:.2f}\n"
		f"  Hesitation detected: {'yes' if confidence_signal['hesitation_detected'] else 'no'}\n"
		f"  Thresholds         : hesitant <= {thresholds['hesitant_max']:.2f}, confident >= {thresholds['confident_min']:.2f} ({thresholds['calibration_source']})"
	)


def _dedupe_lines(items: Sequence[str], *, limit: int) -> list[str]:
	result: list[str] = []
	seen: set[str] = set()
	for item in items:
		text = str(item or "").strip()
		if not text:
			continue
		key = text.casefold()
		if key in seen:
			continue
		seen.add(key)
		result.append(text)
		if len(result) >= limit:
			break
	return result


def _question_label(record: Mapping[str, Any]) -> str:
	text = str(record.get("question_text") or record.get("question") or "").strip()
	if not text:
		return "the core topics in this round"
	text = text.rstrip("?")
	return text if len(text) <= 90 else f"{text[:87].rstrip()}..."


def _fallback_reason_code(exc: FeedbackGeneratorError | None, *, groq_configured: bool) -> str | None:
	if exc is None:
		return None
	if isinstance(exc, FeedbackGeneratorQuotaError):
		return "groq_rate_limit"
	if isinstance(exc, FeedbackGeneratorUnavailableError):
		return "groq_unavailable" if groq_configured else "groq_not_configured"
	return "groq_error"


def _build_local_round_feedback(
	round_name: str,
	evaluation_records: Sequence[Mapping[str, Any]],
	*,
	fallback_reason: str | None,
) -> RoundFeedback:
	dim_avgs = _aggregate_scores(evaluation_records)
	confidence_signal = summarize_confidence_signal(round_name, evaluation_records)
	ranked = sorted(
		evaluation_records,
		key=lambda rec: _clamp(rec.get("final_score", rec.get("score", 0.0))),
		reverse=True,
	)
	round_focus = {
		"hr": "the candidate's motivation and communication",
		"technical": "the core technical concepts for the selected role",
		"project_discussion": "the candidate's project choices and technical trade-offs",
	}.get(round_name, "the key topics in this round")

	strength_candidates: list[str] = []
	if dim_avgs["sbert"] >= 0.55:
		strength_candidates.append(f"You stayed reasonably close to {round_focus} in several answers.")
	if dim_avgs["communication"] >= 0.65:
		strength_candidates.append("Your explanations were mostly clear and easy to follow.")
	if confidence_signal["confident_delivery"]:
		strength_candidates.append("Your delivery sounded steady and low on hesitation across most answers.")
	if ranked and _clamp(ranked[0].get("final_score", ranked[0].get("score", 0.0))) >= 0.6:
		strength_candidates.append(f"You handled {_question_label(ranked[0])} relatively well.")
	if not strength_candidates:
		strength_candidates.append(f"You made a usable attempt to address {round_focus}.")
	if len(strength_candidates) < 2:
		strength_candidates.append(f"You produced enough signal in this round to identify concrete next steps around {round_focus}.")

	improvement_candidates: list[str] = []
	if dim_avgs["sbert"] < 0.5:
		improvement_candidates.append("Address the exact concept in the question more directly and cover the expected key points explicitly.")
	if dim_avgs["communication"] < 0.55:
		improvement_candidates.append("Use a clearer structure: definition, key mechanism, and one short example.")
	if confidence_signal["hesitation_detected"]:
		if round_name == "hr":
			improvement_candidates.append("Your HR delivery sounded hesitant in places. State ownership, motivation, and examples more directly instead of softening them with qualifiers.")
		else:
			improvement_candidates.append("Your delivery sounded hesitant in parts of this round. Lead with the exact concept or approach first, then justify it with one short example.")
	if ranked and _clamp(ranked[-1].get("final_score", ranked[-1].get("score", 0.0))) < 0.55:
		improvement_candidates.append(f"Review {_question_label(ranked[-1])} again and practice answering it with more precise terminology.")
	if not improvement_candidates:
		improvement_candidates.append("Keep tightening your answers so the main point is stated earlier and more explicitly.")
	if len(improvement_candidates) < 2:
		improvement_candidates.append("Add one or two concrete technical terms or examples earlier so your answer sounds more specific.")

	if dim_avgs["final"] >= 0.75:
		coaching_note = (
			f"You showed a solid grasp of {round_focus}. To move from good to excellent, make your strongest answers a little more explicit about the exact concepts and trade-offs being tested."
		)
	elif dim_avgs["final"] >= 0.5:
		coaching_note = (
			f"You showed partial understanding across {round_focus}, but some answers stayed too broad. Focus on naming the concept directly, explaining how it works, and then giving a short example or consequence."
		)
	else:
		coaching_note = (
			f"This round showed early progress, but the answers need stronger alignment with {round_focus}. Slow down, identify the exact concept being asked, and build each answer around two or three concrete technical points."
		)

	if confidence_signal["hesitation_detected"]:
		if round_name == "hr":
			coaching_note += " One repeated gap was hesitation: commit to your examples and ownership statements earlier, and cut back on softening phrases like 'maybe' or 'I think'."
		else:
			coaching_note += " One repeated gap was hesitant delivery: state your chosen concept or approach earlier, and reduce hedging so the answer sounds more decisive."
	elif confidence_signal["confident_delivery"]:
		coaching_note += " Your delivery was mostly steady, which made the stronger answers sound more convincing."

	return RoundFeedback(
		round=round_name,
		average_score=dim_avgs["final"],
		question_count=len(evaluation_records),
		strengths=_dedupe_lines(strength_candidates, limit=4),
		improvement_areas=_dedupe_lines(improvement_candidates, limit=4),
		coaching_note=coaching_note,
		confidence_signal=confidence_signal,
		dimension_averages={
			"groq": dim_avgs["groq"],
			"sbert": dim_avgs["sbert"],
			"communication": dim_avgs["communication"],
		},
		feedback_mode="local_fallback",
		fallback_reason=fallback_reason,
	)


def _build_local_session_feedback(round_feedbacks: Sequence[RoundFeedback]) -> SessionFeedback:
	round_scores: dict[str, float] = {
		rf["round"]: rf["average_score"] for rf in round_feedbacks
	}
	overall_score = round(sum(round_scores.values()) / len(round_scores), 4) if round_scores else 0.0
	strengths = _dedupe_lines(
		[item for rf in round_feedbacks for item in rf.get("strengths", [])],
		limit=3,
	)
	critical_gaps = _dedupe_lines(
		[item for rf in round_feedbacks for item in rf.get("improvement_areas", [])],
		limit=3,
	)
	if not strengths:
		strengths = ["You completed the interview flow and produced enough signal for scoring."]
	if not critical_gaps:
		critical_gaps = ["Keep sharpening your answers so the key idea appears earlier and more explicitly."]
	priority_recommendations = _dedupe_lines(
		[
			"Practice answering one concept at a time using a short structure: definition, mechanism, example.",
			"Review the lowest-scoring technical topics and rehearse them aloud with the exact terminology.",
			"Build one small project or revision sheet around the weakest round so the concepts become concrete.",
		],
		limit=3,
	)
	return SessionFeedback(
		overall_score=overall_score,
		round_scores=round_scores,
		top_strengths=strengths,
		critical_gaps=critical_gaps,
		priority_recommendations=priority_recommendations,
		readiness_label=_readiness_from_score(overall_score),
	)


# ---------------------------------------------------------------------------
# Round-level feedback
# ---------------------------------------------------------------------------

_ROUND_FEEDBACK_SYSTEM_PROMPT = """\
You are a senior technical interview coach. Review the evaluation records for one interview round
and return a JSON coaching summary.

Return a JSON object only — no markdown, no commentary. Keys:
  "strengths"        : array of 2-4 short bullet strings (what the candidate did well)
  "improvement_areas": array of 2-4 short bullet strings (specific gaps to work on)
  "coaching_note"    : 2-3 sentence narrative addressed to the candidate

Rules:
- Be specific — reference actual questions or topics, not generic praise.
- Coaching note must be actionable and encouraging.
- If the confidence signal shows hesitation, explicitly call out that hesitant delivery and suggest how to reduce hedging.
- Do not mention scores or numbers in the coaching note.
- Return JSON only.
"""

_ROUND_FEEDBACK_USER_TEMPLATE = """\
Round type        : {round_type}
Average score     : {avg_score:.2f} / 1.0
{confidence_block}
Evaluation records:

{summary_block}
"""


def generate_round_feedback(
	round_name: str,
	evaluation_records: Sequence[Mapping[str, Any]],
	*,
	settings: GroqSettings | None = None,
) -> RoundFeedback:
	"""Generate coaching feedback for one completed interview round.

	Args:
		round_name: One of "hr", "technical", "project_discussion".
		evaluation_records: List of EvaluationResult dicts (or Supabase rows shaped similarly).
		settings: Groq settings; resolved from env when omitted.

	Returns:
		``RoundFeedback`` TypedDict. If Groq is unavailable or rate-limited,
		the generator falls back to a local coaching summary so the round can
		complete without disconnecting.
	"""
	resolved = settings or get_settings().groq

	dim_avgs = _aggregate_scores(evaluation_records)
	confidence_signal = summarize_confidence_signal(round_name, evaluation_records)
	summary_block = _build_round_summary_block(round_name, evaluation_records)
	confidence_block = _build_confidence_signal_block(confidence_signal)

	user_message = _ROUND_FEEDBACK_USER_TEMPLATE.format(
		round_type=round_name,
		avg_score=dim_avgs["final"],
		confidence_block=confidence_block,
		summary_block=summary_block,
	)

	error: FeedbackGeneratorError | None = None
	if resolved is not None:
		try:
			completion = create_chat_completion(
				settings=resolved,
				model=resolved.feedback_generator_model,
				temperature=0.5,
				messages=[
					{"role": "system", "content": _ROUND_FEEDBACK_SYSTEM_PROMPT},
					{"role": "user", "content": user_message},
				],
			)
		except GroqDependencyError as exc:
			error = FeedbackGeneratorUnavailableError(str(exc))
		except GroqRateLimitError as exc:
			error = FeedbackGeneratorQuotaError(f"Round feedback generation failed: {exc}")
		except GroqCompletionError as exc:
			error = FeedbackGeneratorLlmError(f"Round feedback generation failed: {exc}")
		else:
			raw = completion.choices[0].message.content
			obj = _extract_json_object(raw)
			return RoundFeedback(
				round=round_name,
				average_score=dim_avgs["final"],
				question_count=len(evaluation_records),
				strengths=_str_list(obj.get("strengths"), 4),
				improvement_areas=_str_list(obj.get("improvement_areas"), 4),
				coaching_note=str(obj.get("coaching_note") or "").strip(),
				confidence_signal=confidence_signal,
				dimension_averages={
					"groq": dim_avgs["groq"],
					"sbert": dim_avgs["sbert"],
					"communication": dim_avgs["communication"],
				},
				feedback_mode="groq",
				fallback_reason=None,
			)
	else:
		error = FeedbackGeneratorUnavailableError(
			"GROQ_API_KEY is not configured. Using local round feedback fallback."
		)

	if error is not None:
		_LOGGER.warning("Falling back to local round feedback generation: %s", error)
	return _build_local_round_feedback(
		round_name,
		evaluation_records,
		fallback_reason=_fallback_reason_code(error, groq_configured=resolved is not None),
	)


# ---------------------------------------------------------------------------
# Session-level (cross-round) feedback
# ---------------------------------------------------------------------------

_SESSION_FEEDBACK_SYSTEM_PROMPT = """\
You are a senior hiring manager summarising a candidate's full interview performance across
multiple rounds. Return a JSON coaching report.

Return a JSON object only — no markdown, no commentary. Keys:
  "top_strengths"             : array of 3 short bullet strings (overall standout strengths)
  "critical_gaps"             : array of 3 short bullet strings (most important areas to improve)
  "priority_recommendations"  : array of 3 actionable steps (resources, practice, projects)
  "readiness_label"           : one of: "strong" | "ready" | "developing" | "early"

Readiness label definitions:
  strong     — consistent high scores across all rounds (>= 0.80 overall)
  ready      — solid performance with minor gaps (0.65 – 0.80)
  developing — moderate performance; clear areas to improve (0.45 – 0.65)
  early      — significant gaps; needs foundational work (< 0.45)

Rules:
- Be specific and reference the actual rounds and topics, not generic advice.
- Recommendations must be concrete (e.g. "Build a REST API side project in Python").
- Return JSON only.
"""

_SESSION_FEEDBACK_USER_TEMPLATE = """\
Overall score : {overall_score:.2f} / 1.0
Round scores  :
{round_scores_block}

Round coaching notes:
{coaching_notes_block}
"""


def _readiness_from_score(score: float) -> str:
	if score >= 0.80:
		return "strong"
	if score >= 0.65:
		return "ready"
	if score >= 0.45:
		return "developing"
	return "early"


def generate_session_feedback(
	round_feedbacks: Sequence[RoundFeedback],
	*,
	settings: GroqSettings | None = None,
) -> SessionFeedback:
	"""Generate cross-round session feedback for the final report.

	Args:
		round_feedbacks: List of ``RoundFeedback`` dicts, one per completed round.
		settings: Groq settings; resolved from env when omitted.

	Returns:
		``SessionFeedback`` TypedDict. If Groq is unavailable or rate-limited,
		the generator falls back to a local summary.
	"""
	resolved = settings or get_settings().groq

	round_scores: dict[str, float] = {
		rf["round"]: rf["average_score"] for rf in round_feedbacks
	}
	overall_score = (
		sum(round_scores.values()) / len(round_scores) if round_scores else 0.0
	)
	overall_score = round(overall_score, 4)

	round_scores_block = "\n".join(
		f"  {rnd}: {score:.2f}" for rnd, score in round_scores.items()
	)
	coaching_notes_block = "\n".join(
		f"  {rf['round']}: {rf['coaching_note']}" for rf in round_feedbacks if rf["coaching_note"]
	)

	user_message = _SESSION_FEEDBACK_USER_TEMPLATE.format(
		overall_score=overall_score,
		round_scores_block=round_scores_block or "  (no round data)",
		coaching_notes_block=coaching_notes_block or "  (none)",
	)

	if resolved is not None:
		try:
			completion = create_chat_completion(
				settings=resolved,
				model=resolved.feedback_generator_model,
				temperature=0.4,
				messages=[
					{"role": "system", "content": _SESSION_FEEDBACK_SYSTEM_PROMPT},
					{"role": "user", "content": user_message},
				],
			)
		except (GroqDependencyError, GroqRateLimitError, GroqCompletionError) as exc:
			_LOGGER.warning("Falling back to local session feedback generation: %s", exc)
		else:
			raw = completion.choices[0].message.content
			obj = _extract_json_object(raw)

			raw_label = str(obj.get("readiness_label") or "").lower().strip()
			readiness = raw_label if raw_label in {"strong", "ready", "developing", "early"} else _readiness_from_score(overall_score)

			return SessionFeedback(
				overall_score=overall_score,
				round_scores=round_scores,
				top_strengths=_str_list(obj.get("top_strengths"), 4),
				critical_gaps=_str_list(obj.get("critical_gaps"), 4),
				priority_recommendations=_str_list(obj.get("priority_recommendations"), 4),
				readiness_label=readiness,
			)

	return _build_local_session_feedback(round_feedbacks)
