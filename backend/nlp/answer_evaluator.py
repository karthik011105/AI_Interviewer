"""Per-answer evaluation pipeline for HR, technical, and project rounds.

Scoring formula per answer:
	groq path     -> round-aware weighted blend of Groq rubric, SBERT similarity,
	                 concept coverage, and communication quality
	fallback path -> round-aware weighted blend of the fully local signals when
	                 Groq is unavailable

All scores are floats in [0.0, 1.0] unless noted.
Communication metrics are computed locally (no extra Groq call).
SBERT similarity is computed using the same model already loaded by role_matcher.
Groq rubric evaluation uses a single structured call per answer.
"""

from __future__ import annotations

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
from backend.nlp.linguistic_analyzer import analyze_answer as _analyze_answer
from backend.nlp.role_matcher import get_semantic_encoder

_LOGGER = logging.getLogger(__name__)

_SBERT_MODEL_NAME = "all-MiniLM-L6-v2"

# ---------------------------------------------------------------------------
# Public TypedDicts
# ---------------------------------------------------------------------------


class RubricScore(TypedDict):
	"""Structured output from Groq rubric evaluation."""

	score: float          # 0.0 – 1.0
	relevance: float      # 0.0 – 1.0  (did they answer the actual question?)
	depth: float          # 0.0 – 1.0  (technical/conceptual depth)
	accuracy: float       # 0.0 – 1.0  (factual correctness)
	feedback: str         # 1–2 sentence coaching note


class CommunicationScore(TypedDict):
	"""Local communication quality metrics — extended linguistic NLP dimensions."""

	score: float                  # 0.0 – 1.0  weighted aggregate
	word_count: int
	filler_ratio: float           # fraction of words that are fillers
	vocabulary_diversity: float   # unique tokens / total tokens (type-token ratio)
	# --- Extended linguistic fields (added by linguistic_analyzer) ---
	readability_ease: float       # Flesch Reading Ease 0–100
	readability_grade: float      # Flesch-Kincaid Grade Level
	difficulty_label: str         # Very Easy | Easy | Standard | Difficult | Very Difficult
	sentence_count: int
	avg_sentence_length: float    # mean words per sentence
	discourse_coherence: float    # 0–1; density of discourse connectives
	discourse_markers: int        # total discourse markers found
	hapax_ratio: float            # words appearing exactly once / total words
	sliding_ttr: float            # sliding-window type-token ratio
	hedging_ratio: float          # uncertainty / hedging language density
	technical_density: float      # content-word ratio (domain specificity signal)
	technical_noun_phrases: list[str]
	matched_expected_terms: list[str]
	missing_expected_terms: list[str]
	technical_term_coverage: float
	technical_phrase_extraction_mode: str
	sentiment_available: bool
	sentiment_analysis_mode: str
	sentiment_compound: float
	sentiment_positive_ratio: float
	sentiment_neutral_ratio: float
	sentiment_negative_ratio: float
	confidence_score: float
	confidence_label: str
	sentiment_label: str


class ConceptCoverageDetail(TypedDict):
	"""One ideal answer concept and whether the answer covered it."""

	point: str
	covered: bool
	similarity: float


class ConceptCoverageResult(TypedDict):
	"""Coverage of ideal-answer concepts detected via TF-IDF similarity."""

	coverage_ratio: float
	covered_count: int
	total_concepts: int
	tfidf_score: float
	concept_details: list[ConceptCoverageDetail]
	threshold: float
	available: bool


class ScoreWeights(TypedDict):
	"""Primary weighted blend for the full evaluator path."""

	groq: float
	sbert: float
	concept_coverage: float
	communication: float


class LocalFallbackWeights(TypedDict):
	"""Weighted blend for the local evaluator fallback path."""

	sbert: float
	concept_coverage: float
	communication: float


