"""Per-user spend quota tests.

These guard the routes that cost real money: Groq-backed resume parsing and role
matching, Judge0 code execution, and speech synthesis/transcription.
"""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from backend import config
from backend.api import quotas
from backend.api.quotas import (
	DSA_EXECUTION,
	RESUME_PARSE,
	ROLE_MATCH,
	VOICE,
	enforce_quota,
	reset_quotas,
)


class _QuotaTestBase(TestCase):
	env_overrides: dict[str, str] = {}

	def setUp(self) -> None:
		overrides = {
			"AUTH_JWT_SECRET": "quota-test-secret-padded-to-32-bytes-min",
			"QUOTA_RESUME_PARSE_PER_HOUR": "10",
			"QUOTA_ROLE_MATCH_PER_HOUR": "20",
			"QUOTA_DSA_EXECUTION_PER_HOUR": "60",
			"QUOTA_VOICE_PER_HOUR": "200",
			"QUOTA_WINDOW_SECONDS": "3600",
			**self.env_overrides,
		}
		self._env_patcher = patch.dict(os.environ, overrides, clear=False)
		self._env_patcher.start()
		config.reset_settings()
		reset_quotas()

	def tearDown(self) -> None:
		self._env_patcher.stop()
		config.reset_settings()
		reset_quotas()


class QuotaEnforcementTests(_QuotaTestBase):
	env_overrides = {"QUOTA_RESUME_PARSE_PER_HOUR": "3"}

	def test_allows_up_to_the_limit_then_returns_429(self) -> None:
		for _ in range(3):
			enforce_quota(RESUME_PARSE, "user-1")

		with self.assertRaises(HTTPException) as context:
			enforce_quota(RESUME_PARSE, "user-1")

		self.assertEqual(context.exception.status_code, 429)
		self.assertIn("Retry-After", context.exception.headers)
		self.assertGreaterEqual(int(context.exception.headers["Retry-After"]), 1)

	def test_quota_is_per_user_not_global(self) -> None:
		"""One user exhausting their allowance must not affect anyone else."""
		for _ in range(3):
			enforce_quota(RESUME_PARSE, "user-1")

		with self.assertRaises(HTTPException):
			enforce_quota(RESUME_PARSE, "user-1")

		# A different account still has its own untouched budget.
		enforce_quota(RESUME_PARSE, "user-2")

	def test_quotas_do_not_share_a_budget(self) -> None:
		"""Exhausting one quota must not consume another."""
		for _ in range(3):
			enforce_quota(RESUME_PARSE, "user-1")

		with self.assertRaises(HTTPException):
			enforce_quota(RESUME_PARSE, "user-1")

		# Different quota names are tracked independently.
		enforce_quota(ROLE_MATCH, "user-1")
		enforce_quota(DSA_EXECUTION, "user-1")
		enforce_quota(VOICE, "user-1")

	def test_rejects_an_unknown_quota_name(self) -> None:
		with self.assertRaises(ValueError):
			enforce_quota("not_a_real_quota", "user-1")


class QuotaConfigurationTests(_QuotaTestBase):
	def test_defaults_are_loaded_from_settings(self) -> None:
		quota_settings = config.get_settings().quotas

		self.assertEqual(quota_settings.resume_parse_max_per_hour, 10)
		self.assertEqual(quota_settings.role_match_max_per_hour, 20)
		self.assertEqual(quota_settings.dsa_execution_max_per_hour, 60)
		self.assertEqual(quota_settings.voice_max_per_hour, 200)
		self.assertEqual(quota_settings.quota_window_seconds, 3600)

	def test_limits_are_configurable(self) -> None:
		with patch.dict(os.environ, {"QUOTA_DSA_EXECUTION_PER_HOUR": "5"}, clear=False):
			config.reset_settings()
			reset_quotas()

			for _ in range(5):
				enforce_quota(DSA_EXECUTION, "user-1")

			with self.assertRaises(HTTPException) as context:
				enforce_quota(DSA_EXECUTION, "user-1")

		self.assertEqual(context.exception.status_code, 429)

	def test_rejects_a_nonsensical_limit(self) -> None:
		with patch.dict(os.environ, {"QUOTA_DSA_EXECUTION_PER_HOUR": "0"}, clear=False):
			config.reset_settings()
			with self.assertRaises(config.ConfigurationError):
				config.get_settings()


class QuotaDependencyTests(_QuotaTestBase):
	env_overrides = {"QUOTA_DSA_EXECUTION_PER_HOUR": "2"}

	def test_dependency_meters_and_returns_the_user(self) -> None:
		from backend.api.auth import AuthenticatedUser

		user = AuthenticatedUser(user_id="user-1", email="u@example.com", raw_user={})
		dependency = quotas.quota_dependency(DSA_EXECUTION)

		# The dependency passes the authenticated user through unchanged...
		self.assertIs(dependency(user), user)
		self.assertIs(dependency(user), user)

		# ...and starts rejecting once the allowance is spent.
		with self.assertRaises(HTTPException) as context:
			dependency(user)

		self.assertEqual(context.exception.status_code, 429)

	def test_expensive_routes_are_actually_metered(self) -> None:
		"""Guards against a route silently losing its quota in a refactor."""
		import inspect

		from backend.api import routes_dsa, routes_resume, routes_voice

		# (route function, the identifier it should pass to quota_dependency)
		expected = [
			(routes_resume.parse_resume_upload, "RESUME_PARSE"),
			(routes_resume.match_roles, "ROLE_MATCH"),
			(routes_dsa.run_dsa_code, "DSA_EXECUTION"),
			(routes_dsa.submit_dsa_code, "DSA_EXECUTION"),
			(routes_voice.text_to_speech, "VOICE"),
			(routes_voice.speech_to_text, "VOICE"),
		]

		for route, quota_identifier in expected:
			source = inspect.getsource(route)
			self.assertIn(
				f"quota_dependency({quota_identifier})",
				source,
				msg=f"{route.__name__} is no longer metered by {quota_identifier}",
			)
