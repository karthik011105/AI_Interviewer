from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api.auth import AuthenticatedUser
from backend.api import routes_interview
from backend.main import create_app
from backend.nlp import answer_evaluator


class AnswerEvaluationTests(TestCase):
	def test_load_sbert_model_reuses_shared_encoder(self) -> None:
		shared_encoder = object()

		with patch("backend.nlp.answer_evaluator.get_semantic_encoder", return_value=shared_encoder) as mock_get_encoder:
			loaded_encoder = answer_evaluator._load_sbert_model()

		self.assertIs(loaded_encoder, shared_encoder)
		mock_get_encoder.assert_called_once_with()

	def test_evaluate_answer_uses_local_fallback_with_shared_encoder(self) -> None:
		shared_encoder = MagicMock()
		shared_encoder.encode.return_value = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

		with patch("backend.nlp.answer_evaluator.get_settings", return_value=SimpleNamespace(groq=None)), \
			 patch("backend.nlp.answer_evaluator.get_semantic_encoder", return_value=shared_encoder), \
			 patch("backend.nlp.answer_evaluator._cosine_similarity", side_effect=[0.8, 0.2]):
			result = answer_evaluator.evaluate_answer(
				question="Explain hash map lookups.",
				ideal_points=["Average lookup is O(1)", "Collisions need a resolution strategy"],
				follow_up="",
				answer_text="Hash maps usually give average O one lookup and need a collision strategy when keys overlap.",
				round_type="technical",
			)

		self.assertEqual(result["evaluation_mode"], "local_fallback")
		self.assertEqual(result["fallback_reason"], "groq_not_configured")
		self.assertAlmostEqual(result["sbert_score"], 0.5, places=4)
		self.assertGreater(result["local_score"], 0.0)
		self.assertTrue(result["concept_coverage"]["available"])
		self.assertGreater(result["concept_coverage"]["covered_count"], 0)
		self.assertEqual(result["scoring_profile"]["round_type"], "technical")
		shared_encoder.encode.assert_called_once()

	def test_compute_concept_coverage_marks_matching_ideal_points(self) -> None:
		result = answer_evaluator.compute_concept_coverage(
			"I would use a hash map for O(1) lookups, then use chaining when collisions happen.",
			["hash map", "O(1) lookup", "collision handling"],
		)

		self.assertTrue(result["available"])
		self.assertEqual(result["total_concepts"], 3)
		self.assertGreaterEqual(result["covered_count"], 2)
		self.assertGreater(result["tfidf_score"], 0.0)
		self.assertEqual(result["concept_details"][0]["point"], "hash map")

	def test_round_specific_scoring_profile_shifts_weight_between_hr_and_technical(self) -> None:
		technical = answer_evaluator._resolve_scoring_profile("technical")
		hr = answer_evaluator._resolve_scoring_profile("hr")

		self.assertGreater(technical["coverage_threshold"], hr["coverage_threshold"])
		self.assertGreater(technical["weights"]["concept_coverage"], hr["weights"]["concept_coverage"])
		self.assertGreater(hr["weights"]["communication"], technical["weights"]["communication"])

	def test_local_feedback_for_strong_answer_is_strengths_based(self) -> None:
		feedback = answer_evaluator._build_local_feedback_text(
			ideal_points=["Average lookup is O(1)", "Collisions need a resolution strategy"],
			sbert_score=0.82,
			communication={
				"score": 0.71,
				"word_count": 32,
				"vocabulary_diversity": 0.72,
				"discourse_coherence": 0.4,
				"confidence_score": 0.74,
			},
			coverage={
				"coverage_ratio": 1.0,
				"covered_count": 2,
				"total_concepts": 2,
				"tfidf_score": 0.64,
				"concept_details": [],
				"threshold": 0.12,
				"available": True,
			},
		)

		self.assertIn("Strong answer", feedback)
		self.assertNotRegex(feedback.lower(), r"\b(consider|improve|strengthen|focus|mention|add|include)\b")

	def test_strong_groq_feedback_is_normalized_when_it_uses_generic_coaching(self) -> None:
		with patch("backend.nlp.answer_evaluator.get_settings", return_value=SimpleNamespace(groq=SimpleNamespace(answer_evaluator_model="test-model"))), \
			 patch("backend.nlp.answer_evaluator._compute_communication_score", return_value={
				"score": 0.7,
				"word_count": 30,
				"vocabulary_diversity": 0.7,
				"discourse_coherence": 0.42,
				"confidence_score": 0.72,
			}), \
			 patch("backend.nlp.answer_evaluator.compute_sbert_similarity", return_value=0.81), \
			 patch("backend.nlp.answer_evaluator.compute_concept_coverage", return_value={
				"coverage_ratio": 1.0,
				"covered_count": 2,
				"total_concepts": 2,
				"tfidf_score": 0.58,
				"concept_details": [],
				"threshold": 0.12,
				"available": True,
			}), \
			 patch("backend.nlp.answer_evaluator.evaluate_with_groq", return_value={
				"score": 0.88,
				"relevance": 0.9,
				"depth": 0.84,
				"accuracy": 0.87,
				"feedback": "Good answer. Consider mentioning the tradeoff more clearly.",
			}):
			result = answer_evaluator.evaluate_answer(
				question="Explain hash map lookup.",
				ideal_points=["Average lookup is O(1)", "Collisions need a resolution strategy"],
				follow_up="",
				answer_text="Hash maps usually give O one average lookup and handle collisions with chaining or probing.",
				round_type="technical",
			)

		self.assertEqual(result["evaluation_mode"], "groq")
		self.assertIn("Strong answer", result["feedback"])
		self.assertNotIn("Consider", result["feedback"])


