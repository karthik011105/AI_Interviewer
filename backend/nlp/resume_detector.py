"""Is this PDF a resume at all?

Without this check, any PDF was parsed and scored: a company's list of open
internship positions came back as a 73/100 resume told to "add missing
sections: Name, Email". Runs on the extracted text before the LLM call, so a
wrong file also costs nothing.

Signals, the way a recruiter would glance at the page:
- contact details (email, phone, LinkedIn/GitHub link);
- resume section headings (Education, Projects, Experience, Skills...);
- job-posting phrasing ("Required", "Good to have", "Job title"...), which
  counts against it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{8,14}\d)(?!\d)")
_PROFILE_LINK = re.compile(r"\b(linkedin\.com/in/|github\.com/|leetcode\.com/|portfolio)", re.IGNORECASE)

# A heading is a short line that is (mostly) just the section name.
_RESUME_HEADINGS = {
	"education": r"education|academic(s| background| details| qualifications?)?",
	"experience": r"(work |professional )?experience|employment|internships?|work history",
	"projects": r"(academic |personal |key )?projects?",
	"skills": r"(technical |key |core )?skills|technologies|tech stack|tools",
	"summary": r"summary|objective|profile|about me|career objective",
	"certifications": r"certifications?|courses|trainings?",
	"achievements": r"achievements?|awards?|honou?rs|accomplishments",
	"activities": r"extra[- ]?curricular( activities)?|activities|positions? of responsibility|leadership|volunteering",
	"publications": r"publications?|research",
}
_HEADING_LINE = {
	name: re.compile(rf"^\W*({pattern})\W*$", re.IGNORECASE) for name, pattern in _RESUME_HEADINGS.items()
}


def is_heading(line: str) -> bool:
	"""A resume section heading line ("Education", "PROJECTS", "Technical Skills")."""
	line = line.strip()
	return len(line) <= 40 and any(pattern.match(line) for pattern in _HEADING_LINE.values())


_POSTING_PHRASES = re.compile(
	r"\b(required|good to have|nice to have|job title|job description|responsibilities|"
	r"qualifications required|we are looking for|apply (now|here|by)|open positions?|"
	r"skillset required|years of experience required|about the role|who you are)\b",
	re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ResumeCheck:
	is_resume: bool
	score: int
	headings: tuple[str, ...]
	has_contact: bool
	posting_phrases: int


def check_resume(text: str) -> ResumeCheck:
	lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
	headings = tuple(
		name for name, pattern in _HEADING_LINE.items()
		if any(len(line) <= 40 and pattern.match(line) for line in lines)
	)
	has_email = bool(_EMAIL.search(text or ""))
	has_phone = bool(_PHONE.search(text or ""))
	has_link = bool(_PROFILE_LINK.search(text or ""))
	posting = len(_POSTING_PHRASES.findall(text or ""))

	score = 2 * has_email + has_phone + has_link + min(len(headings), 5) - min(posting // 2, 4)
	# Needs some resume structure: two section headings, or contact details
	# plus at least one heading.
	structured = len(headings) >= 2 or ((has_email or has_phone) and len(headings) >= 1)
	return ResumeCheck(
		is_resume=structured and score >= 3,
		score=score,
		headings=headings,
		has_contact=has_email or has_phone,
		posting_phrases=posting,
	)
