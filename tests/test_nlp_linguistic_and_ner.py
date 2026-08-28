"""Tests for the linguistic_analyzer and ner_extractor NLP modules."""

from __future__ import annotations

from unittest.mock import patch
import unittest


class TestLinguisticAnalyzer(unittest.TestCase):
    """Tests for backend.nlp.linguistic_analyzer.analyze_answer."""

    def _analyze(self, text: str, expected_terms=None):
        from backend.nlp.linguistic_analyzer import analyze_answer
        return analyze_answer(text, expected_terms=expected_terms)

    # ------------------------------------------------------------------
    # Empty / edge inputs
    # ------------------------------------------------------------------

    def test_empty_text_returns_zero_score(self):
        result = self._analyze("")
        self.assertEqual(result.linguistic_score, 0.0)
        self.assertEqual(result.word_count, 0)

    def test_whitespace_only_returns_zero(self):
        result = self._analyze("   \n\t  ")
        self.assertEqual(result.linguistic_score, 0.0)

    # ------------------------------------------------------------------
    # Readability metrics
    # ------------------------------------------------------------------

    def test_readability_ease_range(self):
        text = (
            "A binary search tree is a data structure where each node has at most "
            "two children. The left child contains values smaller than the parent "
            "and the right child contains larger values. This property makes "
            "search, insertion, and deletion operations run in O(log n) time "
            "for balanced trees."
        )
        result = self._analyze(text)
        # Flesch Reading Ease should be in a valid range
        self.assertGreaterEqual(result.readability.reading_ease, 0.0)
        self.assertLessEqual(result.readability.reading_ease, 100.0)

    def test_simple_text_has_higher_ease(self):
        simple = "A list stores items. You can add or remove items."
        complex_ = (
            "Polymorphism, a foundational object-oriented principle, facilitates "
            "runtime substitution of method implementations through dynamic dispatch."
        )
        r_simple = self._analyze(simple)
        r_complex = self._analyze(complex_)
        self.assertGreater(
            r_simple.readability.reading_ease,
            r_complex.readability.reading_ease,
        )

    def test_difficulty_label_populated(self):
        text = "Python is a programming language."
        result = self._analyze(text)
        self.assertIn(
            result.readability.difficulty_label,
            {"Very Easy", "Easy", "Standard", "Difficult", "Very Difficult"},
        )

    # ------------------------------------------------------------------
    # Sentence metrics
    # ------------------------------------------------------------------

    def test_sentence_count_positive(self):
        text = "This is sentence one. This is sentence two. And a third one."
        result = self._analyze(text)
        self.assertGreater(result.sentences.sentence_count, 0)

    def test_avg_sentence_length_reasonable(self):
        text = (
            "Memory management in C++ requires manual allocation and deallocation. "
            "The new operator allocates memory on the heap. "
            "You must call delete to free it and avoid memory leaks."
        )
        result = self._analyze(text)
        # Average words per sentence should be in a sane range
        self.assertGreater(result.sentences.avg_words, 3)
        self.assertLess(result.sentences.avg_words, 60)

    # ------------------------------------------------------------------
    # Discourse metrics
    # ------------------------------------------------------------------

    def test_discourse_markers_detected(self):
        text = (
            "Quicksort uses a divide-and-conquer approach. "
            "First, we choose a pivot element. "
            "Then we partition the array so that all elements less than the pivot "
            "go to the left. "
            "Finally, we recursively sort both halves. "
            "Therefore, the average time complexity is O(n log n). "
            "However, the worst case is O(n^2) because poor pivot selection "
            "causes unbalanced partitions."
        )
        result = self._analyze(text)
        self.assertGreater(result.discourse.total_markers, 0)
        self.assertGreater(result.discourse.coherence_score, 0.0)

    def test_no_discourse_markers_gives_low_coherence(self):
        text = "Sort array. Put pivot. Move elements. Compare values. Repeat."
        result = self._analyze(text)
        # Very terse text with almost no connectives scores low coherence
        self.assertLess(result.discourse.coherence_score, 0.5)

    # ------------------------------------------------------------------
    # Lexical diversity
    # ------------------------------------------------------------------

    def test_repetitive_text_has_low_diversity(self):
        text = "cat cat cat cat cat cat cat cat cat cat cat cat"
        result = self._analyze(text)
        self.assertLess(result.lexical.type_token_ratio, 0.3)

    def test_varied_text_has_higher_diversity(self):
        text = (
            "Dependency injection decouples the creation of an object from its usage. "
            "Instead of constructing dependencies internally, classes receive them "
            "through constructors or setter methods. "
            "This approach improves testability because dependencies can be mocked."
        )
        result = self._analyze(text)
        self.assertGreater(result.lexical.type_token_ratio, 0.5)

    # ------------------------------------------------------------------
    # Technical density
    # ------------------------------------------------------------------

    def test_technical_answer_has_high_density(self):
        text = (
            "HashMap uses hashing to store key-value pairs. "
            "The hash function computes a bucket index from the key. "
            "Collision resolution uses chaining with linked lists. "
            "Load factor determines when rehashing occurs to maintain O(1) access."
        )
        result = self._analyze(text)
        self.assertGreater(result.technical_density, 0.4)

    def test_technical_noun_phrases_match_expected_terms(self):
        text = (
            "A binary search tree stores ordered keys. "
            "A balanced binary search tree keeps lookup close to logarithmic time. "
            "Hash map collision handling is a different strategy used in hash tables."
        )
        result = self._analyze(
            text,
            expected_terms=["binary search tree", "hash map collision", "linked list"],
        )
        self.assertIn("binary search tree", result.technical_phrases.extracted_phrases)
        self.assertIn("binary search tree", result.technical_phrases.matched_expected_terms)
        self.assertIn("linked list", result.technical_phrases.missing_expected_terms)
        self.assertGreater(result.technical_phrases.coverage_ratio, 0.0)
        self.assertIn(result.technical_phrases.extraction_mode, {"regex", "spacy"})

    # ------------------------------------------------------------------
    # Filler and hedging
    # ------------------------------------------------------------------

    def test_filler_words_increase_filler_ratio(self):
        text = "Um, so basically, like, um, you know, arrays are, like, basically lists."
        result = self._analyze(text)
        self.assertGreater(result.filler_ratio, 0.1)

    def test_hedging_language_detected(self):
        text = "I think maybe the answer could possibly be O(n log n), probably."
        result = self._analyze(text)
        self.assertGreater(result.hedging_ratio, 0.0)

    def test_confident_answer_scores_higher_confidence_than_hesitant_answer(self):
        confident = (
            "The best approach uses a hash map for constant-time lookup. "
            "We then resolve collisions with chaining and keep the load factor controlled."
        )
        hesitant = (
            "I think maybe we could probably use some kind of hash map, "
            "but I am not sure whether that would work."
        )
        result_confident = self._analyze(confident)
        result_hesitant = self._analyze(hesitant)
        self.assertGreater(
            result_confident.sentiment_confidence.confidence_score,
            result_hesitant.sentiment_confidence.confidence_score,
        )

    def test_neutral_technical_answer_can_still_be_confident(self):
        text = (
            "The API gateway validates the token, forwards the request to the backend service, "
            "and caches repeated profile lookups to reduce latency."
        )
        result = self._analyze(text)
        self.assertEqual(result.sentiment_confidence.sentiment_label, "neutral")
        self.assertGreaterEqual(result.sentiment_confidence.confidence_score, 0.55)
        self.assertIn(result.sentiment_confidence.confidence_label, {"steady", "confident"})

    def test_sentiment_confidence_uses_heuristic_fallback_when_vader_is_missing(self):
        with patch("backend.nlp.linguistic_analyzer._load_vader_analyzer", return_value=None):
            result = self._analyze("I am confident this approach uses a queue to process nodes level by level.")
        self.assertFalse(result.sentiment_confidence.available)
        self.assertEqual(result.sentiment_confidence.analysis_mode, "heuristic_fallback")
        self.assertGreaterEqual(result.sentiment_confidence.confidence_score, 0.0)
        self.assertLessEqual(result.sentiment_confidence.confidence_score, 1.0)

    # ------------------------------------------------------------------
    # Aggregate score
    # ------------------------------------------------------------------

    def test_good_technical_answer_scores_higher(self):
        good = (
            "A hash map stores key-value pairs using a hash function to determine "
            "bucket indices. Collisions are resolved through chaining. Therefore, "
            "average-case lookup is O(1). However, worst-case is O(n) because of "
            "hash collisions clustering into one bucket. For example, if all keys "
            "hash to the same bucket, search degrades to linear time. "
            "Additionally, rehashing occurs when the load factor exceeds a threshold, "
            "which amortises insertion cost to O(1)."
        )
        poor = "um basically hash maps use hashing you know like to store stuff"
        result_good = self._analyze(good)
        result_poor = self._analyze(poor)
        self.assertGreater(result_good.linguistic_score, result_poor.linguistic_score)

    def test_score_is_in_range(self):
        text = "The stack uses LIFO order. Push adds to the top. Pop removes from the top."
        result = self._analyze(text)
        self.assertGreaterEqual(result.linguistic_score, 0.0)
        self.assertLessEqual(result.linguistic_score, 1.0)

    # ------------------------------------------------------------------
    # Backward compatibility fields
    # ------------------------------------------------------------------

    def test_backward_compat_word_count(self):
        text = "Python is a high level programming language."
        result = self._analyze(text)
        self.assertEqual(result.word_count, len(text.split()))  # approx

    def test_vocabulary_diversity_alias(self):
        text = "Each unique word increases vocabulary diversity in this sentence."
        result = self._analyze(text)
        # vocabulary_diversity is an alias for type_token_ratio
        self.assertAlmostEqual(
            result.vocabulary_diversity, result.lexical.type_token_ratio, places=4
        )