class ScoringProfile(TypedDict):
	"""Round-specific scoring configuration used during evaluation."""

	round_type: str
	coverage_threshold: float
	weights: ScoreWeights
	local_fallback_weights: LocalFallbackWeights


class EvaluationResult(TypedDict):
	"""Full evaluation output for one candidate answer."""

	final_score: float       # 0.0 – 1.0
	groq_score: float
	sbert_score: float
	communication_score: float
	concept_coverage: ConceptCoverageResult
	scoring_profile: ScoringProfile
	rubric: RubricScore
	communication: CommunicationScore
	sbert_similarity: float
	feedback: str            # copy of rubric.feedback for convenience
	evaluation_mode: str     # groq | local_fallback
	fallback_reason: str | None
	local_score: float


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


_SCORING_PROFILES: dict[str, ScoringProfile] = {
	"technical": ScoringProfile(
		round_type="technical",
		coverage_threshold=0.12,
		weights=ScoreWeights(
			groq=0.62,
			sbert=0.18,
			concept_coverage=0.14,
			communication=0.06,
		),
		local_fallback_weights=LocalFallbackWeights(
			sbert=0.48,
			concept_coverage=0.37,
			communication=0.15,
		),
	),
	"project_discussion": ScoringProfile(
		round_type="project_discussion",
		coverage_threshold=0.11,
		weights=ScoreWeights(
			groq=0.62,
			sbert=0.17,
			concept_coverage=0.12,
			communication=0.09,
		),
		local_fallback_weights=LocalFallbackWeights(
			sbert=0.45,
			concept_coverage=0.35,
			communication=0.20,
		),
	),
	"hr": ScoringProfile(
		round_type="hr",
		coverage_threshold=0.08,
		weights=ScoreWeights(
			groq=0.68,
			sbert=0.14,
			concept_coverage=0.06,
			communication=0.12,
		),
		local_fallback_weights=LocalFallbackWeights(
			sbert=0.38,
			concept_coverage=0.22,
			communication=0.40,
		),
	),
}


def _resolve_scoring_profile(round_type: str) -> ScoringProfile:
	normalized_round = str(round_type or "").strip().lower()
	profile = _SCORING_PROFILES.get(normalized_round, _SCORING_PROFILES["technical"])
	weights = profile["weights"]
	local_weights = profile["local_fallback_weights"]
	return ScoringProfile(
		round_type=str(profile["round_type"]),
		coverage_threshold=float(profile["coverage_threshold"]),
		weights=ScoreWeights(
			groq=float(weights["groq"]),
			sbert=float(weights["sbert"]),
			concept_coverage=float(weights["concept_coverage"]),
			communication=float(weights["communication"]),
		),
		local_fallback_weights=LocalFallbackWeights(
			sbert=float(local_weights["sbert"]),
			concept_coverage=float(local_weights["concept_coverage"]),
			communication=float(local_weights["communication"]),
		),
	)


class EvaluatorError(RuntimeError):
	"""Base error for answer evaluation failures."""


class EvaluatorLlmError(EvaluatorError):
	"""Raised when Groq returns an invalid or unparseable rubric response."""


class EvaluatorQuotaError(EvaluatorLlmError):
	"""Raised when Groq evaluation cannot run because quota or rate limits were exceeded."""


class EvaluatorUnavailableError(EvaluatorError):
	"""Raised when Groq is not configured."""


# ---------------------------------------------------------------------------
# Communication metrics (fully local, no API call)
# ---------------------------------------------------------------------------

