"""Linguistic analysis for interview answers with graceful optional NLP enrichments.

Six core NLP dimensions applied per answer:

  1. Readability        — Flesch-Kincaid Reading Ease and Grade Level
  2. Sentence Complexity — distribution and length of sentences
  3. Discourse Coherence — density of connective discourse markers
  4. Lexical Diversity   — type-token ratio, hapax richness, sliding-window TTR
  5. Hedging Language    — uncertainty vs confidence signal
  6. Technical Density   — content-word ratio as a proxy for domain specificity

Optional technical phrase matching:

	7. Technical Noun Phrases — spaCy noun chunks or regex fallback matched
															 against expected technical terms / ideal points

Core processing is pure Python (re + math only).
Optional: if spaCy with ``en_core_web_sm`` is available the module enriches
technical noun phrase analysis. Optional: if ``vaderSentiment`` is available
the module adds transcript-level sentiment scoring. The module always succeeds
without either dependency.

Public API
----------
analyze_answer(text, expected_terms=None) -> LinguisticAnalysis
    Main entry point.  Returns all six dimension results plus an aggregate
	``linguistic_score`` in [0.0, 1.0] suitable for the answer evaluator,
	along with technical noun phrase extraction and expected-term coverage.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from importlib import import_module
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tokenisation helpers
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-zA-Z']+")
_PHRASE_TOKEN_RE = re.compile(r"[a-zA-Z0-9+#']+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"\'(])")
_VOWELS = frozenset("aeiouy")


def _tokenize_words(text: str) -> list[str]:
	"""Extract lowercase word tokens, stripping punctuation."""
	return [m.group().lower().rstrip("'s") for m in _WORD_RE.finditer(text) if len(m.group()) > 0]


def _split_sentences(text: str) -> list[str]:
	"""Split text into sentences using punctuation heuristics."""
	raw = _SENTENCE_SPLIT_RE.split(text.strip())
	return [s.strip() for s in raw if s.strip()]


# ---------------------------------------------------------------------------
# Syllable counting (English approximation)
# ---------------------------------------------------------------------------

def _count_syllables(word: str) -> int:
	"""Estimate syllable count for an English word using vowel-group rules."""
	word = word.lower().strip(".,!?;:'\"()-")
	if not word:
		return 0

	count = 0
	prev_vowel = False
	for char in word:
		is_vowel = char in _VOWELS
		if is_vowel and not prev_vowel:
			count += 1
		prev_vowel = is_vowel

	# Silent 'e' at end reduces count
	if word.endswith("e") and count > 1 and len(word) > 2:
		count -= 1
	# Common '-le' ending (e.g. "simple", "table") adds a syllable
	if word.endswith("le") and len(word) > 2 and word[-3] not in _VOWELS:
		count += 1
	# '-es' and '-ed' endings on words longer than 4 chars
	if len(word) > 4 and word.endswith(("es", "ed")) and count > 1:
		count -= 1

	return max(1, count)


# ---------------------------------------------------------------------------
# Discourse markers
# ---------------------------------------------------------------------------

_DISCOURSE_MARKERS: dict[str, list[str]] = {
	"causal": [
		"because", "therefore", "thus", "hence", "consequently",
		"as a result", "due to", "since", "so that", "in order to",
		"this means", "which means",
	],
	"contrastive": [
		"however", "although", "despite", "whereas", "while",
		"on the other hand", "nevertheless", "yet", "even though",
		"in contrast", "alternatively", "unlike", "instead",
	],
	"additive": [
		"additionally", "moreover", "furthermore", "also", "besides",
		"in addition", "as well", "along with", "not only", "together with",
	],
	"sequential": [
		"first", "firstly", "second", "secondly", "then", "next",
		"finally", "lastly", "subsequently", "after that", "to begin",
		"in conclusion", "to summarize", "overall",
	],
	"exemplification": [
		"for example", "for instance", "such as", "namely",
		"specifically", "in particular", "to illustrate", "like",
	],
}

# Pre-compile multi-word marker patterns for efficiency
_MARKER_PATTERNS: dict[str, list[re.Pattern[str]]] = {
	category: [
		re.compile(r"\b" + re.escape(m) + r"\b", re.IGNORECASE)
		for m in markers
	]
	for category, markers in _DISCOURSE_MARKERS.items()
}

# ---------------------------------------------------------------------------
# Filler words
# ---------------------------------------------------------------------------

_FILLER_WORDS = frozenset({
	"um", "uh", "like", "basically", "actually", "literally",
	"you know", "i mean", "sort of", "kind of", "right", "okay",
	"so", "well", "anyway", "honestly", "truthfully",
})

_FILLER_PATTERN = re.compile(
	r"\b(" + "|".join(re.escape(f) for f in _FILLER_WORDS) + r")\b",
	re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Hedging language
# ---------------------------------------------------------------------------

_HEDGING_TERMS: list[str] = [
	"might", "could", "maybe", "perhaps", "possibly", "probably",
	"i think", "i believe", "i'm not sure", "not sure", "i guess",
	"sort of", "kind of", "somewhat", "roughly", "approximately",
	"generally", "usually", "often", "i feel like",
]

_HEDGING_PATTERNS: list[re.Pattern[str]] = [
	re.compile(r"\b" + re.escape(h) + r"\b", re.IGNORECASE)
	for h in _HEDGING_TERMS
]

# ---------------------------------------------------------------------------
# Function words (for technical density estimation)
# ---------------------------------------------------------------------------

_FUNCTION_WORDS = frozenset({
	"the", "a", "an", "is", "are", "was", "were", "be", "been",
	"being", "have", "has", "had", "do", "does", "did", "will",
	"would", "could", "should", "may", "might", "shall", "can",
	"to", "of", "in", "on", "at", "by", "for", "with", "about",
	"against", "between", "into", "through", "during", "before",
	"after", "above", "below", "from", "up", "down", "out", "off",
	"i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
	"us", "them", "my", "your", "his", "its", "our", "their",
	"this", "that", "these", "those", "what", "which", "who", "how",
	"when", "where", "why", "all", "each", "every", "both", "few",
	"more", "most", "other", "some", "such", "no", "not", "only",
	"same", "than", "then", "too", "very", "just", "as", "if",
	"or", "and", "but", "so", "yet", "nor", "also", "get", "got",
	"let", "make", "go", "want", "need", "know", "think", "see",
	"come", "say", "said", "use", "used", "using",
})

_TECHNICAL_ANCHOR_TERMS = frozenset({
	"algorithm", "array", "api", "authentication", "backend", "binary",
	"bucket", "cache", "class", "collision", "compiler", "complexity",
	"constructor", "database", "dependency", "dispatch", "embedding",
	"graph", "hash", "heap", "index", "inference", "latency", "linked",
	"list", "lookup", "map", "memory", "microservice", "model", "node",
	"object", "pipeline", "pointer", "queue", "query", "recursion",
	"service", "sort", "stack", "table", "thread", "token", "tree",
	"vector", "workflow",
})

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ReadabilityMetrics:
	"""Flesch-Kincaid readability metrics."""

	reading_ease: float      # 0–100; higher = easier to read
	grade_level: float       # US grade level (FK grade)
	avg_syllables_per_word: float
	avg_words_per_sentence: float
	difficulty_label: str    # Very Easy | Easy | Standard | Difficult | Very Difficult


@dataclass
class SentenceMetrics:
	"""Sentence length distribution stats."""

	sentence_count: int
	avg_words: float
	std_words: float
	short_sentence_ratio: float  # sentences < 8 words
	long_sentence_ratio: float   # sentences > 35 words
	complexity_score: float      # 0–1; higher = more varied/complex structure


@dataclass
class DiscourseMetrics:
	"""Discourse marker and coherence analysis."""

	total_markers: int
	markers_per_sentence: float
	causal_count: int
	contrastive_count: int
	additive_count: int
	sequential_count: int
	exemplification_count: int
	coherence_score: float       # 0–1


@dataclass
class LexicalMetrics:
	"""Vocabulary richness and diversity metrics."""

	word_count: int
	unique_word_count: int
	type_token_ratio: float      # unique / total
	hapax_ratio: float           # words appearing exactly once / total
	sliding_ttr: float           # mean TTR over 50-word windows
	lexical_diversity_score: float  # 0–1 normalized aggregate


@dataclass
class TechnicalPhraseMetrics:
	"""Technical noun phrase extraction and expected-term matching."""

	extraction_mode: str
	extracted_phrases: list[str] = field(default_factory=list)
	expected_terms: list[str] = field(default_factory=list)
	matched_expected_terms: list[str] = field(default_factory=list)
	missing_expected_terms: list[str] = field(default_factory=list)
	coverage_ratio: float = 0.0


@dataclass
class SentimentConfidenceMetrics:
	"""Transcript sentiment and derived interview-confidence signal."""

	available: bool
	analysis_mode: str
	compound: float = 0.0
	positive_ratio: float = 0.0
	neutral_ratio: float = 1.0
	negative_ratio: float = 0.0
	confidence_score: float = 0.0
	confidence_label: str = "hesitant"
	sentiment_label: str = "neutral"


@dataclass
class LinguisticAnalysis:
	"""Complete linguistic analysis for one answer text."""

	readability: ReadabilityMetrics
	sentences: SentenceMetrics
	discourse: DiscourseMetrics
	lexical: LexicalMetrics
	filler_ratio: float
	hedging_ratio: float
	technical_density: float
	technical_phrases: TechnicalPhraseMetrics
	sentiment_confidence: SentimentConfidenceMetrics
	linguistic_score: float      # 0–1 aggregate for the evaluator
	# Backward-compatible aliases used by answer_evaluator.CommunicationScore
	word_count: int
	vocabulary_diversity: float  # alias for type_token_ratio


# ---------------------------------------------------------------------------
# Computation helpers
# ---------------------------------------------------------------------------


def _readability_metrics(words: list[str], sentences: list[str]) -> ReadabilityMetrics:
	n_words = len(words)
	n_sents = max(len(sentences), 1)
	n_syllables = sum(_count_syllables(w) for w in words)

	avg_syl = n_syllables / max(n_words, 1)
	avg_wps = n_words / n_sents

	reading_ease = 206.835 - 1.015 * avg_wps - 84.6 * avg_syl
	reading_ease = max(0.0, min(100.0, reading_ease))

	grade_level = 0.39 * avg_wps + 11.8 * avg_syl - 15.59
	grade_level = max(0.0, grade_level)

	if reading_ease >= 80:
		label = "Very Easy"
	elif reading_ease >= 60:
		label = "Easy"
	elif reading_ease >= 45:
		label = "Standard"
	elif reading_ease >= 25:
		label = "Difficult"
	else:
		label = "Very Difficult"

	return ReadabilityMetrics(
		reading_ease=round(reading_ease, 2),
		grade_level=round(grade_level, 2),
		avg_syllables_per_word=round(avg_syl, 3),
		avg_words_per_sentence=round(avg_wps, 2),
		difficulty_label=label,
	)


def _sentence_metrics(sentences: list[str]) -> SentenceMetrics:
	if not sentences:
		return SentenceMetrics(
			sentence_count=0,
			avg_words=0.0,
			std_words=0.0,
			short_sentence_ratio=0.0,
			long_sentence_ratio=0.0,
			complexity_score=0.0,
		)

	lengths = [len(_tokenize_words(s)) for s in sentences]
	n = len(lengths)
	avg = sum(lengths) / n
	variance = sum((l - avg) ** 2 for l in lengths) / n
	std = math.sqrt(variance)

	short = sum(1 for l in lengths if l < 8) / n
	long = sum(1 for l in lengths if l > 35) / n

	# Complexity score: penalise both monotonously short and monotonously long
	# Ideal: mix of medium sentences (8-25 words) with some variation
	medium_ratio = sum(1 for l in lengths if 8 <= l <= 30) / n
	normalised_std = min(1.0, std / 15.0)
	complexity = medium_ratio * 0.6 + normalised_std * 0.4

	return SentenceMetrics(
		sentence_count=n,
		avg_words=round(avg, 2),
		std_words=round(std, 2),
		short_sentence_ratio=round(short, 3),
		long_sentence_ratio=round(long, 3),
		complexity_score=round(complexity, 4),
	)


def _discourse_metrics(text: str, sentence_count: int) -> DiscourseMetrics:
	counts: dict[str, int] = {}
	total = 0
	for category, patterns in _MARKER_PATTERNS.items():
		cat_count = sum(len(p.findall(text)) for p in patterns)
		counts[category] = cat_count
		total += cat_count

	n_sents = max(sentence_count, 1)
	mps = total / n_sents

	# A good technical answer has at least one discourse marker per 2-3 sentences
	coherence = min(1.0, mps / 0.5)

	return DiscourseMetrics(
		total_markers=total,
		markers_per_sentence=round(mps, 4),
		causal_count=counts.get("causal", 0),
		contrastive_count=counts.get("contrastive", 0),
		additive_count=counts.get("additive", 0),
		sequential_count=counts.get("sequential", 0),
		exemplification_count=counts.get("exemplification", 0),
		coherence_score=round(coherence, 4),
	)


def _sliding_window_ttr(words: list[str], window_size: int = 50) -> float:
	"""Mean TTR over overlapping windows of ``window_size`` tokens."""
	n = len(words)
	if n == 0:
		return 0.0
	if n < window_size:
		return len(set(words)) / n

	step = max(1, window_size // 2)
	ttrs: list[float] = []
	for start in range(0, n - window_size + 1, step):
		window = words[start : start + window_size]
		ttrs.append(len(set(window)) / window_size)
	return sum(ttrs) / len(ttrs) if ttrs else 0.0


def _lexical_metrics(words: list[str]) -> LexicalMetrics:
	n = len(words)
	if n == 0:
		return LexicalMetrics(
			word_count=0,
			unique_word_count=0,
			type_token_ratio=0.0,
			hapax_ratio=0.0,
			sliding_ttr=0.0,
			lexical_diversity_score=0.0,
		)

	unique = len(set(words))
	ttr = unique / n

	# Hapax legomena: words that appear exactly once
	from collections import Counter
	freq = Counter(words)
	hapax_count = sum(1 for count in freq.values() if count == 1)
	hapax_ratio = hapax_count / n

	sliding = _sliding_window_ttr(words)

	# Aggregate: weight sliding TTR most heavily (most stable across lengths)
	diversity_score = min(1.0, ttr * 0.3 + hapax_ratio * 0.3 + sliding * 0.4)

	return LexicalMetrics(
		word_count=n,
		unique_word_count=unique,
		type_token_ratio=round(ttr, 4),
		hapax_ratio=round(hapax_ratio, 4),
		sliding_ttr=round(sliding, 4),
		lexical_diversity_score=round(diversity_score, 4),
	)


def _filler_ratio(words: list[str], text: str) -> float:
	matches = _FILLER_PATTERN.findall(text)
	n = max(len(words), 1)
	return round(len(matches) / n, 4)


def _hedging_ratio(words: list[str], text: str) -> float:
	matches: list[str] = []
	for p in _HEDGING_PATTERNS:
		matches.extend(p.findall(text))
	n = max(len(words), 1)
	return round(len(matches) / n, 4)


def _technical_density(words: list[str]) -> float:
	"""Ratio of non-function words (content words) to total words."""
	if not words:
		return 0.0
	content = sum(1 for w in words if w not in _FUNCTION_WORDS)
	return round(content / len(words), 4)


@lru_cache(maxsize=1)
def _load_spacy_nlp() -> Any | None:
	"""Load spaCy with noun chunk support when available."""

	try:
		spacy = import_module("spacy")
	except ModuleNotFoundError:
		return None

	try:
		return spacy.load("en_core_web_sm")
	except Exception:
		_LOGGER.debug("spaCy technical phrase extraction unavailable; using regex fallback.", exc_info=True)
		return None


@lru_cache(maxsize=1)
def _load_vader_analyzer() -> Any | None:
	"""Load VADER sentiment analyzer when available."""

	try:
		module = import_module("vaderSentiment.vaderSentiment")
	except ModuleNotFoundError:
		return None

	try:
		analyzer_class = getattr(module, "SentimentIntensityAnalyzer")
		return analyzer_class()
	except Exception:
		_LOGGER.debug("VADER sentiment analyzer unavailable; using heuristic fallback.", exc_info=True)
		return None


def _normalize_phrase_tokens(text: str) -> list[str]:
	normalized = re.sub(r"([a-zA-Z])\(([^)]+)\)", r"\1 \2 ", str(text or ""))
	raw_tokens = [match.group().lower().rstrip("'s") for match in _PHRASE_TOKEN_RE.finditer(normalized)]
	tokens: list[str] = []
	for token in raw_tokens:
		if len(token) > 4 and token.endswith("ies"):
			token = f"{token[:-3]}y"
		elif len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us")):
			token = token[:-1]
		tokens.append(token)
	return tokens


def _normalize_phrase(text: str) -> str:
	return " ".join(_normalize_phrase_tokens(text))


def _unique_preserve_order(values: Sequence[str]) -> list[str]:
	result: list[str] = []
	seen: set[str] = set()
	for value in values:
		text = str(value or "").strip()
		if not text or text in seen:
			continue
		seen.add(text)
		result.append(text)
	return result


def _expected_term_entries(expected_terms: Sequence[str] | None) -> list[tuple[str, str, set[str]]]:
	entries: list[tuple[str, str, set[str]]] = []
	for raw_value in expected_terms or ():
		original = str(raw_value or "").strip()
		normalized = _normalize_phrase(original)
		tokens = {token for token in _normalize_phrase_tokens(original) if token not in _FUNCTION_WORDS}
		if not original or not normalized or not tokens:
			continue
		entries.append((original, normalized, tokens))
	return entries


def _focus_tokens(expected_terms: Sequence[str] | None) -> set[str]:
	entries = _expected_term_entries(expected_terms)
	if entries:
		return {token for _, _, tokens in entries for token in tokens}
	return set(_TECHNICAL_ANCHOR_TERMS)


def _is_technical_phrase(tokens: Sequence[str], focus_tokens: set[str]) -> bool:
	content_tokens = [token for token in tokens if token and token not in _FUNCTION_WORDS]
	if len(content_tokens) < 2:
		return False
	token_set = set(content_tokens)
	return bool(token_set & focus_tokens) or bool(token_set & _TECHNICAL_ANCHOR_TERMS)


def _extract_regex_technical_phrases(text: str, focus_tokens: set[str]) -> list[str]:
	tokens = _normalize_phrase_tokens(text)
	if len(tokens) < 2:
		return []

	phrases: list[str] = []
	for window_size in range(min(4, len(tokens)), 1, -1):
		for start in range(0, len(tokens) - window_size + 1):
			candidate_tokens = tokens[start : start + window_size]
			if any(token in _FUNCTION_WORDS for token in candidate_tokens):
				continue
			if not _is_technical_phrase(candidate_tokens, focus_tokens):
				continue
			phrases.append(" ".join(candidate_tokens))
	return _unique_preserve_order(phrases)[:12]


def _extract_spacy_technical_phrases(text: str, focus_tokens: set[str]) -> list[str] | None:
	nlp = _load_spacy_nlp()
	if nlp is None:
		return None

	try:
		doc = nlp(text)
	except Exception:
		_LOGGER.debug("spaCy phrase parsing failed; falling back to regex extraction.", exc_info=True)
		return None

	phrases: list[str] = []
	for chunk in getattr(doc, "noun_chunks", []):
		tokens = _normalize_phrase_tokens(getattr(chunk, "text", ""))
		if not _is_technical_phrase(tokens, focus_tokens):
			continue
		phrases.append(" ".join(tokens))
	return _unique_preserve_order(phrases)[:12]


def _phrase_match_ratio(answer_phrase: str, expected_tokens: set[str]) -> float:
	answer_tokens = {token for token in _normalize_phrase_tokens(answer_phrase) if token not in _FUNCTION_WORDS}
	if not answer_tokens or not expected_tokens:
		return 0.0
	intersection = len(answer_tokens & expected_tokens)
	return intersection / max(len(expected_tokens), 1)


def _technical_phrase_metrics(
	text: str,
	*,
	expected_terms: Sequence[str] | None = None,
) -> TechnicalPhraseMetrics:
	entries = _expected_term_entries(expected_terms)
	focus_tokens = _focus_tokens(expected_terms)
	phrases = _extract_spacy_technical_phrases(text, focus_tokens)
	extraction_mode = "spacy" if phrases is not None else "regex"
	if phrases is None:
		phrases = _extract_regex_technical_phrases(text, focus_tokens)

	matched_expected_terms: list[str] = []
	missing_expected_terms: list[str] = []
	for original, _, tokens in entries:
		best_ratio = max((_phrase_match_ratio(phrase, tokens) for phrase in phrases), default=0.0)
		if best_ratio >= 0.6:
			matched_expected_terms.append(original)
		else:
			missing_expected_terms.append(original)

	coverage_ratio = len(matched_expected_terms) / len(entries) if entries else 0.0
	return TechnicalPhraseMetrics(
		extraction_mode=extraction_mode,
		extracted_phrases=phrases,
		expected_terms=[original for original, _, _ in entries],
		matched_expected_terms=matched_expected_terms,
		missing_expected_terms=missing_expected_terms,
		coverage_ratio=round(coverage_ratio, 4),
	)


def _sentiment_label(compound: float, *, positive_ratio: float = 0.0, negative_ratio: float = 0.0) -> str:
	if compound >= 0.45 or (compound >= 0.25 and positive_ratio >= 0.18):
		return "positive"
	if compound <= -0.35 or (compound <= -0.2 and negative_ratio >= 0.18):
		return "negative"
	return "neutral"


def _confidence_label(score: float) -> str:
	if score >= 0.72:
		return "confident"
	if score >= 0.45:
		return "steady"
	return "hesitant"


def _sentiment_confidence_metrics(
	text: str,
	*,
	hedging_ratio: float,
	filler_ratio: float,
) -> SentimentConfidenceMetrics:
	analyzer = _load_vader_analyzer()
	analysis_mode = "vader" if analyzer is not None else "heuristic_fallback"
	available = analyzer is not None
	compound = 0.0
	positive_ratio = 0.0
	neutral_ratio = 1.0
	negative_ratio = 0.0

	if analyzer is not None:
		try:
			scores = analyzer.polarity_scores(text)
		except Exception:
			_LOGGER.debug("VADER sentiment scoring failed; using heuristic fallback.", exc_info=True)
			analysis_mode = "heuristic_fallback"
			available = False
		else:
			compound = float(scores.get("compound", 0.0))
			positive_ratio = float(scores.get("pos", 0.0))
			neutral_ratio = float(scores.get("neu", 1.0))
			negative_ratio = float(scores.get("neg", 0.0))

	uncertainty_penalty = min(0.55, hedging_ratio * 2.8 + filler_ratio * 1.8)
	sentiment_adjustment = (positive_ratio * 0.12) - (negative_ratio * 0.18) + (compound * 0.08)
	confidence_score = max(0.0, min(1.0, 0.74 - uncertainty_penalty + sentiment_adjustment))

	return SentimentConfidenceMetrics(
		available=available,
		analysis_mode=analysis_mode,
		compound=round(compound, 4),
		positive_ratio=round(positive_ratio, 4),
		neutral_ratio=round(neutral_ratio, 4),
		negative_ratio=round(negative_ratio, 4),
		confidence_score=round(confidence_score, 4),
		confidence_label=_confidence_label(confidence_score),
		sentiment_label=_sentiment_label(
			compound,
			positive_ratio=positive_ratio,
			negative_ratio=negative_ratio,
		),
	)


def _readability_component(ease: float) -> float:
	"""
	Map Flesch Reading Ease to a 0–1 quality signal.
	For technical interview answers, the ideal zone is 40–75
	(substantive but not impenetrable).  Values outside that zone
	score lower.
	"""
	if ease < 10:
		return 0.2
	if ease < 25:
		return 0.4
	if ease < 40:
		return 0.7
	if ease <= 75:
		# Linear peak in the ideal zone
		return 0.75 + 0.25 * ((ease - 40) / 35) if ease <= 75 else 1.0
	# Very easy reading (short words/sentences) may indicate superficial answer
	if ease <= 90:
		return 0.7
	return 0.5


def _aggregate_linguistic_score(
	*,
	word_count: int,
	readability: ReadabilityMetrics,
	sentences: SentenceMetrics,
	discourse: DiscourseMetrics,
	lexical: LexicalMetrics,
	filler_ratio: float,
	hedging_ratio: float,
	technical_density: float,
) -> float:
	"""
	Aggregate the six dimensions into a single 0–1 score.

	Weights:
	  - Length adequacy        : 25 %
	  - Discourse coherence    : 25 %
	  - Lexical diversity      : 20 %
	  - Readability quality    : 15 %
	  - Technical density      : 10 %
	  - Sentence complexity    :  5 %

	Penalty applied after aggregation:
	  - Filler ratio   : up to –15 %
	  - Hedging ratio  : up to –10 %
	"""
	# Length adequacy
	if word_count < 20:
		length_comp = 0.35
	elif word_count < 50:
		length_comp = 0.60
	elif word_count <= 300:
		length_comp = 1.0
	else:
		length_comp = max(0.65, 1.0 - (word_count - 300) / 800)

	discourse_comp = discourse.coherence_score
	lexical_comp = lexical.lexical_diversity_score
	readability_comp = _readability_component(readability.reading_ease)
	technical_comp = min(1.0, technical_density * 1.8)  # 55 % content words → 1.0
	sentence_comp = sentences.complexity_score

	base = (
		length_comp * 0.25
		+ discourse_comp * 0.25
		+ lexical_comp * 0.20
		+ readability_comp * 0.15
		+ technical_comp * 0.10
		+ sentence_comp * 0.05
	)

	# Deductions
	filler_penalty = min(0.15, filler_ratio * 0.8)
	hedging_penalty = min(0.10, hedging_ratio * 0.6)

	score = max(0.0, min(1.0, base - filler_penalty - hedging_penalty))
	return round(score, 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_answer(
	text: str,
	*,
	expected_terms: Sequence[str] | None = None,
) -> LinguisticAnalysis:
	"""
	Run full linguistic analysis on an interview answer text.

	Parameters
	----------
	text:
		Raw answer text (transcribed speech or typed).
	expected_terms:
		Optional list of expected technical terms or ideal-answer bullet strings.
		When provided, the analyzer extracts technical noun phrases from the answer
		and compares them against these expected terms.

	Returns
	-------
	LinguisticAnalysis
		All six dimension results plus aggregate ``linguistic_score``.
	"""
	if not text or not text.strip():
		empty_read = ReadabilityMetrics(
			reading_ease=0.0, grade_level=0.0,
			avg_syllables_per_word=0.0, avg_words_per_sentence=0.0,
			difficulty_label="Very Difficult",
		)
		empty_sent = SentenceMetrics(
			sentence_count=0, avg_words=0.0, std_words=0.0,
			short_sentence_ratio=0.0, long_sentence_ratio=0.0,
			complexity_score=0.0,
		)
		empty_disc = DiscourseMetrics(
			total_markers=0, markers_per_sentence=0.0,
			causal_count=0, contrastive_count=0, additive_count=0,
			sequential_count=0, exemplification_count=0, coherence_score=0.0,
		)
		empty_lex = LexicalMetrics(
			word_count=0, unique_word_count=0, type_token_ratio=0.0,
			hapax_ratio=0.0, sliding_ttr=0.0, lexical_diversity_score=0.0,
		)
		empty_phrases = TechnicalPhraseMetrics(extraction_mode="regex")
		empty_sentiment = SentimentConfidenceMetrics(
			available=False,
			analysis_mode="heuristic_fallback",
		)
		return LinguisticAnalysis(
			readability=empty_read,
			sentences=empty_sent,
			discourse=empty_disc,
			lexical=empty_lex,
			filler_ratio=1.0,
			hedging_ratio=0.0,
			technical_density=0.0,
			technical_phrases=empty_phrases,
			sentiment_confidence=empty_sentiment,
			linguistic_score=0.0,
			word_count=0,
			vocabulary_diversity=0.0,
		)

	words = _tokenize_words(text)
	sentences = _split_sentences(text)

	readability = _readability_metrics(words, sentences)
	sentence_m = _sentence_metrics(sentences)
	discourse = _discourse_metrics(text, sentence_m.sentence_count)
	lexical = _lexical_metrics(words)
	filler = _filler_ratio(words, text)
	hedging = _hedging_ratio(words, text)
	technical = _technical_density(words)
	technical_phrases = _technical_phrase_metrics(text, expected_terms=expected_terms)
	sentiment_confidence = _sentiment_confidence_metrics(
		text,
		hedging_ratio=hedging,
		filler_ratio=filler,
	)

	score = _aggregate_linguistic_score(
		word_count=len(words),
		readability=readability,
		sentences=sentence_m,
		discourse=discourse,
		lexical=lexical,
		filler_ratio=filler,
		hedging_ratio=hedging,
		technical_density=technical,
	)

	return LinguisticAnalysis(
		readability=readability,
		sentences=sentence_m,
		discourse=discourse,
		lexical=lexical,
		filler_ratio=filler,
		hedging_ratio=hedging,
		technical_density=technical,
		technical_phrases=technical_phrases,
		sentiment_confidence=sentiment_confidence,
		linguistic_score=score,
		word_count=len(words),
		vocabulary_diversity=lexical.type_token_ratio,
	)


__all__ = [
	"LinguisticAnalysis",
	"ReadabilityMetrics",
	"SentenceMetrics",
	"DiscourseMetrics",
	"LexicalMetrics",
	"SentimentConfidenceMetrics",
	"TechnicalPhraseMetrics",
	"analyze_answer",
]
