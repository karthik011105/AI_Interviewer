"""Render the final interview report as a structured PDF.

Pure layout: ``build_report_pdf`` takes plain data (see ``routes_report``,
which assembles it from the report snapshot, the resume and the session) and
returns PDF bytes. Uses PyMuPDF's Story (HTML + CSS to PDF), which the backend
already depends on for reading resumes, so no new dependency.

Sections, in the order a reviewer reads them:
  1. Header - candidate, role, date
  2. Summary - overall score, verdict, rounds completed
  3. Scores at a glance - one row per round
  4. Round by round - what went well / what to improve / advice
  5. Resume - quality checks and fit for the chosen role
  6. Next steps
"""

from __future__ import annotations

import html
import io
from typing import Any

import fitz  # PyMuPDF

_PAGE = fitz.paper_rect("a4")
_MARGIN = 48
_CONTENT = _PAGE + (_MARGIN, _MARGIN, -_MARGIN, -_MARGIN - 18)  # room for the footer

_ACCENT = "#5b3fd6"
_INK = "#16141f"
_MUTED = "#5f5b72"
_RULE = "#dcd8ea"
_GOOD = "#2e7d5b"
_MID = "#a66a1e"
_LOW = "#ae3b3b"

_CSS = f"""
* {{ font-family: sans-serif; color: {_INK}; }}
body {{ font-size: 10pt; line-height: 1.45; }}
h1 {{ font-size: 20pt; margin: 0 0 2pt 0; }}
h2 {{ font-size: 13pt; color: {_ACCENT}; margin: 16pt 0 6pt 0; padding-bottom: 3pt; border-bottom: 1px solid {_RULE}; }}
h3 {{ font-size: 11pt; margin: 12pt 0 3pt 0; }}
p {{ margin: 0 0 5pt 0; }}
ul {{ margin: 0 0 6pt 0; padding-left: 14pt; }}
li {{ margin: 0 0 2pt 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 2pt 0 4pt 0; }}
th {{ text-align: left; font-size: 9pt; color: {_MUTED}; padding: 4pt 6pt; border-bottom: 1px solid {_RULE}; }}
td {{ padding: 5pt 6pt; border-bottom: 1px solid {_RULE}; vertical-align: middle; }}
.muted {{ color: {_MUTED}; }}
.small {{ font-size: 8.5pt; }}
.label {{ font-size: 8.5pt; color: {_MUTED}; font-weight: bold; margin: 6pt 0 2pt 0; }}
.score {{ font-size: 26pt; font-weight: bold; }}
.good {{ color: {_GOOD}; }}
.mid {{ color: {_MID}; }}
.low {{ color: {_LOW}; }}
.note {{ color: {_LOW}; font-weight: bold; }}
"""


def _e(value: Any) -> str:
	return html.escape(str(value or ""))


def _tone(score: float | None) -> str:
	if score is None:
		return "muted"
	return "good" if score >= 70 else "mid" if score >= 50 else "low"


def _pct(score: float | None) -> str:
	return "-" if score is None else f"{round(score)}%"


def _verdict(score: float | None, completed: int, total: int) -> str:
	if score is None:
		return "No rounds have been scored yet."
	if score >= 80:
		lead = "Strong performance."
	elif score >= 65:
		lead = "Solid performance with a few gaps to close."
	elif score >= 50:
		lead = "A fair attempt; several areas need practice."
	else:
		lead = "Needs more preparation before real interviews."
	if completed < total:
		lead += f" Based on {completed} of {total} rounds."
	return lead


def _list(items: list[str]) -> str:
	items = [i for i in items if str(i or "").strip()]
	if not items:
		return ""
	return "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>"


def _labelled_list(label: str, items: list[str]) -> str:
	body = _list(items)
	return f'<p class="label">{_e(label)}</p>{body}' if body else ""