def _compute_communication_score(
	answer_text: str,
	*,
	expected_terms: Sequence[str] | None = None,
) -> CommunicationScore:
	"""
	Compute rich linguistic NLP metrics from a candidate answer transcript.

	Delegates to ``linguistic_analyzer.analyze_answer`` which provides six
	independent NLP dimensions: readability, sentence complexity, discourse
	coherence, lexical diversity, hedging language, technical density, and
	technical noun phrase coverage against expected terms.
	The result is mapped back to the ``CommunicationScore`` TypedDict shape
	for backward compatibility with the existing evaluation pipeline.
	"""
	analysis = _analyze_answer(answer_text, expected_terms=expected_terms)
	return CommunicationScore(
		score=round(analysis.linguistic_score, 4),
		word_count=analysis.word_count,
		filler_ratio=round(analysis.filler_ratio, 4),
		vocabulary_diversity=round(analysis.vocabulary_diversity, 4),
		# --- Extended linguistic fields ---
		readability_ease=round(analysis.readability.reading_ease, 2),
		readability_grade=round(analysis.readability.grade_level, 2),
		difficulty_label=analysis.readability.difficulty_label,
		sentence_count=analysis.sentences.sentence_count,
		avg_sentence_length=round(analysis.sentences.avg_words, 2),
		discourse_coherence=round(analysis.discourse.coherence_score, 4),
		discourse_markers=analysis.discourse.total_markers,
		hapax_ratio=round(analysis.lexical.hapax_ratio, 4),
		sliding_ttr=round(analysis.lexical.sliding_ttr, 4),
		hedging_ratio=round(analysis.hedging_ratio, 4),
		technical_density=round(analysis.technical_density, 4),
		technical_noun_phrases=list(analysis.technical_phrases.extracted_phrases),
		matched_expected_terms=list(analysis.technical_phrases.matched_expected_terms),
		missing_expected_terms=list(analysis.technical_phrases.missing_expected_terms),
		technical_term_coverage=round(analysis.technical_phrases.coverage_ratio, 4),
		technical_phrase_extraction_mode=analysis.technical_phrases.extraction_mode,
		sentiment_available=bool(analysis.sentiment_confidence.available),
		sentiment_analysis_mode=analysis.sentiment_confidence.analysis_mode,
		sentiment_compound=round(analysis.sentiment_confidence.compound, 4),
		sentiment_positive_ratio=round(analysis.sentiment_confidence.positive_ratio, 4),
		sentiment_neutral_ratio=round(analysis.sentiment_confidence.neutral_ratio, 4),
		sentiment_negative_ratio=round(analysis.sentiment_confidence.negative_ratio, 4),
		confidence_score=round(analysis.sentiment_confidence.confidence_score, 4),
		confidence_label=analysis.sentiment_confidence.confidence_label,
		sentiment_label=analysis.sentiment_confidence.sentiment_label,
	)


def _clamp_score(value: Any) -> float:
	try:
		return max(0.0, min(1.0, float(value)))
	except (TypeError, ValueError):
		return 0.0


def _compute_local_fallback_score(
	*,
	sbert_score: float,
	coverage_score: float,
	communication_score: float,
	weights: LocalFallbackWeights,
	sbert_available: bool,
	coverage_available: bool,
) -> float:
	available_weight = 0.0
	total_score = 0.0
	if sbert_available:
		available_weight += weights["sbert"]
		total_score += sbert_score * weights["sbert"]
	if coverage_available:
		available_weight += weights["concept_coverage"]
		total_score += coverage_score * weights["concept_coverage"]
	available_weight += weights["communication"]
	total_score += communication_score * weights["communication"]
	if available_weight <= 0.0:
		return 0.0
	score = total_score / available_weight
	return round(_clamp_score(score), 4)


def _empty_concept_coverage(*, available: bool, threshold: float) -> ConceptCoverageResult:
	return ConceptCoverageResult(
		coverage_ratio=0.0,
		covered_count=0,
		total_concepts=0,
		tfidf_score=0.0,
		concept_details=[],
		threshold=round(_clamp_score(threshold), 4),
		available=available,
	)


