from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from backend.nlp import feedback_generator


class FeedbackGeneratorConfidenceTests(TestCase):
	def _record(
		self,
		*,
		round_name: str = "technical",
		score: float = 0.62,
		communication_score: float = 0.6,
		confidence_score: float | None = 0.6,
		confidence_label: str = "steady",
		sentiment_label: str = "neutral",
		sentiment_compound: float = 0.0,
		hedging_ratio: float = 0.04,
		filler_ratio: float = 0.01,
		answer_text: str = "The approach uses a queue to process nodes level by level.",
	) -> dict[str, object]:
		record: dict[str, object] = {
			"round": round_name,
			"question_text": "Explain the approach.",
			"user_answer_text": answer_text,
			"feedback": "Good structure.",
			"score": score,
			"final_score": score,
			"groq_score": score,
			"sbert_score": score,
			"communication_score": communication_score,
		}
		if confidence_score is not None:
			record["dimension_scores"] = {
				"communication_detail": {
					"score": communication_score,
					"word_count": 28,
					"filler_ratio": filler_ratio,
					"vocabulary_diversity": 0.72,
					"hedging_ratio": hedging_ratio,
					"sentiment_available": True,
					"sentiment_analysis_mode": "vader",
					"sentiment_compound": sentiment_compound,
					"sentiment_positive_ratio": 0.18,
					"sentiment_neutral_ratio": 0.74,
					"sentiment_negative_ratio": 0.08,
					"confidence_score": confidence_score,
					"confidence_label": confidence_label,
					"sentiment_label": sentiment_label,
				},
			}
		return record

	def test_local_round_feedback_calls_out_hesitation_for_hr(self) -> None:
		records = [
			self._record(
				round_name="hr",
				score=0.58,
				communication_score=0.54,
				confidence_score=0.46,
				confidence_label="hesitant",
				hedging_ratio=0.15,
				answer_text="I think maybe I could fit this role because I probably worked hard on a few projects.",
			),
			self._record(
				round_name="hr",
				score=0.61,
				communication_score=0.57,
				confidence_score=0.5,
				confidence_label="hesitant",
				hedging_ratio=0.12,
				answer_text="I guess I might say my strength is teamwork, but I am not fully sure how to explain it.",
			),
		]

		result = feedback_generator._build_local_round_feedback(
			"hr",
			records,
			fallback_reason="groq_not_configured",
		)

		self.assertTrue(result["confidence_signal"]["hesitation_detected"])
		self.assertTrue(any("hesitant" in item.lower() for item in result["improvement_areas"]))
		self.assertIn("hesitation", result["coaching_note"].lower())

	def test_round_specific_thresholds_make_hr_stricter_than_technical(self) -> None:
		records = [
			self._record(confidence_score=0.55, hedging_ratio=0.03, confidence_label="steady"),
			self._record(confidence_score=0.56, hedging_ratio=0.04, confidence_label="steady"),
		]

		hr_signal = feedback_generator.summarize_confidence_signal("hr", records)
		technical_signal = feedback_generator.summarize_confidence_signal("technical", records)

		self.assertTrue(hr_signal["hesitation_detected"])
		self.assertFalse(technical_signal["hesitation_detected"])

	def test_generate_round_feedback_threads_confidence_signal_into_prompt(self) -> None:
		records = [
			self._record(
				confidence_score=0.44,
				confidence_label="hesitant",
				hedging_ratio=0.14,
				answer_text="I think maybe we could use a hash map, but I am not fully sure.",
			),
		]
		completion = SimpleNamespace(
			choices=[
				SimpleNamespace(
					message=SimpleNamespace(
						content='{"strengths": ["You stayed on topic."], "improvement_areas": ["Be more direct."], "coaching_note": "You showed useful signal. Reduce hesitation and answer more directly."}'
					)
				)
			]
		)

		with patch("backend.nlp.feedback_generator.create_chat_completion", return_value=completion) as mock_completion:
			result = feedback_generator.generate_round_feedback(
				"technical",
				records,
				settings=SimpleNamespace(feedback_generator_model="test-model"),
			)

		prompt = mock_completion.call_args.kwargs["messages"][1]["content"]
		self.assertIn("Confidence signal", prompt)
		self.assertIn("Hesitation detected: yes", prompt)
		self.assertTrue(result["confidence_signal"]["hesitation_detected"])

	def test_calibration_uses_transcripts_when_saved_confidence_is_missing(self) -> None:
		records = [
			self._record(
				confidence_score=None,
				score=0.78,
				answer_text="I am confident the best approach uses a queue for breadth first search and it is the right tradeoff.",
			),
			self._record(
				confidence_score=None,
				score=0.75,
				answer_text="The correct solution uses a hash map and I am sure this gives constant average lookup time.",
			),
			self._record(
				confidence_score=None,
				score=0.42,
				answer_text="I think maybe we could probably use a list, but I am not sure if that is optimal.",
			),
			self._record(
				confidence_score=None,
				score=0.39,
				answer_text="I guess maybe a loop could work, although I am not really certain about the complexity.",
			),
		]

		profile = feedback_generator.calibrate_confidence_thresholds(
			"technical",
			records,
			min_samples=4,
			min_quality_samples=2,
		)

		self.assertEqual(profile["calibration_source"], "transcript_percentiles")
		self.assertEqual(profile["sample_size"], 4)
		self.assertGreater(profile["confident_min"], profile["hesitant_max"])

	def test_legacy_communication_detail_without_confidence_recomputes_from_transcript(self) -> None:
		record = self._record(confidence_score=0.0)
		record["dimension_scores"] = {
			"communication_detail": {
				"score": 0.58,
				"word_count": 24,
				"filler_ratio": 0.01,
				"vocabulary_diversity": 0.69,
				"hedging_ratio": 0.03,
			}
		}

		signal = feedback_generator.summarize_confidence_signal(
			"technical",
			[record],
			min_calibration_samples=1,
		)

		self.assertEqual(signal["available_responses"], 1)
		self.assertGreater(signal["average_confidence"], 0.0)