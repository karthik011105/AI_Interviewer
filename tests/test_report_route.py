from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from backend.api.auth import AuthenticatedUser
from backend.api import routes_report
from backend.database.supabase_client import SupabaseClientError


class ReportRouteTests(TestCase):
	def setUp(self) -> None:
		self.current_user = AuthenticatedUser(
			user_id="user-123",
			email="candidate@example.com",
			raw_user={"id": "user-123", "email": "candidate@example.com"},
		)

	def _complete_round_record(
		self,
		round_name: str,
		total_score: float,
		*,
		extra_questions_json: dict[str, object] | None = None,
	) -> dict[str, object]:
		questions_json = {"questions": [{"question": "Q1"}, {"question": "Q2"}]}
		if round_name == "project_discussion":
			questions_json = {"projects": [{"questions": [{"question": "P1"}, {"question": "P2"}]}]}
		if isinstance(extra_questions_json, dict):
			questions_json = {**questions_json, **extra_questions_json}
		return {
			"status": "complete",
			"questions_json": questions_json,
			"current_question_index": 2,
			"completed_at": "2026-05-10T09:00:00+00:00",
			"total_score": total_score,
		}

	def _concept_coverage(self, ratio: float, *, threshold: float, covered_count: int, total_concepts: int, missing_point: str | None = None) -> dict[str, object]:
		details = [
			{"point": "Hash map", "covered": True, "similarity": 0.41},
		]
		if missing_point:
			details.append({"point": missing_point, "covered": False, "similarity": 0.0})
		return {
			"coverage_ratio": ratio,
			"covered_count": covered_count,
			"total_concepts": total_concepts,
			"tfidf_score": ratio,
			"concept_details": details,
			"threshold": threshold,
			"available": True,
		}

	def _scoring_profile(self, round_name: str, threshold: float) -> dict[str, object]:
		return {
			"round_type": round_name,
			"coverage_threshold": threshold,
			"weights": {
				"groq": 0.62 if round_name != "hr" else 0.68,
				"sbert": 0.18 if round_name != "hr" else 0.14,
				"concept_coverage": 0.14 if round_name == "technical" else 0.06,
				"communication": 0.06 if round_name == "technical" else 0.12,
			},
			"local_fallback_weights": {
				"sbert": 0.48,
				"concept_coverage": 0.37,
				"communication": 0.15,
			},
		}

	def _communication_detail(
		self,
		*,
		confidence_score: float,
		confidence_label: str,
		sentiment_compound: float,
		sentiment_label: str,
		hedging_ratio: float,
		filler_ratio: float,
		analysis_mode: str = "vader",
		sentiment_available: bool = True,
	) -> dict[str, object]:
		return {
			"score": round(0.55 + confidence_score * 0.2, 4),
			"word_count": 28,
			"filler_ratio": filler_ratio,
			"vocabulary_diversity": 0.72,
			"hedging_ratio": hedging_ratio,
			"sentiment_available": sentiment_available,
			"sentiment_analysis_mode": analysis_mode,
			"sentiment_compound": sentiment_compound,
			"sentiment_positive_ratio": 0.2,
			"sentiment_neutral_ratio": 0.7,
			"sentiment_negative_ratio": 0.1,
			"confidence_score": confidence_score,
			"confidence_label": confidence_label,
			"sentiment_label": sentiment_label,
		}

	def _response(
		self,
		score: float,
		feedback: str,
		*,
		groq: float = 0.0,
		sbert: float = 0.0,
		communication: float = 0.0,
		concept_coverage: dict[str, object] | None = None,
		scoring_profile: dict[str, object] | None = None,
		communication_detail: dict[str, object] | None = None,
	) -> dict[str, object]:
		dimension_scores: dict[str, object] = {}
		if concept_coverage is not None:
			dimension_scores["concept_coverage"] = concept_coverage
		if scoring_profile is not None:
			dimension_scores["scoring_profile"] = scoring_profile
		if communication_detail is not None:
			dimension_scores["communication_detail"] = communication_detail
		return {
			"score": score,
			"groq_score": groq or score,
			"sbert_score": sbert or score,
			"communication_score": communication or score,
			"evaluation_mode": "groq",
			"dimension_scores": dimension_scores,
			"feedback": feedback,
		}

	def _complete_dsa_records(self) -> list[dict[str, object]]:
		return [
			{
				"question_number": 1,
				"stage": "complete",
				"completed_at": "2026-05-10T09:00:00+00:00",
				"total_score": 0.8,
				"state_json": {"debrief_messages": ["done"]},
			},
			{
				"question_number": 2,
				"stage": "complete",
				"completed_at": "2026-05-10T09:15:00+00:00",
				"total_score": 0.9,
				"state_json": {"debrief_messages": ["done"]},
			},
		]

	def test_get_report_session_returns_snapshot_when_final_reports_table_is_missing(self) -> None:
		technical_questions_json = {
			"skill_profile_summary": {
				"strong_count": 2,
				"familiar_count": 1,
				"mentioned_count": 0,
				"absent_count": 2,
				"soft_gap_count": 1,
				"strong_skills": ["Python", "FastAPI"],
				"familiar_skills": ["SQL"],
				"mentioned_skills": [],
				"absent_skills": ["Git", "PostgreSQL"],
				"soft_gap_skills": ["PostgreSQL"],
				"priority_focus_areas": ["Git", "PostgreSQL", "SQL", "Python"],
			},
			"covered_topics": [
				{"question_index": 0, "question_tier": "strong", "focus_skill": "Python", "question_text": "Q1"},
				{"question_index": 1, "question_tier": "absent", "focus_skill": "Git", "question_text": "Q2"},
			],
			"covered_topic_summary": {
				"total_questions_covered": 2,
				"tier_counts": {"strong": 1, "familiar": 0, "mentioned": 0, "absent": 1, "general": 0},
				"skills_by_tier": {"strong": ["Python"], "familiar": [], "mentioned": [], "absent": ["Git"], "general": []},
				"covered_focus_skills": ["Git", "Python"],
			},
		}

		with patch("backend.api.routes_report._require_parent_session", return_value={"status": "hr_complete", "role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_report.get_final_report", side_effect=SupabaseClientError("Supabase table 'final_reports' is missing. Apply the required schema or enable the compatibility fallback.")), \
			 patch("backend.api.routes_report.get_assessment_session", return_value={
				 "status": "completed",
				 "total_questions": 8,
				 "answered_count": 8,
				 "correct_count": 6,
				 "score_percent": 75.0,
				 "completed_at": "2026-05-10T08:30:00+00:00",
			 }), \
			 patch("backend.api.routes_report.get_interview_round_session", side_effect=[
				 self._complete_round_record("technical", 0.72, extra_questions_json=technical_questions_json),
				 self._complete_round_record("project_discussion", 0.81),
				 self._complete_round_record("hr", 0.77),
			 ]), \
			 patch("backend.api.routes_report.list_interview_responses", side_effect=[
				 [
					 self._response(0.7, "Clear explanation.", concept_coverage=self._concept_coverage(0.5, threshold=0.12, covered_count=1, total_concepts=2, missing_point="Collision handling"), scoring_profile=self._scoring_profile("technical", 0.12), communication_detail=self._communication_detail(confidence_score=0.78, confidence_label="confident", sentiment_compound=0.32, sentiment_label="positive", hedging_ratio=0.02, filler_ratio=0.01)),
					 self._response(0.74, "Good structure.", concept_coverage=self._concept_coverage(1.0, threshold=0.12, covered_count=2, total_concepts=2), scoring_profile=self._scoring_profile("technical", 0.12), communication_detail=self._communication_detail(confidence_score=0.64, confidence_label="steady", sentiment_compound=0.08, sentiment_label="neutral", hedging_ratio=0.06, filler_ratio=0.02)),
				 ],
				 [
					 self._response(0.81, "Strong project depth.", communication_detail=self._communication_detail(confidence_score=0.69, confidence_label="steady", sentiment_compound=0.11, sentiment_label="neutral", hedging_ratio=0.04, filler_ratio=0.01)),
					 self._response(0.8, "Good tradeoff framing.", communication_detail=self._communication_detail(confidence_score=0.72, confidence_label="confident", sentiment_compound=0.19, sentiment_label="positive", hedging_ratio=0.03, filler_ratio=0.01)),
				 ],
				 [
					 self._response(0.77, "Calm and concise.", concept_coverage=self._concept_coverage(0.5, threshold=0.08, covered_count=1, total_concepts=2, missing_point="Concrete ownership example"), scoring_profile=self._scoring_profile("hr", 0.08), communication_detail=self._communication_detail(confidence_score=0.58, confidence_label="steady", sentiment_compound=0.04, sentiment_label="neutral", hedging_ratio=0.08, filler_ratio=0.03)),
					 self._response(0.76, "Good motivation answer.", concept_coverage=self._concept_coverage(0.5, threshold=0.08, covered_count=1, total_concepts=2), scoring_profile=self._scoring_profile("hr", 0.08), communication_detail=self._communication_detail(confidence_score=0.41, confidence_label="hesitant", sentiment_compound=-0.06, sentiment_label="neutral", hedging_ratio=0.14, filler_ratio=0.05, analysis_mode="heuristic_fallback", sentiment_available=False)),
				 ],
			 ]), \
			 patch("backend.api.routes_report.list_dsa_sessions", return_value=self._complete_dsa_records()):
			result = routes_report.get_report_session("session-123", self.current_user)

		self.assertFalse(result["report_exists"])
		self.assertFalse(result["persistence_supported"])
		snapshot = result["snapshot"]
		self.assertAlmostEqual(snapshot["round_scores"]["assessment"], 0.75, places=4)
		self.assertAlmostEqual(snapshot["round_scores"]["dsa"], 0.855, places=4)
		self.assertEqual(snapshot["report_json"]["technical"]["status"], "complete")
		self.assertAlmostEqual(snapshot["dimension_scores"]["technical"]["concept_coverage"]["average_ratio"], 0.75, places=4)
		self.assertAlmostEqual(snapshot["dimension_scores"]["technical"]["confidence_signal"]["average_confidence"], 0.71, places=4)
		self.assertEqual(snapshot["dimension_scores"]["technical"]["skill_profile_summary"]["strong_count"], 2)
		self.assertEqual(snapshot["dimension_scores"]["technical"]["covered_topic_summary"]["tier_counts"]["absent"], 1)
		self.assertEqual(snapshot["report_json"]["technical"]["covered_topics"][1]["focus_skill"], "Git")
		self.assertEqual(snapshot["report_json"]["technical"]["confidence_signal"]["dominant_confidence_label"], "steady")
		self.assertEqual(snapshot["report_json"]["technical"]["concept_coverage"]["sample_missing_points"], ["Collision handling"])
		self.assertEqual(snapshot["dimension_scores"]["hr"]["concept_coverage"]["threshold"], 0.08)
		self.assertEqual(snapshot["dimension_scores"]["hr"]["confidence_signal"]["analysis_modes"], ["heuristic_fallback", "vader"])
		self.assertEqual(len(snapshot["report_json"]["round_summaries"]), 5)

	def test_generate_report_session_persists_computed_report(self) -> None:
		technical_questions_json = {
			"skill_profile_summary": {
				"strong_count": 2,
				"familiar_count": 1,
				"mentioned_count": 0,
				"absent_count": 1,
				"soft_gap_count": 0,
				"strong_skills": ["Python", "FastAPI"],
				"familiar_skills": ["SQL"],
				"mentioned_skills": [],
				"absent_skills": ["Git"],
				"soft_gap_skills": [],
				"priority_focus_areas": ["Git", "SQL", "Python"],
			},
			"covered_topics": [
				{"question_index": 0, "question_tier": "strong", "focus_skill": "Python", "question_text": "Q1"},
			],
			"covered_topic_summary": {
				"total_questions_covered": 1,
				"tier_counts": {"strong": 1, "familiar": 0, "mentioned": 0, "absent": 0, "general": 0},
				"skills_by_tier": {"strong": ["Python"], "familiar": [], "mentioned": [], "absent": [], "general": []},
				"covered_focus_skills": ["Python"],
			},
		}

		def _persisted_report(**kwargs):
			return {
				"session_id": kwargs["session_id"],
				"overall_score": kwargs["overall_score"],
				"round_scores": kwargs["round_scores"],
				"dimension_scores": kwargs["dimension_scores"],
				"report_json": kwargs["report_json"],
			}

		with patch("backend.api.routes_report._require_parent_session", return_value={"status": "hr_complete", "role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_report.get_final_report", return_value=None), \
			 patch("backend.api.routes_report.get_assessment_session", return_value={
				 "status": "completed",
				 "total_questions": 8,
				 "answered_count": 8,
				 "correct_count": 7,
				 "score_percent": 87.5,
				 "completed_at": "2026-05-10T08:30:00+00:00",
			 }), \
			 patch("backend.api.routes_report.get_interview_round_session", side_effect=[
				 self._complete_round_record("technical", 0.82, extra_questions_json=technical_questions_json),
				 self._complete_round_record("project_discussion", 0.79),
				 self._complete_round_record("hr", 0.76),
			 ]), \
			 patch("backend.api.routes_report.list_interview_responses", side_effect=[
				 [self._response(0.82, "Strong technical explanations.", communication_detail=self._communication_detail(confidence_score=0.74, confidence_label="confident", sentiment_compound=0.28, sentiment_label="positive", hedging_ratio=0.02, filler_ratio=0.01))],
				 [self._response(0.79, "Good project ownership.", communication_detail=self._communication_detail(confidence_score=0.66, confidence_label="steady", sentiment_compound=0.12, sentiment_label="neutral", hedging_ratio=0.04, filler_ratio=0.02))],
				 [self._response(0.76, "Clear HR reasoning.", communication_detail=self._communication_detail(confidence_score=0.52, confidence_label="steady", sentiment_compound=0.03, sentiment_label="neutral", hedging_ratio=0.09, filler_ratio=0.03))],
			 ]), \
			 patch("backend.api.routes_report.list_dsa_sessions", return_value=self._complete_dsa_records()), \
			 patch("backend.api.routes_report.upsert_final_report", side_effect=_persisted_report) as mock_upsert:
			result = routes_report.generate_report_session("session-123", self.current_user)

		self.assertTrue(result["persisted"])
		self.assertTrue(result["persistence_supported"])
		self.assertIn("assessment", result["report"]["round_scores"])
		self.assertIn("technical", result["report"]["round_scores"])
		self.assertIn("dsa", result["report"]["round_scores"])
		self.assertEqual(result["report"]["report_json"]["overview"]["session_status"], "hr_complete")
		self.assertAlmostEqual(result["report"]["report_json"]["technical"]["confidence_signal"]["average_confidence"], 0.74, places=4)
		self.assertEqual(result["report"]["report_json"]["technical"]["skill_profile_summary"]["strong_skills"], ["Python", "FastAPI"])
		self.assertEqual(result["report"]["dimension_scores"]["technical"]["covered_topic_summary"]["covered_focus_skills"], ["Python"])
		mock_upsert.assert_called_once()

	def test_report_snapshot_counts_q1_debrief_with_passed_hidden_tests_as_complete(self) -> None:
		q1_debrief_q2_complete = [
			{
				"question_number": 1,
				"stage": "debrief",
				"execution_results": {"passed_count": 4, "total_count": 4},
				"state_json": {},
			},
			{
				"question_number": 2,
				"stage": "complete",
				"completed_at": "2026-05-10T09:15:00+00:00",
				"total_score": 0.9,
				"execution_results": {"passed_count": 5, "total_count": 5},
				"state_json": {"debrief_messages": ["done"]},
			},
		]

		with patch("backend.api.routes_report._require_parent_session", return_value={"status": "hr_complete", "role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_report.get_final_report", side_effect=SupabaseClientError("Supabase table 'final_reports' is missing. Apply the required schema or enable the compatibility fallback.")), \
			 patch("backend.api.routes_report.get_assessment_session", return_value=None), \
			 patch("backend.api.routes_report.get_interview_round_session", side_effect=[None, None, None]), \
			 patch("backend.api.routes_report.list_interview_responses", side_effect=[[], [], []]), \
			 patch("backend.api.routes_report.list_dsa_sessions", return_value=q1_debrief_q2_complete):
			result = routes_report.get_report_session("session-123", self.current_user)

		snapshot = result["snapshot"]
		self.assertEqual(snapshot["report_json"]["dsa"]["status"], "complete")
		self.assertEqual(snapshot["report_json"]["dsa"]["completed_question_count"], 2)
		self.assertEqual(len(snapshot["report_json"]["dsa"]["question_reports"]), 2)
		self.assertAlmostEqual(snapshot["round_scores"]["dsa"], 0.945, places=4)

	def test_report_snapshot_uses_progress_fallback_when_interview_answers_are_missing(self) -> None:
		technical_record = {
			"status": "complete",
			"questions_json": {
				"questions": [{"question": f"Q{index}"} for index in range(1, 7)],
				"covered_topics": [
					{"question_index": index, "question_tier": "general", "question_text": f"Q{index + 1}"}
					for index in range(6)
				],
				"covered_topic_summary": {
					"total_questions_covered": 6,
					"tier_counts": {"strong": 0, "familiar": 0, "mentioned": 0, "absent": 0, "general": 6},
				},
			},
			"current_question_index": 6,
			"completed_at": "2026-05-10T09:00:00+00:00",
			"total_score": 0.0,
		}

		with patch("backend.api.routes_report._require_parent_session", return_value={"status": "hr_complete", "role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_report.get_final_report", side_effect=SupabaseClientError("Supabase table 'final_reports' is missing. Apply the required schema or enable the compatibility fallback.")), \
			 patch("backend.api.routes_report.get_assessment_session", return_value=None), \
			 patch("backend.api.routes_report.get_interview_round_session", side_effect=[technical_record, None, None]), \
			 patch("backend.api.routes_report.list_interview_responses", side_effect=[[], [], []]), \
			 patch("backend.api.routes_report.list_dsa_sessions", return_value=[]):
			result = routes_report.get_report_session("session-123", self.current_user)

		technical = result["snapshot"]["report_json"]["technical"]
		self.assertEqual(technical["metrics"]["response_count"], 6)
		self.assertEqual(technical["metrics"]["response_count_source"], "progress_fallback")
		self.assertIsNone(technical["score"])
		self.assertIn("prompts completed", technical["summary"])
		self.assertIn("not fully preserved", technical["detail"])

	def test_report_snapshot_rebuilds_stale_dsa_question_report(self) -> None:
		dsa_records = [
			{
				"question_number": 1,
				"problem_id": "problem-1",
				"stage": "complete",
				"completed_at": "2026-05-10T09:00:00+00:00",
				"total_score": 0.84,
				"last_judge_status": "ac",
				"all_code_submissions": [{"submission_id": "sub-1"}],
				"state_json": {"debrief_messages": ["done"], "current_language": "python", "candidate_model": {"code_handles_edge_cases": True}},
				"dimension_scores": {
					"approach_quality": 0.78,
					"code_correctness": 1.0,
					"complexity_awareness": 0.72,
					"followup_depth": 0.68,
					"communication": 0.64,
					"analysis": {"optimality_guess": "yes"},
					"candidate_model": {"code_handles_edge_cases": True},
					"question_report": {
						"question_number": 1,
						"problem_title": "DSAQuestion1",
						"analysis_summary": "sorting",
						"score": 0.0,
						"dimension_scores": {
							"approach_quality": 0.0,
							"code_correctness": 0.0,
							"complexity_awareness": 0.0,
							"followup_depth": 0.0,
							"communication": 0.0,
						},
					},
				},
			},
			{
				"question_number": 2,
				"problem_id": "problem-2",
				"stage": "complete",
				"completed_at": "2026-05-10T09:15:00+00:00",
				"total_score": 0.9,
				"last_judge_status": "ac",
				"all_code_submissions": [{"submission_id": "sub-2"}],
				"state_json": {"debrief_messages": ["done"], "current_language": "python", "candidate_model": {"code_handles_edge_cases": True}},
				"dimension_scores": {
					"approach_quality": 0.81,
					"code_correctness": 1.0,
					"complexity_awareness": 0.79,
					"followup_depth": 0.74,
					"communication": 0.7,
					"analysis": {"optimality_guess": "yes"},
					"candidate_model": {"code_handles_edge_cases": True},
				},
			},
		]

		with patch("backend.api.routes_report._require_parent_session", return_value={"status": "hr_complete", "role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_report.get_final_report", side_effect=SupabaseClientError("Supabase table 'final_reports' is missing. Apply the required schema or enable the compatibility fallback.")), \
			 patch("backend.api.routes_report.get_assessment_session", return_value=None), \
			 patch("backend.api.routes_report.get_interview_round_session", side_effect=[None, None, None]), \
			 patch("backend.api.routes_report.list_interview_responses", side_effect=[[], [], []]), \
			 patch("backend.api.routes_report.list_dsa_sessions", return_value=dsa_records):
			result = routes_report.get_report_session("session-123", self.current_user)

		question_reports = result["snapshot"]["report_json"]["dsa"]["question_reports"]
		self.assertEqual(question_reports[0]["question_number"], 1)
		self.assertEqual(question_reports[0]["problem_title"], "DSAQuestion1")
		self.assertEqual(question_reports[0]["analysis_summary"], "sorting")
		self.assertAlmostEqual(question_reports[0]["score"], 0.84, places=4)
		self.assertAlmostEqual(question_reports[0]["dimension_scores"]["approach_quality"], 0.78, places=4)
		self.assertEqual(question_reports[0]["coding_journey"]["submission_count"], 1)

	def test_report_snapshot_keeps_completed_q1_metrics_before_q2_starts(self) -> None:
		dsa_records = [
			{
				"question_number": 1,
				"problem_id": "problem-1",
				"stage": "complete",
				"completed_at": "2026-05-10T09:00:00+00:00",
				"total_score": 1.0,
				"last_judge_status": "ac",
				"all_code_submissions": [{"submission_id": "sub-1"}, {"submission_id": "sub-2"}],
				"state_json": {"debrief_messages": ["done"], "current_language": "python", "candidate_model": {"code_handles_edge_cases": True}},
				"dimension_scores": {
					"approach_quality": 0.92,
					"code_correctness": 1.0,
					"complexity_awareness": 0.88,
					"followup_depth": 0.79,
					"communication": 0.74,
					"analysis": {"optimality_guess": "yes"},
					"candidate_model": {"code_handles_edge_cases": True},
					"question_report": {
						"question_number": 1,
						"problem_title": "DSAQuestion1",
						"analysis_summary": "sorting",
						"score": 0.0,
						"dimension_scores": {
							"approach_quality": 0.0,
							"code_correctness": 0.0,
							"complexity_awareness": 0.0,
							"followup_depth": 0.0,
							"communication": 0.0,
						},
					},
				},
			},
		]

		with patch("backend.api.routes_report._require_parent_session", return_value={"status": "dsa_active", "role_selected": "backend_python_developer"}), \
			 patch("backend.api.routes_report.get_final_report", side_effect=SupabaseClientError("Supabase table 'final_reports' is missing. Apply the required schema or enable the compatibility fallback.")), \
			 patch("backend.api.routes_report.get_assessment_session", return_value=None), \
			 patch("backend.api.routes_report.get_interview_round_session", side_effect=[None, None, None]), \
			 patch("backend.api.routes_report.list_interview_responses", side_effect=[[], [], []]), \
			 patch("backend.api.routes_report.list_dsa_sessions", return_value=dsa_records):
			result = routes_report.get_report_session("session-123", self.current_user)

		dsa_section = result["snapshot"]["report_json"]["dsa"]
		self.assertEqual(dsa_section["status"], "in_progress")
		self.assertEqual(dsa_section["completed_question_count"], 1)
		self.assertIsNone(dsa_section["score"])
		self.assertEqual(len(dsa_section["question_reports"]), 1)
		self.assertEqual(dsa_section["question_reports"][0]["problem_title"], "DSAQuestion1")
		self.assertAlmostEqual(dsa_section["question_reports"][0]["score"], 1.0, places=4)
		self.assertAlmostEqual(dsa_section["question_reports"][0]["dimension_scores"]["approach_quality"], 0.92, places=4)