def _normalize_concept_text(text: str) -> str:
	"""Normalize short technical phrases before lexical similarity scoring."""
	normalized = re.sub(r"([a-zA-Z])\(([^)]+)\)", r"\1 \2 ", str(text or ""))
	normalized = re.sub(r"[^a-zA-Z0-9+#]+", " ", normalized.casefold())
	tokens: list[str] = []
	for token in normalized.split():
		if len(token) > 4 and token.endswith("ies"):
			token = f"{token[:-3]}y"
		elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
			token = token[:-1]
		tokens.append(token)
	return " ".join(tokens)


def _ideal_points_focus(ideal_points: Sequence[str], *, limit: int = 2) -> str:
	points = [str(point).strip().rstrip(".") for point in ideal_points if str(point).strip()]
	if not points:
		return "the key technical concepts asked in the question"
	return ", ".join(points[:limit])


def _build_strength_feedback_text(
	*,
	ideal_points: Sequence[str],
	sbert_score: float,
	communication: CommunicationScore,
	coverage: ConceptCoverageResult,
) -> str:
	focus = _ideal_points_focus(ideal_points)
	coverage_ratio = float(coverage.get("coverage_ratio") or 0.0)
	parts: list[str] = []

	if coverage_ratio >= 0.8:
		parts.append("Strong answer. You covered the main technical points directly and stayed on the actual question.")
	elif sbert_score >= 0.78:
		parts.append("Strong answer. You stayed close to the expected technical points and explained them clearly.")
	else:
		parts.append(f"Good answer. You addressed the key ideas around {focus} with solid relevance.")

	if communication["score"] >= 0.68:
		parts.append("The explanation was clear, structured, and interview-ready.")
	elif float(communication.get("confidence_score") or 0.0) >= 0.65:
		parts.append("Your delivery sounded steady and confident.")

	return " ".join(parts[:2]).strip()


def _looks_like_generic_coaching(text: str) -> bool:
	normalized = str(text or "").strip().casefold()
	if not normalized:
		return False
	return bool(
		re.search(
			r"\b(consider|improve|strengthen|focus|mention|add|include|clarify|explain more|cover|state more clearly|use a clearer structure)\b",
			normalized,
		)
	)


def _build_local_feedback_text(
	*,
	ideal_points: Sequence[str],
	sbert_score: float,
	communication: CommunicationScore,
	coverage: ConceptCoverageResult,
) -> str:
	focus = _ideal_points_focus(ideal_points)
	coverage_ratio = float(coverage.get("coverage_ratio") or 0.0)
	strong_content = sbert_score >= 0.72 or coverage_ratio >= 0.75
	if strong_content:
		return _build_strength_feedback_text(
			ideal_points=ideal_points,
			sbert_score=sbert_score,
			communication=communication,
			coverage=coverage,
		)

	parts: list[str] = []

	if sbert_score < 0.3 and coverage_ratio < 0.35:
		parts.append(
			f"The answer did not stay close enough to the expected technical points. Focus more directly on {focus}."
		)
	elif sbert_score < 0.55 and coverage_ratio < 0.55:
		parts.append(
			f"The answer touched some relevant ideas but missed important technical details. Strengthen it by explicitly covering {focus}."
		)
	else:
		parts.append(
			f"The answer covered several relevant ideas. Make it stronger by naming {focus} more explicitly and earlier in the response."
		)

	disc = communication.get("discourse_coherence", 0.0)
	if communication["word_count"] < 25:
		parts.append("Add a little more detail so the explanation feels complete.")
	elif disc < 0.25:
		parts.append(
			"Use connective language (e.g. 'because', 'therefore', 'for example') to "
			"show the reasoning between your points rather than just listing them."
		)
	elif communication["score"] < 0.55:
		parts.append("Use a clearer structure: define the concept, explain how it works, then give a short example.")
	else:
		parts.append("Keep the explanation structured and call out the main technical terms earlier.")

	return " ".join(parts[:2]).strip()


