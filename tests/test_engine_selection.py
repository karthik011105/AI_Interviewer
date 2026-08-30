"""Engine selection for interview rounds.

The conversational engine is opt-in per round via INTERVIEW_DYNAMIC_ROUNDS.
Anything unconfigured - or a dynamic engine that fails to import - must fall
back to the scripted flow rather than failing the connection.
"""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from backend.api.interview_engines import SCRIPTED_ENGINE, dynamic_rounds, select_engine


class EngineSelectionTests(TestCase):
	def test_rounds_default_to_the_scripted_engine(self) -> None:
		with patch.dict(os.environ, {"INTERVIEW_DYNAMIC_ROUNDS": ""}, clear=False):
			for round_type in ("technical", "hr", "project_discussion"):
				self.assertIs(select_engine(round_type), SCRIPTED_ENGINE)

	def test_unlisted_rounds_stay_scripted_when_one_is_enabled(self) -> None:
		with patch.dict(os.environ, {"INTERVIEW_DYNAMIC_ROUNDS": "technical"}, clear=False):
			self.assertIs(select_engine("hr"), SCRIPTED_ENGINE)
			self.assertIs(select_engine("project_discussion"), SCRIPTED_ENGINE)

	def test_configuration_is_parsed_case_insensitively(self) -> None:
		with patch.dict(os.environ, {"INTERVIEW_DYNAMIC_ROUNDS": " Technical , HR "}, clear=False):
			self.assertEqual(dynamic_rounds(), frozenset({"technical", "hr"}))

	def test_configured_round_selects_the_dynamic_engine(self) -> None:
		with patch.dict(os.environ, {"INTERVIEW_DYNAMIC_ROUNDS": "technical"}, clear=False):
			engine = select_engine("technical")
		self.assertEqual(engine.name, "dynamic")
		for hook in ("start", "handle_transcript", "handle_control"):
			self.assertTrue(callable(getattr(engine, hook)), msg=hook)

	def test_unimportable_dynamic_engine_degrades_to_scripted(self) -> None:
		# A broken or absent dynamic engine must not fail the connection: the
		# round should still run on the scripted flow. Both the cached module
		# and the package attribute have to go, or `from ... import dynamic`
		# resolves the attribute without ever consulting the import machinery.
		import sys

		import backend.api.interview_engines as package

		cached = package.dynamic
		try:
			del package.dynamic
			with patch.dict(os.environ, {"INTERVIEW_DYNAMIC_ROUNDS": "technical"}, clear=False):
				with patch.dict(sys.modules, {"backend.api.interview_engines.dynamic": None}):
					self.assertIs(select_engine("technical"), SCRIPTED_ENGINE)
		finally:
			package.dynamic = cached

	def test_scripted_engine_exposes_the_full_hook_set(self) -> None:
		for hook in ("start", "handle_transcript", "handle_control"):
			self.assertTrue(callable(getattr(SCRIPTED_ENGINE, hook)), msg=hook)
		self.assertEqual(SCRIPTED_ENGINE.name, "scripted")
