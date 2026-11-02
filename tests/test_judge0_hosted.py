"""Hosted Judge0 (RapidAPI free plan): every request counts toward ~50 a day.

A run must cost one request - the submit with wait=true - not a submit plus a
poll loop, and must carry the RapidAPI key headers.
"""

from __future__ import annotations

import base64
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from backend.assessment import oa_generator
from backend.config import get_settings
from backend.dsa import code_executor


def _b64(text: str) -> str:
	return base64.b64encode(text.encode("utf-8")).decode("ascii")


class HostedJudge0Tests(TestCase):
	def _settings(self, **overrides):
		values = {
			"api_base_url": "https://judge0-ce.p.rapidapi.com",
			"rapidapi_key": "test-key",
			"wait_for_result": True,
			**overrides,
		}
		judge0 = replace(get_settings().judge0, **values)
		return SimpleNamespace(judge0=judge0)

	def _patch(self, settings, responses):
		calls = []

		class FakeResponse:
			def __init__(self, body):
				self._body = body
				self.headers = SimpleNamespace(get_content_charset=lambda: "utf-8")

			def read(self):
				import json

				return json.dumps(self._body).encode("utf-8")

			def __enter__(self):
				return self

			def __exit__(self, *exc):
				return False

		def fake_urlopen(request, timeout):
			calls.append(request)
			return FakeResponse(responses.pop(0))

		for target, value in (
			("backend.dsa.code_executor.get_settings", lambda: settings),
			("backend.dsa.code_executor.urllib_request.urlopen", fake_urlopen),
			("backend.dsa.code_executor.time.sleep", lambda _s: None),
		):
			patcher = patch(target, value)
			patcher.start()
			self.addCleanup(patcher.stop)
		return calls

	def test_one_request_per_run_with_rapidapi_headers(self) -> None:
		calls = self._patch(self._settings(), [
			{"status": {"id": 3, "description": "Accepted"}, "stdout": _b64("hello\n"), "token": "t1"},
		])
		result = code_executor.run_program('print("hello")', "python")

		self.assertEqual(len(calls), 1)
		request = calls[0]
		self.assertIn("wait=true", request.full_url)
		self.assertEqual(request.get_header("X-rapidapi-key"), "test-key")
		self.assertEqual(request.get_header("X-rapidapi-host"), "judge0-ce.p.rapidapi.com")
		self.assertEqual(result["stdout"], "hello\n")

	def test_unfinished_wait_response_falls_back_to_polling(self) -> None:
		calls = self._patch(self._settings(), [
			{"status": {"id": 2, "description": "Processing"}, "token": "t2"},
			{"status": {"id": 3, "description": "Accepted"}, "stdout": _b64("42\n")},
		])
		result = code_executor.run_program("print(42)", "python")

		self.assertEqual(len(calls), 2)
		self.assertIn("/submissions/t2", calls[1].full_url)
		self.assertEqual(result["stdout"], "42\n")

	def test_self_hosted_default_still_polls_without_rapidapi_headers(self) -> None:
		settings = self._settings(rapidapi_key="", wait_for_result=False)
		calls = self._patch(settings, [
			{"token": "t3"},
			{"status": {"id": 3, "description": "Accepted"}, "stdout": _b64("ok\n")},
		])
		code_executor.run_program('print("ok")', "python")

		self.assertNotIn("wait=true", calls[0].full_url)
		self.assertIsNone(calls[0].get_header("X-rapidapi-key"))


class AssessmentJudge0SwitchTests(TestCase):
	def test_assessment_can_leave_judge0_to_the_candidate(self) -> None:
		with patch.dict(os.environ, {"OA_RUN_CODE_ON_JUDGE0": "0"}), \
			 patch("backend.dsa.code_executor.run_program") as run_program:
			with self.assertRaises(RuntimeError):
				oa_generator._default_run("print(1)", "python")
		run_program.assert_not_called()
