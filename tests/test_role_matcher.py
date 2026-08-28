from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from backend.nlp.role_matcher import RoleMatcher, get_semantic_backend_status


class RoleMatcherBm25Tests(TestCase):
	def setUp(self) -> None:
		self.parsed_resume = {
			"summary": "Built backend APIs with Python and FastAPI for authentication workflows.",
			"skills": ["Python", "FastAPI", "SQL", "Git"],
			"technologies": ["Docker"],
			"projects": [
				{
					"title": "Auth API",
					"description": "Built backend REST API with Python FastAPI and SQL database auth flows.",
					"tech_stack": ["Python", "FastAPI", "SQL"],
					"outcomes": ["API deployment"],
					"role": "Implemented backend services",
				}
			],
		}
		self.role_profiles = [
			{
				"key": "backend_python_developer",
				"title": "Backend Python Developer",
				"description": "Builds APIs, backend services, and data-backed application flows.",
				"required_skills": ["python", "sql", "git", "fastapi"],
				"bonus_skills": ["docker"],
				"project_signals": ["backend", "api"],
			},
			{
				"key": "data_analyst",
				"title": "Data Analyst",
				"description": "Analyzes datasets, builds reports, and communicates quantitative insights.",
				"required_skills": ["python", "pandas", "matplotlib", "sql"],
				"bonus_skills": ["power bi"],
				"project_signals": ["analysis", "dashboard"],
			},
		]

	def test_rank_roles_includes_bm25_signal_in_score_breakdown(self) -> None:
		with patch("backend.nlp.role_matcher.get_semantic_encoder", return_value=None), \
			 patch("backend.nlp.role_matcher._load_bm25_okapi", return_value=None):
			result = RoleMatcher(role_profiles=self.role_profiles).rank_roles(
				session_id="session-123",
				parsed_resume=self.parsed_resume,
				use_groq_profiles=False,
			)

		self.assertEqual(result.matches[0].role_key, "backend_python_developer")
		self.assertIn("bm25_match", result.matches[0].score_breakdown)
		self.assertGreater(
			result.matches[0].score_breakdown["bm25_match"],
			result.matches[1].score_breakdown["bm25_match"],
		)
		self.assertEqual(result.matches[0].score_breakdown["bm25_backend"], "bm25_builtin")
		self.assertEqual(result.matches[0].score_breakdown["semantic_backend"], "token_overlap")

	def test_backend_status_reports_bm25_availability(self) -> None:
		with patch("backend.nlp.role_matcher.get_semantic_encoder", return_value=None), \
			 patch("backend.nlp.role_matcher._load_bm25_okapi", return_value=None):
			status = get_semantic_backend_status()

		self.assertFalse(status["sbert_ready"])
		self.assertEqual(status["active_backend"], "token_overlap")
		self.assertTrue(status["bm25_enabled"])
		self.assertEqual(status["bm25_backend"], "bm25_builtin")
		self.assertFalse(status["bm25_library_available"])