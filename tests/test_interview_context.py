from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from backend.nlp.interview_context import (
	build_hr_round_context,
	build_project_round_context,
	build_technical_round_context,
)


class InterviewContextSkillProfileTests(TestCase):
	def setUp(self) -> None:
		self.parsed_resume = {
			"name": "Candidate",
			"summary": "Built backend APIs with Python and FastAPI.",
			"skills": ["Python", "FastAPI"],
			"technologies": ["SQL"],
			"projects": [
				{
					"title": "Auth API",
					"description": "Built a Python FastAPI backend service.",
					"tech_stack": ["Python", "FastAPI"],
					"outcomes": ["Improved reliability"],
					"role": "Implemented backend endpoints",
				}
			],
		}

	def test_build_technical_round_context_includes_skill_profile(self) -> None:
		fake_profile = {
			"role_key": "backend_python_developer",
			"role_title": "Backend Python Developer",
			"skill_scores": {},
			"strong_skills": ["python"],
			"familiar_skills": ["sql"],
			"mentioned_skills": [],
			"absent_skills": ["git"],
			"soft_gap_skills": ["postgresql"],
			"priority_focus_areas": ["git", "sql", "python"],
			"tfidf_backend": "lexical_overlap",
			"semantic_backend": "disabled",
			"ner_mode": "regex",
		}

		class FakeProfile:
			strong_skills = ["python"]
			familiar_skills = ["sql"]
			mentioned_skills = []
			absent_skills = ["git"]
			soft_gap_skills = ["postgresql"]
			priority_focus_areas = ["git", "sql", "python"]

			def to_dict(self):
				return fake_profile

		with patch("backend.nlp.interview_context.profile_resume_skills", return_value=FakeProfile()) as mock_profile:
			context = build_technical_round_context(
				self.parsed_resume,
				selected_role="backend_python_developer",
			)

		mock_profile.assert_called_once_with(self.parsed_resume, "backend_python_developer")
		self.assertEqual(context["selected_role_key"], "backend_python_developer")
		self.assertEqual(context["strong_skills"], ["python"])
		self.assertEqual(context["soft_gap_skills"], ["postgresql"])
		self.assertEqual(context["priority_focus_areas"], ["git", "sql", "python"])
		self.assertEqual(context["skill_profile"], fake_profile)

	def test_hr_and_project_contexts_do_not_include_skill_profile(self) -> None:
		hr_context = build_hr_round_context(
			self.parsed_resume,
			selected_role="backend_python_developer",
		)
		project_context = build_project_round_context(
			self.parsed_resume,
			selected_role="backend_python_developer",
		)

		self.assertNotIn("skill_profile", hr_context)
		self.assertNotIn("strong_skills", hr_context)
		self.assertNotIn("skill_profile", project_context)
		self.assertNotIn("strong_skills", project_context)