def _build_local_rubric(
	*,
	ideal_points: Sequence[str],
	sbert_score: float,
	communication: CommunicationScore,
	coverage: ConceptCoverageResult,
	local_score: float,
) -> RubricScore:
	relevance = _clamp_score(sbert_score * 1.1)
	# Richer depth estimate using discourse coherence and vocabulary diversity
	disc = communication.get("discourse_coherence", 0.0)
	depth = _clamp_score(
		sbert_score * 0.60
		+ min(1.0, communication["word_count"] / 120) * 0.10
		+ communication["vocabulary_diversity"] * 0.15
		+ disc * 0.15
	)
	accuracy = _clamp_score(sbert_score)
	feedback = _build_local_feedback_text(
		ideal_points=ideal_points,
		sbert_score=sbert_score,
		communication=communication,
		coverage=coverage,
	)
	return RubricScore(
		score=local_score,
		relevance=round(relevance, 4),
		depth=round(depth, 4),
		accuracy=round(accuracy, 4),
		feedback=feedback,
	)


def _fallback_reason_from_error(exc: EvaluatorError | None, *, groq_configured: bool) -> str | None:
	if exc is None:
		return None
	if isinstance(exc, EvaluatorQuotaError):
		return "groq_rate_limit"
	if isinstance(exc, EvaluatorUnavailableError):
		return "groq_unavailable" if groq_configured else "groq_not_configured"
	return "groq_error"


# ---------------------------------------------------------------------------
# SBERT similarity (reuses the model from role_matcher)
# ---------------------------------------------------------------------------


def _load_sbert_model() -> Any:
	encoder = get_semantic_encoder()
	if encoder is None:
		raise EvaluatorUnavailableError(
			"The shared semantic encoder is unavailable. "
			"Install `sentence-transformers` and warm up the process-wide SBERT model."
		)
	return encoder


def _cosine_similarity(vec_a: Any, vec_b: Any) -> float:
	"""Compute cosine similarity between two numpy-like vectors."""
	try:
		import numpy as np  # type: ignore[import]
		a = np.array(vec_a, dtype=float)
		b = np.array(vec_b, dtype=float)
		norm_a = np.linalg.norm(a)
		norm_b = np.linalg.norm(b)
		if norm_a == 0.0 or norm_b == 0.0:
			return 0.0
		return float(np.dot(a, b) / (norm_a * norm_b))
	except Exception:
		return 0.0


def compute_sbert_similarity(
	answer_text: str,
	ideal_points: Sequence[str],
	*,
	model: Any = None,
) -> float:
	"""Return the mean cosine similarity between the answer and ideal answer points.

	Args:
		answer_text: Candidate's transcribed answer.
		ideal_points: List of ideal answer bullet strings from the question bank.
		model: Optional pre-loaded SentenceTransformer model (avoids repeated loads).

	Returns:
		Float in [0.0, 1.0].
	"""
	if not answer_text.strip() or not ideal_points:
		return 0.0

	sbert = model or _load_sbert_model()
	try:
		all_texts = [answer_text] + [str(p) for p in ideal_points]
		embeddings = sbert.encode(all_texts, convert_to_numpy=True)
		answer_emb = embeddings[0]
		ideal_embs = embeddings[1:]

		similarities = [_cosine_similarity(answer_emb, emb) for emb in ideal_embs]
		mean_sim = sum(similarities) / len(similarities)
		# clip to [0, 1]
		return max(0.0, min(1.0, float(mean_sim)))
	except Exception as exc:
		_LOGGER.warning("SBERT similarity computation failed: %s", exc)
		return 0.0


