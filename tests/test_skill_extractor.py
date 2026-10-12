"""JobBERT skill extraction: span cleanup and the no-model fallback."""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from backend.nlp import skill_extractor
from backend.nlp.skill_extractor import _chunks, _clean, extract_skills


class CleanTests(TestCase):
	def test_splits_lists_and_trims_filler(self) -> None:
		self.assertEqual(_clean("AWS, REST"), ["aws", "rest"])
		self.assertEqual(_clean("git experience"), ["git"])
		self.assertEqual(_clean("python apis"), ["python"])
		self.assertEqual(_clean("ci/cd"), ["ci/cd"])

	def test_drops_degrees_headings_and_bare_filler(self) -> None:
		for span in ("Computer Science", "ECS Education", "experience", "-"):
			with self.subTest(span=span):
				self.assertEqual(_clean(span), [])


class ChunkTests(TestCase):
	def test_lines_stay_separate_and_chunks_stay_short(self) -> None:
		text = "\n".join(["Skills Python Docker"] * 100)
		chunks = _chunks(text)
		self.assertGreater(len(chunks), 1)
		self.assertTrue(all(len(c.split()) <= 120 for c in chunks))
		self.assertIn("Docker; Skills", chunks[0])


class FallbackTests(TestCase):
	def test_no_model_means_no_skills(self) -> None:
		skill_extractor._load_pipeline.cache_clear()
		extract_skills.cache_clear()
		self.addCleanup(skill_extractor._load_pipeline.cache_clear)
		self.addCleanup(extract_skills.cache_clear)
		with patch.dict(os.environ, {"DISABLE_SKILL_EXTRACTOR": "1"}):
			self.assertEqual(extract_skills("Python, Docker and AWS"), ())
