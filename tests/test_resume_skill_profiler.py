from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from backend.nlp.resume_skill_profiler import profile_resume_skills


class ResumeSkillProfilerTests(TestCase):
	def setUp(self) -> None:
		self.backend_resume = {
			"summary": "Built backend APIs with Python and FastAPI for authentication workflows.",
			"skills": ["Python", "FastAPI", "SQL"],
			"technologies": ["Supabase"],
			"projects": [
				{
					"title": "Auth API",
					"description": (
						"Built and deployed a Python FastAPI backend service with SQL queries, "
						"authentication, and database integrations."
					),
					"tech_stack": ["Python", "FastAPI", "SQL"],
					"outcomes": ["Improved login flow reliability"],
					"role": "Implemented backend services and API endpoints",
				}
			],
		}

	def test_profile_resume_skills_marks_strong_and_absent_tiers(self) -> None:
		with patch("backend.nlp.resume_skill_profiler._load_tfidf_vectorizer", return_value=None), \
			 patch("backend.nlp.resume_skill_profiler.get_semantic_encoder", return_value=None):
			profile = profile_resume_skills(self.backend_resume, "backend_python_developer")

		self.assertEqual(profile.role_key, "backend_python_developer")
		self.assertIn("python", profile.strong_skills)
		self.assertIn("fastapi", profile.strong_skills)
		self.assertIn("git", profile.absent_skills)
		self.assertEqual(profile.skill_scores["python"].tier, "strong")
		self.assertEqual(profile.skill_scores["git"].tier, "absent")
		self.assertEqual(profile.semantic_backend, "disabled")
		self.assertEqual(profile.tfidf_backend, "lexical_overlap")
		self.assertLess(
			profile.priority_focus_areas.index("git"),
			profile.priority_focus_areas.index("python"),
		)

	def test_profile_resume_skills_flags_soft_gap_from_semantic_signal(self) -> None:
		class FakeEncoder:
			def encode(self, texts, normalize_embeddings=True):
				vectors = []
				for text in texts:
					normalized = str(text).lower()
					if "postgresql" in normalized:
						vectors.append([1.0, 0.0, 0.0])
					elif "sql" in normalized or "database" in normalized:
						vectors.append([0.95, 0.05, 0.0])
					else:
						vectors.append([0.0, 1.0, 0.0])
				return vectors

		with patch("backend.nlp.resume_skill_profiler._load_tfidf_vectorizer", return_value=None), \
			 patch("backend.nlp.resume_skill_profiler.get_semantic_encoder", return_value=FakeEncoder()):
			profile = profile_resume_skills(self.backend_resume, "backend_python_developer")

		self.assertIn("postgresql", profile.soft_gap_skills)
		self.assertEqual(profile.semantic_backend, "sbert")
		self.assertGreater(profile.skill_scores["postgresql"].sbert_score, 0.9)
		self.assertEqual(profile.skill_scores["postgresql"].exact_match_score, 0.0)