def compute_concept_coverage(
	answer_text: str,
	ideal_points: Sequence[str],
	*,
	threshold: float | None = None,
) -> ConceptCoverageResult:
	"""Measure which ideal-answer concepts are surfaced in the response.

	Uses TF-IDF cosine similarity as a lightweight classical IR signal. This is
	intentionally independent from SBERT so the evaluator can combine neural and
	lexical evidence when scoring one answer.
	"""
	clean_answer = _normalize_concept_text(answer_text)
	clean_points = [str(point).strip() for point in ideal_points if str(point).strip()]
	normalized_points = [_normalize_concept_text(point) for point in clean_points]
	coverage_threshold = _clamp_score(
		_resolve_scoring_profile("technical")["coverage_threshold"] if threshold is None else threshold
	)
	if not clean_answer or not clean_points:
		return _empty_concept_coverage(available=True, threshold=coverage_threshold)

	try:
		from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import]
		from sklearn.metrics.pairwise import cosine_similarity  # type: ignore[import]
	except Exception:
		return _empty_concept_coverage(available=False, threshold=coverage_threshold)

	try:
		corpus = normalized_points + [clean_answer]
		matrix = TfidfVectorizer(
			ngram_range=(1, 2),
			stop_words="english",
			min_df=1,
			token_pattern=r"(?u)\b[a-zA-Z0-9+#]+\b",
		).fit_transform(corpus)
		answer_vector = matrix[-1]
		concept_details: list[ConceptCoverageDetail] = []
		total_similarity = 0.0
		for index, point in enumerate(clean_points):
			similarity = float(cosine_similarity(matrix[index], answer_vector)[0][0])
			clamped_similarity = round(_clamp_score(similarity), 4)
			concept_details.append(
				ConceptCoverageDetail(
					point=point,
					covered=clamped_similarity >= coverage_threshold,
					similarity=clamped_similarity,
				)
			)
			total_similarity += clamped_similarity
	except Exception as exc:
		_LOGGER.warning("Concept coverage computation failed: %s", exc)
		return _empty_concept_coverage(available=False, threshold=coverage_threshold)

	covered_count = sum(1 for detail in concept_details if detail["covered"])
	total_concepts = len(concept_details)
	return ConceptCoverageResult(
		coverage_ratio=round(covered_count / total_concepts, 4),
		covered_count=covered_count,
		total_concepts=total_concepts,
		tfidf_score=round(total_similarity / total_concepts, 4),
		concept_details=concept_details,
		threshold=round(coverage_threshold, 4),
		available=True,
	)


# ---------------------------------------------------------------------------
# Groq rubric evaluation
# ---------------------------------------------------------------------------

_RUBRIC_SYSTEM_PROMPT = """\
You are a senior technical interview evaluator. Score the candidate's answer using the rubric below.

Return a JSON object only — no markdown, no commentary. The object must have exactly these keys:
  "score"     : float 0.0 to 1.0 — overall answer quality
  "relevance" : float 0.0 to 1.0 — how directly the answer addressed the question
  "depth"     : float 0.0 to 1.0 — technical or conceptual depth demonstrated
  "accuracy"  : float 0.0 to 1.0 — factual and technical correctness
	"feedback"  : string — 1-2 sentence candidate-facing summary; for strong answers, summarize what worked well and avoid generic coaching, and only include improvement guidance when there is a specific meaningful gap

Scoring guidelines:
  1.0  = Excellent, complete, no meaningful gaps
  0.75 = Good with minor gaps or imprecision
  0.5  = Partial — key ideas present but incomplete
  0.25 = Weak — tangentially related or mostly incorrect
  0.0  = Off-topic, blank, or entirely wrong

Feedback rule:
	If the answer is already strong, do not pad the response with generic advice like "consider mentioning" or "improve by". Use strengths-first language unless a concrete gap materially affects the score.
"""

_RUBRIC_USER_TEMPLATE = """\
Round type    : {round_type}
Question      : {question}
Ideal answer  : {ideal_points}
Follow-up used: {follow_up}
Candidate answer (transcript): {answer}
"""