class InterviewProgressionTests(TestCase):
	def setUp(self) -> None:
		self.current_user = AuthenticatedUser(
			user_id="user-123",
			email="user@example.com",
			raw_user={"id": "user-123", "email": "user@example.com"},
		)

	def test_start_interview_round_requires_force_restart_for_completed_round(self) -> None:
		request = routes_interview.InterviewStartRequest(
			session_id="session-123",
			round="technical",
			force_restart=False,
		)
		completed_round = {
			"status": "complete",
			"questions_json": {"questions": [{"question": "What is an API?"}]},
			"current_question_index": 0,
		}

		with patch("backend.api.routes_interview._require_parent_session", return_value={"role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_interview.get_interview_round_session", return_value=completed_round):
			with self.assertRaises(HTTPException) as context:
				routes_interview.start_interview_round(request, self.current_user)

		self.assertEqual(context.exception.status_code, 409)
		self.assertIn("force_restart=true", str(context.exception.detail))

	def test_start_interview_round_force_restart_rebuilds_completed_round(self) -> None:
		request = routes_interview.InterviewStartRequest(
			session_id="session-123",
			round="technical",
			force_restart=True,
		)
		parent_session = {"role_selected": "backend_python_developer"}
		existing_round = {
			"status": "complete",
			"difficulty_signal": 0.7,
			"questions_json": {"questions": [{"question": "old question"}]},
		}
		questions_json = {
			"questions": [
				{
					"question": "Explain dependency injection.",
					"ideal_points": ["Loose coupling"],
					"follow_up": "Where is it useful?",
					"difficulty": "medium",
				}
			],
		}
		created_round = {
			"status": "in_progress",
			"current_question_index": 0,
			"questions_json": questions_json,
		}

		with patch("backend.api.routes_interview._require_parent_session", return_value=parent_session), \
			 patch("backend.api.routes_interview.get_interview_round_session", return_value=existing_round), \
			 patch("backend.api.routes_interview._get_parsed_resume", return_value={"skills": ["Python"]}), \
			 patch("backend.api.routes_interview._build_round_context", return_value={"selected_role": "backend_python_developer"}) as mock_build_context, \
			 patch("backend.api.routes_interview.get_or_generate_questions", return_value=questions_json) as mock_generate_questions, \
			 patch("backend.api.routes_interview.create_interview_round_session", return_value=created_round) as mock_create_round:
			result = routes_interview.start_interview_round(request, self.current_user)

		self.assertTrue(result["created"])
		self.assertEqual(result["questions_json"]["questions"], questions_json["questions"])
		self.assertEqual(result["questions_json"]["covered_topics"], [])
		self.assertEqual(result["questions_json"]["covered_topic_summary"]["total_questions_covered"], 0)
		mock_build_context.assert_called_once()
		mock_generate_questions.assert_called_once_with(
			"technical",
			{"selected_role": "backend_python_developer"},
			cached_questions_json=None,
			difficulty_signal=0.7,
		)
		mock_create_round.assert_called_once_with(
			session_id="session-123",
			round="technical",
			role_key="backend_python_developer",
			questions_json=mock_create_round.call_args.kwargs["questions_json"],
			started_at=mock_create_round.call_args.kwargs["started_at"],
		)
		persisted_questions_json = mock_create_round.call_args.kwargs["questions_json"]
		self.assertEqual(persisted_questions_json["questions"], questions_json["questions"])
		self.assertEqual(persisted_questions_json["covered_topics"], [])

	def test_start_interview_round_persists_skill_profile_summary_for_technical_round(self) -> None:
		request = routes_interview.InterviewStartRequest(
			session_id="session-123",
			round="technical",
			force_restart=True,
		)
		parent_session = {"role_selected": "backend_python_developer"}
		context = {
			"strong_skills": ["Python", "FastAPI"],
			"familiar_skills": ["SQL"],
			"mentioned_skills": [],
			"absent_skills": ["Git"],
			"soft_gap_skills": ["PostgreSQL"],
			"priority_focus_areas": ["Git", "SQL", "Python"],
			"skill_profile": {"skill_scores": {"Python": {"tier": "strong"}}},
		}
		questions_json = {
			"questions": [
				{
					"question": "Explain dependency injection.",
					"ideal_points": ["Loose coupling"],
					"follow_up": "Where is it useful?",
					"difficulty": "medium",
					"question_tier": "strong",
					"focus_skill": "Python",
				}
			],
		}
		created_round = {
			"status": "in_progress",
			"current_question_index": 0,
			"questions_json": questions_json,
		}

		with patch("backend.api.routes_interview._require_parent_session", return_value=parent_session), \
			 patch("backend.api.routes_interview.get_interview_round_session", return_value=None), \
			 patch("backend.api.routes_interview._get_parsed_resume", return_value={"skills": ["Python"]}), \
			 patch("backend.api.routes_interview._build_round_context", return_value=context), \
			 patch("backend.api.routes_interview.get_or_generate_questions", return_value=questions_json), \
			 patch("backend.api.routes_interview.create_interview_round_session", return_value=created_round) as mock_create_round:
			routes_interview.start_interview_round(request, self.current_user)

		persisted_questions_json = mock_create_round.call_args.kwargs["questions_json"]
		self.assertEqual(persisted_questions_json["skill_profile_summary"]["strong_count"], 2)
		self.assertEqual(persisted_questions_json["skill_profile_summary"]["absent_skills"], ["Git"])
		self.assertEqual(persisted_questions_json["covered_topics"], [])
		self.assertEqual(persisted_questions_json["covered_topic_summary"]["total_questions_covered"], 0)

	def test_submit_interview_answer_advances_question_index_and_persists_result(self) -> None:
		request = routes_interview.InterviewAnswerRequest(
			session_id="session-123",
			round="technical",
			question_index=0,
			answer_text="Hash maps give average O one lookup and handle collisions with chaining or probing.",
		)
		round_record = {
			"status": "in_progress",
			"current_question_index": 0,
			"state_version": 4,
			"difficulty_signal": 0.5,
			"questions_json": {
				"questions": [
					{
						"question": "Explain hash maps.",
						"ideal_points": ["Average O(1) lookup", "Collision handling"],
						"follow_up": "What happens during collisions?",
						"difficulty": "medium",
						"question_tier": "strong",
						"focus_skill": "Hash maps",
					},
					{
						"question": "Explain caching.",
						"ideal_points": ["Latency reduction"],
						"follow_up": "",
						"difficulty": "medium",
					},
				],
			},
		}
		evaluation = {
			"final_score": 0.8,
			"groq_score": 0.7,
			"sbert_score": 0.9,
			"communication_score": 0.6,
			"concept_coverage": {
				"coverage_ratio": 1.0,
				"covered_count": 2,
				"total_concepts": 2,
				"tfidf_score": 0.44,
				"concept_details": [
					{"point": "Average O(1) lookup", "covered": True, "similarity": 0.51},
					{"point": "Collision handling", "covered": True, "similarity": 0.37},
				],
				"available": True,
			},
			"scoring_profile": {
				"round_type": "technical",
				"coverage_threshold": 0.12,
				"weights": {
					"groq": 0.62,
					"sbert": 0.18,
					"concept_coverage": 0.14,
					"communication": 0.06,
				},
				"local_fallback_weights": {
					"sbert": 0.48,
					"concept_coverage": 0.37,
					"communication": 0.15,
				},
			},
			"rubric": {
				"score": 0.7,
				"relevance": 0.9,
				"depth": 0.8,
				"accuracy": 0.85,
				"feedback": "Good technical coverage.",
			},
			"communication": {
				"score": 0.6,
				"word_count": 14,
				"filler_ratio": 0.0,
				"vocabulary_diversity": 0.9,
					"sentiment_available": True,
					"sentiment_analysis_mode": "vader",
					"sentiment_compound": 0.4215,
					"sentiment_positive_ratio": 0.26,
					"sentiment_neutral_ratio": 0.69,
					"sentiment_negative_ratio": 0.05,
					"confidence_score": 0.78,
					"confidence_label": "confident",
					"sentiment_label": "positive",
			},
			"sbert_similarity": 0.9,
			"feedback": "Good technical coverage.",
			"evaluation_mode": "groq",
			"fallback_reason": None,
			"local_score": 0.75,
		}
		updated_round = {
			"status": "in_progress",
			"current_question_index": 1,
			"state_version": 5,
		}

		with patch("backend.api.routes_interview._require_parent_session", return_value={"role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_interview.get_interview_round_session", return_value=round_record), \
			 patch("backend.api.routes_interview.evaluate_answer", return_value=evaluation), \
			 patch("backend.api.routes_interview.save_interview_response", return_value={"id": "response-123"}) as mock_save_response, \
			 patch("backend.api.routes_interview.advance_interview_question", return_value=updated_round) as mock_advance_question:
			result = routes_interview.submit_interview_answer(request, self.current_user)

		self.assertEqual(result["question_index"], 0)
		self.assertEqual(result["next_question_index"], 1)
		self.assertFalse(result["is_last_question"])
		self.assertEqual(result["total_questions"], 2)
		self.assertEqual(result["round_session"], updated_round)

		persisted_payload = mock_save_response.call_args.args[0]
		self.assertEqual(persisted_payload["round"], "technical")
		self.assertEqual(persisted_payload["question_id"], "technical_0")
		self.assertEqual(persisted_payload["score"], 0.8)
		self.assertEqual(persisted_payload["dimension_scores"]["evaluation_mode"], "groq")
		self.assertEqual(persisted_payload["dimension_scores"]["concept_coverage"], evaluation["concept_coverage"])
		self.assertEqual(persisted_payload["dimension_scores"]["scoring_profile"], evaluation["scoring_profile"])
		self.assertEqual(persisted_payload["dimension_scores"]["communication_detail"]["confidence_score"], 0.78)
		self.assertEqual(persisted_payload["dimension_scores"]["communication_detail"]["sentiment_label"], "positive")
		mock_advance_question.assert_called_once_with(
			session_id="session-123",
			round="technical",
			new_index=1,
			difficulty_signal=0.62,
			questions_json=mock_advance_question.call_args.kwargs["questions_json"],
			expected_state_version=4,
		)
		advanced_questions_json = mock_advance_question.call_args.kwargs["questions_json"]
		self.assertEqual(advanced_questions_json["_response_history"][0]["id"], "response-123")
		self.assertEqual(advanced_questions_json["covered_topics"][0]["question_tier"], "strong")
		self.assertEqual(advanced_questions_json["covered_topics"][0]["focus_skill"], "Hash maps")
		self.assertEqual(advanced_questions_json["covered_topic_summary"]["tier_counts"]["strong"], 1)
		self.assertEqual(result["covered_topic_summary"]["total_questions_covered"], 1)
		self.assertEqual(persisted_payload["dimension_scores"]["targeting_detail"]["question_tier"], "strong")
		self.assertEqual(persisted_payload["dimension_scores"]["targeting_detail"]["focus_skill"], "Hash maps")

	def test_get_technical_targeting_analytics_summarizes_score_drop_and_hesitation_by_tier(self) -> None:
		technical_round = {
			"questions_json": {
				"questions": [
					{"question": "Explain Python internals.", "question_tier": "strong", "focus_skill": "Python"},
					{"question": "What problem does Git solve?", "question_tier": "absent", "focus_skill": "Git"},
				],
			},
		}
		responses_by_session = {
			"session-1": [
				{
					"question_id": "technical_0",
					"score": 0.84,
					"dimension_scores": {
						"communication_detail": {
							"confidence_score": 0.81,
							"confidence_label": "confident",
						},
					},
				},
				{
					"question_id": "technical_1",
					"score": 0.51,
					"dimension_scores": {
						"communication_detail": {
							"confidence_score": 0.42,
							"confidence_label": "hesitant",
						},
					},
				},
			],
			"session-2": [
				{
					"question_id": "technical_1",
					"score": 0.56,
					"dimension_scores": {
						"communication_detail": {
							"confidence_score": 0.48,
							"confidence_label": "hesitant",
						},
					},
				},
			],
		}

		with patch("backend.api.routes_interview._require_parent_session", return_value={"role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_interview.list_sessions_for_user", return_value=[
				{"id": "session-1", "role_selected": "backend_python_developer"},
				{"id": "session-2", "role_selected": "backend_python_developer"},
			 ]), \
			 patch("backend.api.routes_interview.get_interview_round_session", return_value=technical_round), \
			 patch("backend.api.routes_interview.list_interview_responses", side_effect=lambda session_id, round: responses_by_session.get(session_id, [])):
			result = routes_interview.get_technical_targeting_analytics("session-current", self.current_user)

		analytics = result["analytics"]
		self.assertEqual(analytics["scope"], "same_role_saved_sessions")
		self.assertEqual(analytics["sessions_analyzed"], 2)
		self.assertEqual(analytics["responses_analyzed"], 3)
		self.assertEqual(analytics["lowest_scoring_tier"]["tier"], "absent")
		self.assertEqual(analytics["highest_hesitation_tier"]["tier"], "absent")
		self.assertAlmostEqual(analytics["tier_stats"][0]["average_score"], 0.535, places=4)


class WebSocketInterviewTests(TestCase):
	def setUp(self) -> None:
		self.current_user = AuthenticatedUser(
			user_id="user-123",
			email="user@example.com",
			raw_user={"id": "user-123", "email": "user@example.com"},
		)

	def _receive_until_type(self, websocket, expected_type: str) -> dict[str, object]:
		for _ in range(12):
			payload = websocket.receive_json()
			if payload.get("type") == expected_type:
				return payload
		self.fail(f"Did not receive websocket payload of type '{expected_type}'.")

	def test_websocket_typed_answer_updates_live_covered_topics(self) -> None:
		round_questions_json = {
			"questions": [
				{
					"question": "Explain Python concurrency.",
					"ideal_points": ["Async IO helps with waiting tasks", "Threads can overlap blocking I/O"],
					"follow_up": "When would you choose threads over async?",
					"difficulty": "medium",
					"question_tier": "strong",
					"focus_skill": "Python",
				},
				{
					"question": "What problem does Git solve?",
					"ideal_points": ["Version control", "Collaboration history"],
					"follow_up": "Why does branching help?",
					"difficulty": "easy",
					"question_tier": "absent",
					"focus_skill": "Git",
				},
			],
		}
		round_record = {
			"status": "in_progress",
			"current_question_index": 0,
			"state_version": 4,
			"difficulty_signal": 0.5,
			"questions_json": round_questions_json,
		}
		evaluation = {
			"final_score": 0.82,
			"groq_score": 0.78,
			"sbert_score": 0.84,
			"communication_score": 0.69,
			"concept_coverage": {
				"coverage_ratio": 1.0,
				"covered_count": 2,
				"total_concepts": 2,
				"tfidf_score": 0.48,
				"concept_details": [
					{"point": "Async IO helps with waiting tasks", "covered": True, "similarity": 0.52},
					{"point": "Threads can overlap blocking I/O", "covered": True, "similarity": 0.43},
				],
				"available": True,
			},
			"scoring_profile": {
				"round_type": "technical",
				"coverage_threshold": 0.12,
				"weights": {
					"groq": 0.62,
					"sbert": 0.18,
					"concept_coverage": 0.14,
					"communication": 0.06,
				},
				"local_fallback_weights": {
					"sbert": 0.48,
					"concept_coverage": 0.37,
					"communication": 0.15,
				},
			},
			"rubric": {
				"score": 0.78,
				"relevance": 0.88,
				"depth": 0.8,
				"accuracy": 0.84,
				"feedback": "Clear explanation.",
			},
			"communication": {
				"score": 0.69,
				"word_count": 17,
				"filler_ratio": 0.0,
				"vocabulary_diversity": 0.83,
				"confidence_score": 0.74,
				"confidence_label": "confident",
				"sentiment_available": True,
				"sentiment_label": "positive",
			},
			"sbert_similarity": 0.84,
			"feedback": "Clear explanation.",
			"evaluation_mode": "groq",
			"fallback_reason": None,
			"local_score": 0.79,
		}

		def _advance_round(**kwargs):
			return {
				**round_record,
				"current_question_index": kwargs["new_index"],
				"state_version": 5,
				"difficulty_signal": kwargs["difficulty_signal"],
				"questions_json": kwargs["questions_json"],
			}

		with patch("backend.main.warmup_semantic_encoder"), \
			 patch("backend.api.ws_interview._authenticate_websocket_user", return_value=self.current_user), \
			 patch("backend.api.ws_interview._load_parent_session", return_value={
				 "id": "session-123",
				 "user_id": "user-123",
				 "role_selected": "backend_python_developer",
			 }), \
			 patch("backend.api.ws_interview.ensure_session_access"), \
			 patch("backend.api.ws_interview._load_or_create_round_session", return_value=(round_record, dict(round_questions_json))), \
			 patch("backend.api.ws_interview.synthesize", return_value=b""), \
			 patch("backend.api.ws_interview.evaluate_answer", return_value=evaluation), \
			 patch("backend.api.ws_interview.save_interview_response") as mock_save_response, \
			 patch("backend.api.ws_interview.advance_interview_question", side_effect=_advance_round) as mock_advance_question:
			app = create_app()
			with TestClient(app) as client:
				with client.websocket_connect("/interview/ws/session-123/technical?access_token=test-token") as websocket:
					connected_payload = self._receive_until_type(websocket, "connected")
					question_payload = self._receive_until_type(websocket, "question")

					self.assertEqual(connected_payload["round"], "technical")
					self.assertEqual(question_payload["text"], "Explain Python concurrency.")

					websocket.send_json({
						"type": "typed_answer",
						"text": "Async IO helps one event loop handle waiting tasks, while threads can overlap blocking I O work.",
					})

					feedback_payload = self._receive_until_type(websocket, "feedback")

		persisted_payload = mock_save_response.call_args.args[0]
		self.assertEqual(persisted_payload["round"], "technical")
		self.assertEqual(persisted_payload["question_id"], "technical_0")
		self.assertEqual(persisted_payload["dimension_scores"]["targeting_detail"]["question_tier"], "strong")
		self.assertEqual(persisted_payload["dimension_scores"]["targeting_detail"]["focus_skill"], "Python")

		advanced_questions_json = mock_advance_question.call_args.kwargs["questions_json"]
		self.assertEqual(advanced_questions_json["covered_topics"][0]["question_index"], 0)
		self.assertEqual(advanced_questions_json["covered_topics"][0]["question_tier"], "strong")
		self.assertEqual(advanced_questions_json["covered_topics"][0]["focus_skill"], "Python")
		self.assertEqual(advanced_questions_json["covered_topic_summary"]["tier_counts"]["strong"], 1)
		self.assertEqual(advanced_questions_json["covered_topic_summary"]["covered_focus_skills"], ["Python"])

		self.assertEqual(feedback_payload["next_question_index"], 1)
		self.assertFalse(feedback_payload["is_last_question"])
		self.assertEqual(
			feedback_payload["round_session"]["questions_json"]["covered_topic_summary"]["total_questions_covered"],
			1,
		)
		self.assertEqual(
			feedback_payload["round_session"]["questions_json"]["covered_topics"][0]["focus_skill"],
			"Python",
		)