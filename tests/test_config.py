"""Tests for backend/config.py — currently just the CORS settings.

CORS_ALLOWED_ORIGINS/CORS_ALLOW_ORIGIN_REGEX exist so a real deployment can
allow its actual frontend domain without a code change; the defaults exist so
local development (the Vite dev server) needs no configuration at all.
"""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from backend import config


class CorsSettingsTests(TestCase):
	def setUp(self) -> None:
		self._env_patcher = patch.dict(
			os.environ,
			{"AUTH_JWT_SECRET": "cors-test-secret-padded-to-32-bytes-min"},
			clear=False,
		)
		self._env_patcher.start()
		config.reset_settings()

	def tearDown(self) -> None:
		self._env_patcher.stop()
		config.reset_settings()

	def test_defaults_to_the_vite_dev_server(self) -> None:
		settings = config.get_settings()

		self.assertEqual(
			settings.cors.allowed_origins,
			("http://127.0.0.1:5173", "http://localhost:5173"),
		)
		self.assertIn("localhost", settings.cors.allow_origin_regex)

	def test_reads_a_comma_separated_origin_list_from_env(self) -> None:
		with patch.dict(
			os.environ,
			{"CORS_ALLOWED_ORIGINS": "https://app.example.com, https://www.example.com"},
		):
			config.reset_settings()
			settings = config.get_settings()

		self.assertEqual(
			settings.cors.allowed_origins,
			("https://app.example.com", "https://www.example.com"),
		)

	def test_blank_origin_env_var_falls_back_to_the_dev_default(self) -> None:
		with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": "   "}):
			config.reset_settings()
			settings = config.get_settings()

		self.assertEqual(
			settings.cors.allowed_origins,
			("http://127.0.0.1:5173", "http://localhost:5173"),
		)

	def test_reads_a_custom_origin_regex_from_env(self) -> None:
		with patch.dict(os.environ, {"CORS_ALLOW_ORIGIN_REGEX": r"https://.*\.example\.com"}):
			config.reset_settings()
			settings = config.get_settings()

		self.assertEqual(settings.cors.allow_origin_regex, r"https://.*\.example\.com")