def _parse_rubric_response(raw: str) -> RubricScore:
	"""Extract and validate a RubricScore from raw Groq output."""
	import json
	text = raw.strip()

	# Strip markdown fences if present
	fence = re.search(r"```(?:json)?\s*(?P<body>\{.*?\})\s*```", text, re.DOTALL)
	if fence:
		text = fence.group("body")

	start = text.find("{")
	end = text.rfind("}")
	if start == -1 or end == -1:
		raise EvaluatorLlmError(f"Rubric response contained no JSON object: {raw[:200]}")

	try:
		obj = json.loads(text[start : end + 1])
	except json.JSONDecodeError as exc:
		raise EvaluatorLlmError(f"Rubric JSON was malformed: {exc}") from exc

	def _clamp(val: Any) -> float:
		try:
			return max(0.0, min(1.0, float(val)))
		except (TypeError, ValueError):
			return 0.0

	return RubricScore(
		score=_clamp(obj.get("score")),
		relevance=_clamp(obj.get("relevance")),
		depth=_clamp(obj.get("depth")),
		accuracy=_clamp(obj.get("accuracy")),
		feedback=str(obj.get("feedback") or "").strip(),
	)


def evaluate_with_groq(
	*,
	question: str,
	ideal_points: Sequence[str],
	follow_up: str,
	answer_text: str,
	round_type: str,
	settings: GroqSettings,
) -> RubricScore:
	"""Run the Groq rubric evaluation for one candidate answer.

	Args:
		question: The question that was asked.
		ideal_points: Ideal answer bullet points from the question bank.
		follow_up: The follow-up question text (may be empty if not asked).
		answer_text: Candidate's transcribed answer.
		round_type: One of "hr", "technical", or "project_discussion".
		settings: Groq settings.

	Returns:
		``RubricScore`` TypedDict.

	Raises:
		EvaluatorLlmError: Groq response was unparseable.
		EvaluatorUnavailableError: Groq SDK not available.
	"""
	ideal_str = "\n".join(f"- {p}" for p in ideal_points) if ideal_points else "not specified"
	user_message = _RUBRIC_USER_TEMPLATE.format(
		round_type=round_type,
		question=question,
		ideal_points=ideal_str,
		follow_up=follow_up or "not asked",
		answer=answer_text or "(no answer provided)",
	)

	try:
		completion = create_chat_completion(
			settings=settings,
			model=settings.answer_evaluator_model,
			temperature=0.0,
			messages=[
				{"role": "system", "content": _RUBRIC_SYSTEM_PROMPT},
				{"role": "user", "content": user_message},
			],
		)
	except GroqDependencyError as exc:
		raise EvaluatorUnavailableError(str(exc)) from exc
	except GroqRateLimitError as exc:
		raise EvaluatorQuotaError(f"Groq rubric evaluation failed: {exc}") from exc
	except GroqCompletionError as exc:
		raise EvaluatorLlmError(f"Groq rubric evaluation failed: {exc}") from exc

	raw_content = completion.choices[0].message.content
	return _parse_rubric_response(raw_content)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_answer(
	*,
	question: str,
	ideal_points: Sequence[str],
	follow_up: str,
	answer_text: str,
	round_type: str,
	settings: GroqSettings | None = None,
	sbert_model: Any = None,
) -> EvaluationResult:
	"""Evaluate one candidate answer using the full pipeline.

	Pipeline:
		1. Local communication metrics (no API call).
		2. SBERT cosine similarity vs ideal answer points.
		3. Groq rubric evaluation.
		4. Weighted aggregation → final_score.

	Args:
		question: The interview question text.
		ideal_points: Ideal answer bullet strings from the question bank.
		follow_up: Follow-up question text (empty string if not asked).
		answer_text: Candidate's transcribed or typed answer.
		round_type: One of "hr", "technical", "project_discussion".
		settings: Groq settings; resolved from env when omitted.
		sbert_model: Optional pre-loaded SentenceTransformer (avoids repeated loads).

	Returns:
		``EvaluationResult`` TypedDict. If Groq is unavailable or rate-limited,
		the evaluator falls back to a fully local score using SBERT similarity and
		communication metrics so the interview round can continue.
	"""
	resolved = settings or get_settings().groq
	scoring_profile = _resolve_scoring_profile(round_type)

	# 1. Communication metrics (local, always runs)
	comm = _compute_communication_score(answer_text, expected_terms=ideal_points)

	# 2. SBERT similarity (local, always runs)
	sbert_available = True
	try:
		sbert_sim = compute_sbert_similarity(answer_text, ideal_points, model=sbert_model)
	except EvaluatorError as exc:
		_LOGGER.warning("SBERT similarity unavailable during answer evaluation: %s", exc)
		sbert_available = False
		sbert_sim = 0.0

	coverage = compute_concept_coverage(
		answer_text,
		ideal_points,
		threshold=scoring_profile["coverage_threshold"],
	)
	coverage_score = coverage["tfidf_score"]

	local_score = _compute_local_fallback_score(
		sbert_score=sbert_sim,
		coverage_score=coverage_score,
		communication_score=comm["score"],
		weights=scoring_profile["local_fallback_weights"],
		sbert_available=sbert_available,
		coverage_available=coverage["available"],
	)

	rubric_error: EvaluatorError | None = None
	evaluation_mode = "groq"
	if resolved is not None:
		try:
			rubric = evaluate_with_groq(
				question=question,
				ideal_points=ideal_points,
				follow_up=follow_up,
				answer_text=answer_text,
				round_type=round_type,
				settings=resolved,
			)
		except (EvaluatorUnavailableError, EvaluatorQuotaError, EvaluatorLlmError) as exc:
			rubric_error = exc
			_LOGGER.warning("Falling back to local answer evaluation: %s", exc)
			rubric = _build_local_rubric(
				ideal_points=ideal_points,
				sbert_score=sbert_sim,
				communication=comm,
				coverage=coverage,
				local_score=local_score,
			)
			evaluation_mode = "local_fallback"
	else:
		rubric_error = EvaluatorUnavailableError(
			"GROQ_API_KEY is not configured. Using local fallback evaluation."
		)
		rubric = _build_local_rubric(
			ideal_points=ideal_points,
			sbert_score=sbert_sim,
			communication=comm,
			coverage=coverage,
			local_score=local_score,
		)
		evaluation_mode = "local_fallback"

	if evaluation_mode == "groq":
		weights = scoring_profile["weights"]
		groq_score = rubric["score"]
		final_score = (
			groq_score * weights["groq"]
			+ sbert_sim * weights["sbert"]
			+ coverage_score * weights["concept_coverage"]
			+ comm["score"] * weights["communication"]
		)
		final_score = round(_clamp_score(final_score), 4)
	else:
		groq_score = 0.0
		final_score = local_score

	high_quality_answer = (
		float(rubric.get("score") or 0.0) >= 0.78
		and (sbert_sim >= 0.72 or float(coverage.get("coverage_ratio") or 0.0) >= 0.6)
	)
	if high_quality_answer and _looks_like_generic_coaching(rubric.get("feedback", "")):
		rubric["feedback"] = _build_strength_feedback_text(
			ideal_points=ideal_points,
			sbert_score=sbert_sim,
			communication=comm,
			coverage=coverage,
		)

	fallback_reason = _fallback_reason_from_error(
		rubric_error,
		groq_configured=resolved is not None,
	)

	return EvaluationResult(
		final_score=final_score,
		groq_score=round(groq_score, 4),
		sbert_score=round(sbert_sim, 4),
		communication_score=round(comm["score"], 4),
		concept_coverage=coverage,
		scoring_profile=scoring_profile,
		rubric=rubric,
		communication=comm,
		sbert_similarity=round(sbert_sim, 4),
		feedback=rubric["feedback"],
		evaluation_mode=evaluation_mode,
		fallback_reason=fallback_reason,
		local_score=local_score,
	)
