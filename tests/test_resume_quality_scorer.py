"""Resume quality score: general quality at upload, role match after selection.

Three defects found while reviewing the score card: keyword matching was
substring-based ("ci" matched "specific"), the section check compared
"Summary / Objective" against the recorded "Summary" so complete resumes were
told to add sections they had, and 35% of the overall score was role keyword
coverage against a default role before any role was chosen.
"""

from __future__ import annotations

from unittest import TestCase

from backend.nlp.resume_quality_scorer import (
	_has_keyword,
	_role_keywords_for_key,
	_score_section_completeness,
	_score_style,
	score_resume,
	score_role_match,
)

_COMPLETE = {
	"name": "Alex Tester",
	"email": "alex@example.com",
	"summary": "Backend engineer who builds reliable Python APIs and data services for growing products.",
	"skills": ["Python", "SQL", "Git", "REST"],
	"technologies": ["FastAPI", "Docker", "PostgreSQL"],
	"projects": [{"title": "Orders API", "description": "Built an API", "outcomes": ["Cut latency 40%"]}],
	"education": [{"degree": "B.Tech", "cgpa": "8.4"}],
}


class KeywordMatchingTests(TestCase):
	def test_keywords_match_whole_words_only(self) -> None:
		text = "a specific build of my interest in redistribution"
		for keyword in ("ci", "ui", "rest", "redis"):
			with self.subTest(keyword=keyword):
				self.assertFalse(_has_keyword(text, keyword))

	def test_real_mentions_still_match(self) -> None:
		text = "set up ci/cd, a rest api, and redis caching"
		for keyword in ("ci/cd", "rest", "redis"):
			with self.subTest(keyword=keyword):
				self.assertTrue(_has_keyword(text, keyword))

	def test_plurals_and_alternatives_match(self) -> None:
		text = "built rest apis on two databases with django"
		self.assertTrue(_has_keyword(text, "api"))
		self.assertTrue(_has_keyword(text, "database"))
		self.assertTrue(_has_keyword(text, "flask|django|fastapi"))

	def test_invented_role_names_get_a_sensible_keyword_family(self) -> None:
		self.assertIn("pandas", _role_keywords_for_key("junior_data_analyst_trainee"))
		self.assertTrue(any("react" in kw.split("|") for kw in _role_keywords_for_key("custom_role", "Junior Web Developer")))
		self.assertIn("docker", _role_keywords_for_key("api_engineer"))


class SectionTests(TestCase):
	def test_a_complete_resume_is_not_told_to_add_sections_it_has(self) -> None:
		result = _score_section_completeness(_COMPLETE)
		self.assertNotIn("Add missing sections", result.tip)

	def test_a_missing_summary_is_reported_by_its_display_name(self) -> None:
		result = _score_section_completeness({**_COMPLETE, "summary": ""})
		self.assertIn("Summary / Objective", result.tip)


class StyleTests(TestCase):
	def test_pronouns_and_buzzwords_are_flagged(self) -> None:
		text = ("I am a hardworking team player. My role was various tasks. " * 30)
		result = _score_style(text)
		self.assertGreater(result.pronoun_count, 0)
		self.assertIn("hardworking", result.buzzwords_found)
		self.assertLess(result.score, 1.0)

	def test_clean_concise_text_scores_well(self) -> None:
		text = " ".join(["Built a FastAPI service that cut response time by 40 percent for 2k users."] * 30)
		result = _score_style(text)
		self.assertEqual(result.pronoun_count, 0)
		self.assertEqual(result.buzzwords_found, [])
		self.assertGreaterEqual(result.score, 0.9)


class OverallScoreTests(TestCase):
	def test_general_score_needs_no_role(self) -> None:
		result = score_resume(_COMPLETE, "Built an API that cut latency 40%.")
		self.assertFalse(hasattr(result, "keyword_density"))
		self.assertTrue(hasattr(result, "style"))
		self.assertGreater(result.overall_score, 0)

	def test_the_same_resume_scores_the_same_whatever_role_is_passed(self) -> None:
		a = score_resume(_COMPLETE, "text", role_key="frontend_react_developer").overall_score
		b = score_resume(_COMPLETE, "text", role_key="data_analyst").overall_score
		self.assertEqual(a, b)


	def test_a_very_short_resume_cannot_score_highly(self) -> None:
		result = score_resume(_COMPLETE, "Built an API that cut latency 40%.")
		self.assertLessEqual(result.overall_score, 60)


class RoleMatchTests(TestCase):
	def test_reports_matched_and_missing_skills_for_the_role(self) -> None:
		result = score_role_match(_COMPLETE, "FastAPI Docker PostgreSQL REST API", "backend_python_developer", "Backend Python Developer")
		self.assertIn("docker", result.matched)
		self.assertIn("rest", result.matched)
		self.assertTrue(result.missing)
		self.assertGreater(result.match_percent, 0)
		self.assertEqual(result.role_title, "Backend Python Developer")
		self.assertIn("fastapi", result.matched)
		self.assertNotIn("flask / django / fastapi", result.missing)

	def test_a_different_role_gives_a_different_match(self) -> None:
		backend = score_role_match(_COMPLETE, "FastAPI Docker", "backend_python_developer").match_percent
		frontend = score_role_match(_COMPLETE, "FastAPI Docker", "frontend_react_developer").match_percent
		self.assertNotEqual(backend, frontend)