class TestNerExtractor(unittest.TestCase):
    """Tests for backend.nlp.ner_extractor."""

    def _extract(self, text: str):
        from backend.nlp.ner_extractor import extract_entities
        return extract_entities(text)

    def _validate(self, skills, text):
        from backend.nlp.ner_extractor import validate_skills
        return validate_skills(skills, text)

    # ------------------------------------------------------------------
    # extract_entities
    # ------------------------------------------------------------------

    def test_empty_text_returns_empty_result(self):
        result = self._extract("")
        self.assertEqual(result.entity_count, 0)
        self.assertEqual(result.technologies, [])

    def test_detects_python(self):
        result = self._extract("Built a REST API using Python and FastAPI.")
        tech_lower = [t.lower() for t in result.technologies]
        self.assertIn("python", tech_lower)

    def test_detects_multiple_technologies(self):
        text = (
            "Developed with React, Node.js, and PostgreSQL. "
            "Containerized using Docker and deployed on AWS."
        )
        result = self._extract(text)
        self.assertGreaterEqual(len(result.technologies), 3)

    def test_detects_ml_frameworks(self):
        text = "Trained a BERT model using PyTorch for text classification."
        result = self._extract(text)
        tech_lower = [t.lower() for t in result.technologies]
        # At least one of pytorch or bert should be found
        self.assertTrue(
            any(t in tech_lower for t in ("pytorch", "bert")),
            f"Expected PyTorch or BERT in {result.technologies}",
        )

    def test_ner_mode_is_string(self):
        result = self._extract("Python developer with 2 years of experience.")
        self.assertIn(result.ner_mode, ("spacy", "regex"))

    # ------------------------------------------------------------------
    # validate_skills
    # ------------------------------------------------------------------

    def test_confirmed_skills_appear_in_text(self):
        text = "Proficient in Python, SQL, and Docker."
        groq_skills = ["Python", "SQL", "Docker", "Kubernetes"]
        result = self._validate(groq_skills, text)
        self.assertIn("Python", result.confirmed)
        self.assertIn("SQL", result.confirmed)
        self.assertIn("Docker", result.confirmed)

    def test_unconfirmed_skill_absent_from_text(self):
        text = "Proficient in Python and SQL."
        groq_skills = ["Python", "SQL", "Kubernetes"]
        result = self._validate(groq_skills, text)
        self.assertIn("Kubernetes", result.unconfirmed)

    def test_coverage_ratio_is_fraction(self):
        text = "Knows Python."
        groq_skills = ["Python", "Java", "C++"]
        result = self._validate(groq_skills, text)
        self.assertGreaterEqual(result.coverage_ratio, 0.0)
        self.assertLessEqual(result.coverage_ratio, 1.0)
        # Only Python is confirmed (1/3 ≈ 0.33)
        self.assertAlmostEqual(result.coverage_ratio, 1 / 3, places=2)

    def test_extra_found_detected(self):
        text = "Built microservices with Docker and Kubernetes."
        groq_skills = ["Python"]  # Groq missed Docker and Kubernetes
        result = self._validate(groq_skills, text)
        extra_lower = [e.lower() for e in result.extra_found]
        self.assertTrue(
            any("docker" in e or "kubernetes" in e for e in extra_lower),
            f"Expected Docker or Kubernetes in extras: {result.extra_found}",
        )

    def test_empty_skills_list(self):
        text = "Python developer."
        result = self._validate([], text)
        self.assertEqual(result.confirmed, [])
        self.assertEqual(result.unconfirmed, [])

    # ------------------------------------------------------------------
    # ner_health
    # ------------------------------------------------------------------

    def test_ner_health_returns_dict(self):
        from backend.nlp.ner_extractor import ner_health
        health = ner_health()
        self.assertIn("ner_mode", health)
        self.assertIn("spacy_available", health)
        self.assertIn("tech_pattern_count", health)
        self.assertIsInstance(health["tech_pattern_count"], int)
        self.assertGreater(health["tech_pattern_count"], 0)