def _render_html(data: dict[str, Any]) -> str:
	candidate = data.get("candidate") or {}
	rounds: list[dict[str, Any]] = data.get("rounds") or []
	completed = sum(1 for r in rounds if r.get("status") == "complete")
	overall = data.get("overall_score")
	parts: list[str] = []

	# 1. Header
	name = candidate.get("name") or "Candidate"
	parts.append("<h1>Interview report</h1>")
	meta = [f"<b>{_e(name)}</b>"]
	if candidate.get("email"):
		meta.append(_e(candidate["email"]))
	parts.append(f'<p>{" &middot; ".join(meta)}</p>')
	parts.append(
		f'<p class="muted">Role: <b>{_e(data.get("role_title") or "Not selected")}</b> &middot; '
		f'{_e(data.get("generated_on"))} &middot; Session {_e(data.get("session_ref"))}</p>'
	)

	# 2. Summary
	parts.append("<h2>Summary</h2>")
	parts.append(
		'<table><tr>'
		f'<td style="width: 110pt; border-bottom: none;"><span class="score {_tone(overall)}">{_pct(overall)}</span>'
		'<br/><span class="small muted">Overall score</span></td>'
		f'<td style="border-bottom: none;"><p><b>{_e(_verdict(overall, completed, len(rounds)))}</b></p>'
		f'<p class="muted">Rounds completed: {completed} of {len(rounds)}</p></td>'
		"</tr></table>"
	)
	if data.get("ended_early"):
		parts.append(
			f'<p class="note">This interview was ended early: the candidate left full screen or switched '
			f'tabs {_e(data.get("warning_limit") or 3)} times.</p>'
		)

	# 3. Scores at a glance
	parts.append("<h2>Scores at a glance</h2>")
	rows = "".join(
		f"<tr><td><b>{_e(r.get('label'))}</b></td>"
		f"<td>{_e(r.get('status_label'))}</td>"
		f"<td class='{_tone(r.get('score'))}'><b>{_pct(r.get('score'))}</b></td></tr>"
		for r in rounds
	)
	parts.append(
		'<table><tr><th style="width: 40%;">Round</th><th style="width: 35%;">Status</th>'
		f'<th style="width: 25%;">Score</th></tr>{rows}</table>'
	)

	# 4. Round by round
	scored = [r for r in rounds if r.get("status") == "complete" or r.get("score") is not None]
	if scored:
		parts.append("<h2>Round by round</h2>")
		for r in scored:
			parts.append(f"<h3>{_e(r.get('label'))} <span class='{_tone(r.get('score'))}'>{_pct(r.get('score'))}</span></h3>")
			if r.get("summary"):
				parts.append(f"<p>{_e(r['summary'])}</p>")
			parts.append(_labelled_list("What went well", r.get("strengths") or []))
			parts.append(_labelled_list("What to improve", r.get("improve") or []))
			parts.append(_labelled_list("Advice", r.get("advice") or []))

	# 5. Resume
	resume = data.get("resume") or {}
	if resume:
		parts.append("<h2>Resume</h2>")
		if resume.get("score") is not None:
			parts.append(f"<p>Resume score: <b class='{_tone(resume['score'])}'>{round(resume['score'])} / 100</b></p>")
		checks = resume.get("checks") or []
		if checks:
			rows = "".join(
				f"<tr><td><b>{_e(c.get('label'))}</b></td><td class='{_tone(c.get('pct'))}'>{_pct(c.get('pct'))}</td>"
				f"<td class='muted'>{_e(c.get('tip'))}</td></tr>"
				for c in checks
			)
			parts.append(
				'<table><tr><th style="width: 30%;">Check</th><th style="width: 12%;">Score</th>'
				f'<th style="width: 58%;">Tip</th></tr>{rows}</table>'
			)
		match = resume.get("role_match") or {}
		if match:
			parts.append(
				f"<h3>Fit for {_e(match.get('role_title') or data.get('role_title'))} "
				f"<span class='{_tone(match.get('match_percent'))}'>{_pct(match.get('match_percent'))}</span></h3>"
			)
			if match.get("matched"):
				parts.append(f"<p><b>Skills on the resume:</b> {_e(', '.join(match['matched']))}</p>")
			if match.get("missing"):
				missing = [m.split(" / ")[0] for m in match["missing"]][:8]
				parts.append(f"<p><b>Skills not found:</b> {_e(', '.join(missing))}</p>")

	# 6. Next steps
	steps = data.get("next_steps") or []
	if steps:
		parts.append("<h2>Next steps</h2>")
		parts.append("<ol>" + "".join(f"<li>{_e(s)}</li>" for s in steps) + "</ol>")

	return "<body>" + "".join(parts) + "</body>"


def build_report_pdf(data: dict[str, Any]) -> bytes:
	"""Lay the report out on A4 pages and return the PDF bytes."""
	story = fitz.Story(html=_render_html(data), user_css=_CSS)
	buffer = io.BytesIO()
	writer = fitz.DocumentWriter(buffer)
	more = True
	while more:
		device = writer.begin_page(_PAGE)
		more, _ = story.place(_CONTENT)
		story.draw(device)
		writer.end_page()
	writer.close()

	# Footer: product name and page numbers, added after layout so the total is known.
	doc = fitz.open("pdf", buffer.getvalue())
	total = doc.page_count
	for index, page in enumerate(doc, start=1):
		y = _PAGE.height - _MARGIN + 12
		page.insert_text((_MARGIN, y), "PrepForge interview report", fontsize=8, color=(0.37, 0.36, 0.45))
		label = f"Page {index} of {total}"
		width = fitz.get_text_length(label, fontsize=8)
		page.insert_text((_PAGE.width - _MARGIN - width, y), label, fontsize=8, color=(0.37, 0.36, 0.45))
	doc.set_metadata({"title": f"Interview report - {(data.get('candidate') or {}).get('name') or 'Candidate'}", "creator": "PrepForge"})
	return doc.tobytes(garbage=3, deflate=True)
