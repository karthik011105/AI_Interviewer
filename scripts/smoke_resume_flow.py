"""Smoke test for the local resume parsing and role-matching flow.

This script avoids external dependencies like Groq and Supabase by:
- generating a temporary PDF locally with PyMuPDF
- calling the FastAPI app in-process through TestClient
- supplying structured_resume_payload as a local override

Run it with:
e:/interview_simulator/.venv/Scripts/python.exe scripts/smoke_resume_flow.py
"""

from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import fitz
from fastapi.testclient import TestClient

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
	sys.path.insert(0, str(WORKSPACE_ROOT))

from backend.main import app


def build_sample_resume_text() -> str:
	return "\n".join(
		[
			"Ada Lovelace",
			"Email: ada@example.com | Phone: 1234567890 | Bangalore",
			"Summary: Python backend developer building FastAPI services, SQL workflows, and analytics tooling.",
			"Skills: Python, SQL, FastAPI, Docker, APIs, debugging, testing.",
			"Project 1: Built analytics APIs and dashboards for reporting automation with PostgreSQL.",
			"Project 2: Implemented evaluation pipelines, candidate scoring, and session persistence.",
			"Education: B.Tech in Electronics and Communication Engineering.",
			"Interests: backend systems, machine learning, data analysis, product reliability.",
		]
	)


def create_pdf(pdf_path: Path, text: str) -> None:
	document = fitz.open()
	page = document.new_page()
	page.insert_textbox(fitz.Rect(50, 50, 550, 750), text, fontsize=12)
	document.save(pdf_path)
	document.close()


def main() -> None:
	resume_text = build_sample_resume_text()

	with TemporaryDirectory() as temp_dir:
		pdf_path = Path(temp_dir) / "resume.pdf"
		create_pdf(pdf_path, resume_text)

		client = TestClient(app)
		response = client.post(
			"/resume/parse",
			json={
				"pdf_path": str(pdf_path),
				"structured_resume_payload": {
					"name": "Ada Lovelace",
					"summary": resume_text,
					"skills": ["Python", "SQL", "testing"],
					"technologies": ["FastAPI", "Docker"],
					"projects": [
						{
							"title": "Analytics API",
							"description": "Built backend APIs for analytics dashboards.",
							"tech_stack": ["Python", "FastAPI", "SQL"],
							"outcomes": ["Reduced reporting time"],
							"role": "Backend developer",
						}
					],
				},
				"persist_resume": False,
				"run_role_matching": True,
				"use_groq_profiles": False,
				"persist_role_matches": False,
			},
		)

		payload = response.json()
		assert response.status_code == 200, payload
		assert payload["resume"]["parsed_resume"]["name"] == "Ada Lovelace"
		assert payload["role_matches"]["matches"]
		assert payload["role_matches"]["matches"][0]["role_key"] == "backend_python_developer"

		print("resume parser smoke test passed")
		print(f"session_id={payload['session_id']}")
		print(f"top_role={payload['role_matches']['matches'][0]['role_key']}")


if __name__ == "__main__":
	main()