"""Non-resume PDFs are rejected before the LLM call.

The case that prompted it: a company's list of open internship positions was
parsed and scored 73/100 as a resume.
"""

from __future__ import annotations

from unittest import TestCase

from backend.nlp.resume_detector import check_resume

_RESUME = """Alex Tester
alex@example.com | +91 98765 43210 | linkedin.com/in/alextester
Summary
Backend developer who builds Python APIs.
Education
B.Tech Computer Science, 2026, CGPA 8.4
Projects
Orders API - FastAPI, PostgreSQL; cut latency 40%
Skills
Python, SQL, Docker, Git
"""

_JOB_LIST = """Intern Job Title Location Skillset Required
Intern, Software Development Engineer [PSET-Access-Eng] Bengaluru Java, C#, AWS, Jenkins
Intern, Software Development Engineer [PDMS-DPM-Engineering] Bengaluru
Required: Java, REST/HTTP and JSON fundamentals, basic API/software testing knowledge, Git
Good to have: JUnit/REST Assured, Pact, OpenAPI/Swagger, Jenkins
Intern, Software Development Engineer Pune Java, Springboot, AWS (Good to have)
Intern, Software Development Engineer Pune Node/Typescript, AWS, AI/ML (Nice to have)
"""


class ResumeCheckTests(TestCase):
	def test_a_resume_passes(self) -> None:
		result = check_resume(_RESUME)
		self.assertTrue(result.is_resume)
		self.assertTrue(result.has_contact)
		self.assertIn("education", result.headings)

	def test_a_job_listing_is_rejected(self) -> None:
		result = check_resume(_JOB_LIST)
		self.assertFalse(result.is_resume)
		self.assertGreater(result.posting_phrases, 0)

	def test_contact_details_alone_are_not_a_resume(self) -> None:
		self.assertFalse(check_resume("Fee receipt\nalex@example.com\nAmount paid 45000").is_resume)

	def test_a_resume_without_contact_details_still_passes_on_structure(self) -> None:
		text = "Education\nB.Tech 2026\nProjects\nChat app\nSkills\nPython, React\nAchievements\nHackathon winner"
		self.assertTrue(check_resume(text).is_resume)

	def test_empty_text_is_rejected(self) -> None:
		self.assertFalse(check_resume("").is_resume)
