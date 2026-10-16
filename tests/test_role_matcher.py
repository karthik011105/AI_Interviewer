from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from backend.nlp import role_matcher
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

class SemanticEncoderStartupPolicyTests(TestCase):
	"""A missing semantic encoder must not be a silent downgrade.

	Role matching falls back to token overlap when the sentence-transformers
	encoder cannot load. The fallback is deliberate — it keeps local
	development and CI working with no model download — but it produces
	materially worse matches, and a deployment that switches to it looks
	completely healthy: the process starts, ``/health`` returns 200, and only
	``semantic_matching.active_backend`` says otherwise.

	``backend/Dockerfile`` sets ``HF_HUB_OFFLINE=1`` and used to claim that
	changing the model name without updating the pre-fetch step meant "startup
	will fail rather than silently download". Verified against a real offline
	load: it did not fail. ``warmup_semantic_encoder()`` returned False,
	``create_app()`` succeeded, and the app served degraded matches. These tests
	pin the two behaviours that replaced that.
	"""

	def setUp(self) -> None:
		role_matcher.get_semantic_encoder.cache_clear()

	def tearDown(self) -> None:
		role_matcher.get_semantic_encoder.cache_clear()

	def test_a_missing_encoder_is_permitted_by_default(self) -> None:
		"""The default must stay permissive so local runs and CI are unchanged."""

		with patch.dict(os.environ, {}, clear=False):
			os.environ.pop("REQUIRE_SEMANTIC_ENCODER", None)
			with patch.object(role_matcher, "get_semantic_encoder", return_value=None):
				self.assertFalse(role_matcher.warmup_semantic_encoder())

	def test_a_missing_encoder_is_logged_as_a_warning_naming_the_consequence(self) -> None:
		"""If it is going to be permitted, the log has to say what was lost —
		otherwise the only evidence is a field in /health nobody reads."""

		with patch.dict(os.environ, {}, clear=False):
			os.environ.pop("REQUIRE_SEMANTIC_ENCODER", None)
			with patch.object(role_matcher, "get_semantic_encoder", return_value=None):
				with self.assertLogs(role_matcher._LOGGER, level="WARNING") as captured:
					role_matcher.warmup_semantic_encoder()

		message = "\n".join(captured.output)
		self.assertIn("token-overlap", message)
		self.assertIn("REQUIRE_SEMANTIC_ENCODER", message)

	def test_strict_mode_turns_a_missing_encoder_into_a_startup_failure(self) -> None:
		with patch.dict(os.environ, {"REQUIRE_SEMANTIC_ENCODER": "true"}, clear=False):
			with patch.object(role_matcher, "get_semantic_encoder", return_value=None):
				with self.assertRaises(role_matcher.SemanticEncoderUnavailableError) as context:
					role_matcher.warmup_semantic_encoder()

		# The message has to be actionable: it is the only thing an operator
		# sees when a container refuses to start.
		detail = str(context.exception)
		self.assertIn("REQUIRE_SEMANTIC_ENCODER", detail)
		self.assertIn("token overlap", detail)

	def test_strict_mode_is_satisfied_by_a_working_encoder(self) -> None:
		"""Strict mode must not fail when the encoder is fine, or the flag
		would make every deployment unbootable."""

		with patch.dict(os.environ, {"REQUIRE_SEMANTIC_ENCODER": "true"}, clear=False):
			with patch.object(
				role_matcher, "get_semantic_encoder", return_value=object()
			):
				self.assertTrue(role_matcher.warmup_semantic_encoder())

	def test_the_flag_accepts_the_usual_truthy_spellings(self) -> None:
		for value, expected in (
			("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
			("false", False), ("0", False), ("", False), ("no", False),
		):
			with self.subTest(value=value):
				self.assertEqual(
					role_matcher.semantic_encoder_required({"REQUIRE_SEMANTIC_ENCODER": value}),
					expected,
				)

	def test_the_flag_is_absent_by_default(self) -> None:
		self.assertFalse(role_matcher.semantic_encoder_required({}))
