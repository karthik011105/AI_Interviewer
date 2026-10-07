"""Coverage plans for each conversational round.

The conversational engine walks a coverage plan: a list of (focus_skill,
question_tier) targets it steers the interviewer through. The technical round
derives its targets from the candidate's skill profile. HR and project
discussion need different targets — behavioural competencies, and the
candidate's own projects — but the same plan shape, so everything downstream
(the coverage director, the coverage rail, the report) works unchanged.

Both the engine (on connect) and the round setup (on /interview/start) build
the plan, so it lives here rather than in either of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from backend.nlp.coverage_director import CoveragePlan, build_coverage_plan

TECHNICAL_TARGET_COUNT = 6

# The same competencies the scripted HR generator is told to focus on (see
# _HR_SYSTEM_PROMPT in question_generator.py), so the two modes assess the same
# things.
HR_COMPETENCIES: tuple[str, ...] = (
	"Motivation for this role",
	"Teamwork and collaboration",
	"Handling feedback",
	"Ownership and accountability",
	"Learning from a setback",
	"Career goals",
)

HR_TIER = "behavioural"
PROJECT_TIER = "project"

_MAX_PROJECTS = 3

# Used when the resume lists no projects, so the round still has a shape.
_GENERIC_PROJECT_TARGETS: tuple[str, ...] = (
	"A project you built: what it does and why you built it",
	"A project you built: your role and key decisions",
	"A project you built: the hardest problem",
	"A project you built: the results and what you would change",
)


# Targets are "<project>: <angle>", where the angle reads naturally after
# "tell me about" — fallback_question() relies on that.
def _project_title(project: Mapping[str, Any], index: int) -> str:
	title = " ".join(str(project.get("title") or project.get("name") or "").split())
	if len(title) > 60:
		title = f"{title[:57].rstrip()}..."
	return title or f"Project {index + 1}"


def _project_targets(projects: Sequence[Any]) -> list[str]:
	named = [
		_project_title(project, index)
		for index, project in enumerate(projects)
		if isinstance(project, Mapping)
	][:_MAX_PROJECTS]

	if not named:
		return list(_GENERIC_PROJECT_TARGETS)
	if len(named) == 1:
		# One project carries the whole round, so split it more finely.
		title = named[0]
		return [
			f"{title}: its goals and your role",
			f"{title}: the key technical decisions",
			f"{title}: the hardest challenge",
			f"{title}: the results and lessons",
		]
	return [
		target
		for title in named
		for target in (f"{title}: your role and decisions", f"{title}: the challenges and results")
	]


def build_round_coverage_plan(round_type: str, context: Mapping[str, Any]) -> CoveragePlan:
	"""The coverage plan for one round, tagged with the round it belongs to."""

	round_key = str(round_type or "").strip().casefold()

	if round_key == "hr":
		allocation = [{"focus_skill": name, "question_tier": HR_TIER} for name in HR_COMPETENCIES]
		plan = build_coverage_plan(allocation, min_turns=len(allocation), max_turns=len(allocation) + 2)
	elif round_key == "project_discussion":
		targets = _project_targets(context.get("projects") or [])
		allocation = [{"focus_skill": name, "question_tier": PROJECT_TIER} for name in targets]
		plan = build_coverage_plan(allocation, min_turns=len(allocation), max_turns=len(allocation) + 3)
	else:
		# Imported here: question_generator is heavy and the technical round is
		# the only one that needs it.
		from backend.nlp.question_generator import _build_skill_allocation_plan

		plan = build_coverage_plan(_build_skill_allocation_plan(context, TECHNICAL_TARGET_COUNT))

	# The fallback question and the director prompt read this to phrase things
	# for the right round.
	plan["round"] = round_key or "technical"
	return plan


__all__ = [
	"HR_COMPETENCIES",
	"HR_TIER",
	"PROJECT_TIER",
	"build_round_coverage_plan",
]