class TestCommunicationScoreExtendedFields(unittest.TestCase):
    """Verify that answer_evaluator._compute_communication_score returns the new fields."""

    def _compute(self, text: str):
        # Access the private function via the module
        import importlib
        mod = importlib.import_module("backend.nlp.answer_evaluator")
        return mod._compute_communication_score(text)

    def test_new_fields_present(self):
        text = (
            "Dependency injection is a pattern where objects receive their "
            "dependencies from the outside rather than creating them internally. "
            "Therefore, classes become easier to test because dependencies can "
            "be mocked. For example, a service class can receive a mock database "
            "connection during unit tests."
        )
        result = self._compute(text)
        # Core fields
        self.assertIn("score", result)
        self.assertIn("word_count", result)
        self.assertIn("filler_ratio", result)
        self.assertIn("vocabulary_diversity", result)
        # New linguistic fields
        self.assertIn("readability_ease", result)
        self.assertIn("readability_grade", result)
        self.assertIn("difficulty_label", result)
        self.assertIn("sentence_count", result)
        self.assertIn("avg_sentence_length", result)
        self.assertIn("discourse_coherence", result)
        self.assertIn("discourse_markers", result)
        self.assertIn("hapax_ratio", result)
        self.assertIn("sliding_ttr", result)
        self.assertIn("hedging_ratio", result)
        self.assertIn("technical_density", result)
        self.assertIn("technical_noun_phrases", result)
        self.assertIn("matched_expected_terms", result)
        self.assertIn("missing_expected_terms", result)
        self.assertIn("technical_term_coverage", result)
        self.assertIn("technical_phrase_extraction_mode", result)
        self.assertIn("sentiment_available", result)
        self.assertIn("sentiment_analysis_mode", result)
        self.assertIn("sentiment_compound", result)
        self.assertIn("sentiment_positive_ratio", result)
        self.assertIn("sentiment_neutral_ratio", result)
        self.assertIn("sentiment_negative_ratio", result)
        self.assertIn("confidence_score", result)
        self.assertIn("confidence_label", result)
        self.assertIn("sentiment_label", result)

    def test_expected_term_coverage_is_reported(self):
        text = (
            "A hash map uses collision handling to maintain average constant-time lookup. "
            "The collision handling strategy usually uses chaining or probing."
        )
        import importlib
        mod = importlib.import_module("backend.nlp.answer_evaluator")
        result = mod._compute_communication_score(
            text,
            expected_terms=["hash map", "collision handling", "binary search tree"],
        )
        self.assertGreater(result["technical_term_coverage"], 0.0)
        self.assertIn("hash map", result["matched_expected_terms"])
        self.assertIn("binary search tree", result["missing_expected_terms"])
        self.assertIn(result["technical_phrase_extraction_mode"], {"regex", "spacy"})

    def test_confidence_fields_are_reported(self):
        text = "I am confident this queue-based breadth first search solution is the correct approach."
        import importlib
        mod = importlib.import_module("backend.nlp.answer_evaluator")
        result = mod._compute_communication_score(text)
        self.assertGreaterEqual(result["confidence_score"], 0.0)
        self.assertLessEqual(result["confidence_score"], 1.0)
        self.assertIn(result["confidence_label"], {"hesitant", "steady", "confident"})
        self.assertIn(result["sentiment_label"], {"negative", "neutral", "positive"})

    def test_score_is_in_range(self):
        text = "Arrays store elements contiguously. Access is O(1) by index."
        result = self._compute(text)
        self.assertGreaterEqual(result["score"], 0.0)
        self.assertLessEqual(result["score"], 1.0)

    def test_discourse_markers_positive_for_connective_text(self):
        text = (
            "First, initialise the array. Then, iterate through elements. "
            "Therefore, the complexity is O(n). However, space is O(1) "
            "because we use in-place swaps."
        )
        result = self._compute(text)
        self.assertGreater(result["discourse_markers"], 0)
        self.assertGreater(result["discourse_coherence"], 0.0)


if __name__ == "__main__":
    unittest.main()
