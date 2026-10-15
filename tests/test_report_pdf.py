"""Structured PDF report: layout from plain data, and the endpoint's data."""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

import fitz

from backend.api import routes_report
from backend.report_pdf import build_report_pdf

_USER = routes_report.AuthenticatedUser(user_id="u1", email="c@example.com", raw_user={"id": "u1"})


def _text(pdf: bytes) -> str:
	return "\n".join(page.get_text() for page in fitz.open("pdf", pdf))


class LayoutTests(TestCase):
	def test_sections_appear_in_reading_order(self) -> None:
		pdf = build_report_pdf({
			"candidate": {"name": "Alex Tester", "email": "alex@example.com"},
			"role_title": "Backend Python Developer",
			"generated_on": "15 Oct 2026",
			"session_ref": "abcd1234",
			"overall_score": 68.4,
			"rounds": [
				{"label": "Assessment", "status": "complete", "status_label": "Completed", "score": 80,
				 "summary": "Answered 8 of 10.", "strengths": ["SQL"], "improve": ["Async I/O"], "advice": ["Review asyncio"]},
				{"label": "HR", "status": "not_started", "status_label": "Not taken", "score": None},
			],
			"resume": {"score": 73, "checks": [{"label": "Sections", "pct": 100, "tip": "All there."}],
					   "role_match": {"role_title": "Backend Python Developer", "match_percent": 59,
									  "matched": ["python"], "missing": ["kubernetes"]}},
			"next_steps": ["Finish the HR round."],
			"ended_early": True,
		})
		text = _text(pdf)
		order = ["Interview report", "Alex Tester", "Summary", "68%", "Scores at a glance",
				 "Round by round", "What went well", "Resume", "kubernetes", "Next steps", "Page 1 of"]
		positions = [text.find(item) for item in order]
		self.assertTrue(all(p >= 0 for p in positions), dict(zip(order, positions)))
		self.assertEqual(positions, sorted(positions))
		self.assertIn("ended early", text)

	def test_text_is_escaped_not_interpreted(self) -> None:
		pdf = build_report_pdf({"candidate": {"name": "<b>Eve</b> & co"}, "rounds": []})
		self.assertIn("<b>Eve</b> & co", _text(pdf))


class PdfDataTests(TestCase):
	def test_rounds_use_app_names_percentages_and_skip_placeholder_text(self) -> None:
		snapshot = {
			"overall_score": 0.7,
			"report_json": {
				"technical": {"status": "complete", "score": 0.62, "summary": "Clear REST answers.",
							  "strengths": ["APIs"], "risks": ["Caching"], "recommendations": ["Practise"]},
				"dsa": {"status": "not_started", "score": None, "summary": "Waiting on saved data."},
			},
		}
		session = {"role_selected": "backend_python_developer", "proctoring": {"terminated": True}}
		with patch("backend.api.routes_report.get_resume_data", return_value=None):
			data = routes_report._build_pdf_data("session-12345678", session, snapshot)
		rounds = {r["key"]: r for r in data["rounds"]}
		self.assertEqual(rounds["dsa"]["label"], "Coding")
		self.assertEqual(rounds["dsa"]["summary"], "")
		self.assertEqual(rounds["technical"]["score"], 62.0)
		self.assertEqual(rounds["technical"]["improve"], ["Caching"])
		self.assertEqual(data["overall_score"], 70.0)
		self.assertEqual(data["role_title"], "Backend Python Developer")
		self.assertTrue(data["ended_early"])
		self.assertTrue(any("Finish the remaining rounds" in s for s in data["next_steps"]))

	def test_endpoint_returns_a_pdf_attachment(self) -> None:
		with patch("backend.api.routes_report._require_parent_session", return_value={"role_selected": "", "user_id": "u1"}), \
			 patch("backend.api.routes_report._load_persisted_report", return_value=(None, True, None)), \
			 patch("backend.api.routes_report._build_report_snapshot", return_value={"report_json": {}, "overall_score": None}), \
			 patch("backend.api.routes_report.get_resume_data", return_value={"parsed_json": {"name": "Alex Tester"}, "raw_text": ""}):
			response = routes_report.download_report_pdf("s1", _USER)
		self.assertEqual(response.media_type, "application/pdf")
		self.assertTrue(response.body.startswith(b"%PDF"))
		self.assertIn('filename="PrepForge-report-Alex-Tester.pdf"', response.headers["content-disposition"])
