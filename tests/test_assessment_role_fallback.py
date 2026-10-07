"""Every role the matcher can produce must be able to start an assessment.

Role matching generates role keys freely ("junior_backend_engineer",
"api_engineer"), and the assessment used to refuse anything outside its
19-role catalog with "No assessment blueprint exists", blocking the
candidate at step two. Found by walking the redesigned UI end to end.
"""

from __future__ import annotations

from unittest import TestCase

from backend.assessment.question_bank import (
	FALLBACK_ASSESSMENT_ROLE_KEY,
	build_assessment_batch,
	get_assessment_blueprint,
	load_role_catalog,
)


class AssessmentRoleFallbackTests(TestCase):
	def test_framework_names_pin_the_stack(self) -> None:
		cases = {
			"django_developer": "backend_python_developer",
			"spring_boot_engineer": "backend_java_developer",
			"junior_android_developer": "mobile_app_developer",
			"python_backend_engineer": "backend_python_developer",
		}
		for role_key, expected in cases.items():
			with self.subTest(role_key=role_key):
				self.assertEqual(get_assessment_blueprint(role_key).role_key, expected)

	def test_unmatched_roles_fall_back_instead_of_failing(self) -> None:
		for role_key in ("junior_backend_engineer", "api_engineer", "totally_unknown_role"):
			with self.subTest(role_key=role_key):
				self.assertEqual(get_assessment_blueprint(role_key).role_key, FALLBACK_ASSESSMENT_ROLE_KEY)

	def test_the_fallback_exists_and_builds_a_batch(self) -> None:
		self.assertIn(FALLBACK_ASSESSMENT_ROLE_KEY, load_role_catalog())
		batch = build_assessment_batch(session_id="s1", role_key="junior_backend_engineer")
		questions = batch.get("questions") if isinstance(batch, dict) else getattr(batch, "questions", batch)
		self.assertTrue(questions)

	def test_catalog_roles_still_resolve_to_themselves(self) -> None:
		for role_key in load_role_catalog():
			with self.subTest(role_key=role_key):
				self.assertEqual(get_assessment_blueprint(role_key).role_key, role_key)
