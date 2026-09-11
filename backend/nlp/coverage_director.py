"""Steer a free-form interviewer toward the skills that must be covered.

A fully dynamic interviewer picks its own questions, which is what makes the
conversation feel real — and what would otherwise let a round finish without
ever touching a skill the report is expected to assess. Rather than scripting
questions back, this module keeps a plan of skill targets and injects the
remaining ones into each turn as guidance.

The plan is built by `_build_skill_allocation_plan`, the same quota logic the
scripted generator already uses, so tier semantics are identical across both
engines and `covered_topics` keeps adding up for the report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

BudgetPressure = Literal["normal", "tight", "wrap_up_now"]

# Mirrors the guidance in question_generator._build_skill_focus_block so a
# dynamic question at a given tier means the same thing as a scripted one.
TIER_GUIDANCE: dict[str, str] = {
	"strong": "verify depth with mechanisms, trade-offs, or failure modes",
	"familiar": "probe limitations, edge cases, or integration trade-offs",
	"mentioned": "probe whether the concept is understood beyond name recognition",
	"absent": "ask fundamentals first: what problem it solves in this role's context, why it matters, and when it's used",
	"general": "keep the question conceptual and role-relevant",
}

DEFAULT_MIN_TURNS = 6
DEFAULT_MAX_TURNS = 9
DEFAULT_HARD_MAX_TURNS = 11


class CoverageTarget(TypedDict):
	focus_skill: str
	question_tier: str
	status: str  # "pending" | "covered"


class CoveragePlan(TypedDict):
	targets: list[CoverageTarget]
	quota: dict[str, int]
	min_turns: int
	max_turns: int
	hard_max_turns: int


def build_coverage_plan(
	allocation_plan: Sequence[Mapping[str, Any]],
	*,
	min_turns: int = DEFAULT_MIN_TURNS,
	max_turns: int = DEFAULT_MAX_TURNS,
	hard_max_turns: int = DEFAULT_HARD_MAX_TURNS,
) -> CoveragePlan:
	"""Turn a skill allocation plan into a mutable coverage plan."""

	targets: list[CoverageTarget] = []
	quota: dict[str, int] = {}

	for slot in allocation_plan:
		skill = str(slot.get("focus_skill") or "").strip()
		tier = str(slot.get("question_tier") or "general").strip().casefold()
		if not skill:
			continue
		targets.append(CoverageTarget(focus_skill=skill, question_tier=tier, status="pending"))
		quota[tier] = quota.get(tier, 0) + 1

	effective_min = max(1, min_turns)
	effective_max = max(effective_min, max_turns, len(targets))
	return CoveragePlan(
		targets=targets,
		quota=quota,
		min_turns=effective_min,
		max_turns=effective_max,
		hard_max_turns=max(effective_max, hard_max_turns),
	)


def pending_targets(plan: Mapping[str, Any]) -> list[CoverageTarget]:
	return [t for t in plan.get("targets") or [] if t.get("status") != "covered"]


def covered_targets(plan: Mapping[str, Any]) -> list[CoverageTarget]:
	return [t for t in plan.get("targets") or [] if t.get("status") == "covered"]


def next_target(plan: Mapping[str, Any]) -> CoverageTarget | None:
	"""The next skill to cover, absent-tier first.

	Absent-tier gaps are what the report's gap analysis is built on, so they are
	the ones worth spending a scarce turn on when the budget tightens.
	"""

	remaining = pending_targets(plan)
	if not remaining:
		return None
	priority = {"absent": 0, "mentioned": 1, "familiar": 2, "strong": 3, "general": 4}
	return min(remaining, key=lambda t: priority.get(str(t.get("question_tier")), 5))


def tier_progress(plan: Mapping[str, Any]) -> dict[str, dict[str, int]]:
	"""Per-tier {covered, target} counts, for the UI and for steering."""

	progress: dict[str, dict[str, int]] = {}
	for target in plan.get("targets") or []:
		tier = str(target.get("question_tier") or "general")
		bucket = progress.setdefault(tier, {"covered": 0, "target": 0})
		bucket["target"] += 1
		if target.get("status") == "covered":
			bucket["covered"] += 1
	return progress


def resolve_budget_pressure(
	plan: Mapping[str, Any],
	*,
	turns_used: int,
) -> BudgetPressure:
	"""How hard to push the interviewer toward closing out the plan."""

	max_turns = int(plan.get("max_turns") or DEFAULT_MAX_TURNS)
	min_turns = int(plan.get("min_turns") or DEFAULT_MIN_TURNS)
	remaining = max_turns - turns_used
	outstanding = len(pending_targets(plan))

	if remaining <= 1 and turns_used >= min_turns and outstanding == 0:
		return "wrap_up_now"
	if remaining <= 0:
		return "wrap_up_now" if turns_used >= min_turns else "tight"
	if remaining <= outstanding + 1:
		return "tight"
	return "normal"


def enforce_absent_coverage(plan: CoveragePlan, *, turns_used: int) -> CoveragePlan:
	"""Buy one extra turn rather than finish with an absent-tier gap unprobed.

	Strong and familiar tiers get no such extension: missing one costs detail,
	whereas missing an absent-tier skill removes a gap the report is meant to
	surface.
	"""

	pressure = resolve_budget_pressure(plan, turns_used=turns_used)
	if pressure != "wrap_up_now":
		return plan

	has_absent_gap = any(
		str(t.get("question_tier")) == "absent" for t in pending_targets(plan)
	)
	if not has_absent_gap:
		return plan

	hard_max = int(plan.get("hard_max_turns") or DEFAULT_HARD_MAX_TURNS)
	current_max = int(plan.get("max_turns") or DEFAULT_MAX_TURNS)
	if current_max >= hard_max:
		return plan

	extended = dict(plan)
	extended["max_turns"] = min(hard_max, current_max + 1)
	return CoveragePlan(**extended)  # type: ignore[arg-type]


def mark_covered(
	plan: CoveragePlan,
	*,
	focus_skill: str,
	question_tier: str,
	consumes_target: bool,
) -> CoveragePlan:
	"""Record that a turn covered a skill.

	A follow-up consumes nothing — it drills into the answer just given. When
	the interviewer goes off-plan the skill is appended as an already-covered
	target so tier counts still reflect what actually happened, rather than
	silently under-reporting.
	"""

	targets = [dict(t) for t in plan.get("targets") or []]
	if not consumes_target:
		return CoveragePlan(**{**plan, "targets": targets})  # type: ignore[arg-type]

	skill = str(focus_skill or "").strip()
	tier = str(question_tier or "general").strip().casefold()

	if skill:
		wanted = skill.casefold()
		for target in targets:
			if target.get("status") == "covered":
				continue
			if str(target.get("focus_skill", "")).casefold() == wanted:
				target["status"] = "covered"
				return CoveragePlan(**{**plan, "targets": targets})  # type: ignore[arg-type]

	# Off-plan question: record it so the tier counts stay truthful.
	targets.append(
		{"focus_skill": skill or "unspecified", "question_tier": tier, "status": "covered"}
	)
	return CoveragePlan(**{**plan, "targets": targets})  # type: ignore[arg-type]


def resolve_turn_target(
	plan: Mapping[str, Any],
	*,
	action: str,
	focus_skill: str,
	question_tier: str,
	parent_focus_skill: str | None = None,
	parent_question_tier: str | None = None,
) -> tuple[str, str]:
	"""Reconcile the model's self-reported skill and tier against the plan.

	The director labels its own turns, and those labels drift: it will call an
	absent-tier skill "strong", or rename the topic mid-follow-up. Those labels
	end up in `covered_topics`, which is what the report's tier analytics count,
	so the plan has to be the authority rather than the model.

	Two corrections:

	* A follow-up inherits its parent turn's skill and tier. It is drilling into
	  the answer just given, whatever it chooses to call it.
	* For any other action, if the named skill matches a planned target, the
	  plan's tier wins over the claimed one.
	"""

	claimed_skill = str(focus_skill or "").strip()
	claimed_tier = str(question_tier or "general").strip().casefold()

	if action == "follow_up" and (parent_focus_skill or parent_question_tier):
		return (
			str(parent_focus_skill or claimed_skill).strip(),
			str(parent_question_tier or claimed_tier).strip().casefold(),
		)

	if claimed_skill:
		wanted = claimed_skill.casefold()
		for target in plan.get("targets") or []:
			if str(target.get("focus_skill", "")).casefold() == wanted:
				return (
					str(target.get("focus_skill") or claimed_skill),
					str(target.get("question_tier") or claimed_tier).casefold(),
				)

	return claimed_skill, claimed_tier


def build_director_block(
	plan: Mapping[str, Any],
	*,
	turns_used: int,
	covered_topics: Sequence[Mapping[str, Any]] = (),
) -> str:
	"""Render the steering text appended to the director's final user message."""

	max_turns = int(plan.get("max_turns") or DEFAULT_MAX_TURNS)
	min_turns = int(plan.get("min_turns") or DEFAULT_MIN_TURNS)
	pressure = resolve_budget_pressure(plan, turns_used=turns_used)

	progress = tier_progress(plan)
	progress_text = (
		" | ".join(
			f"{tier} {counts['covered']}/{counts['target']}"
			for tier, counts in sorted(progress.items())
		)
		or "no tier plan"
	)

	covered_names = [
		str(t.get("focus_skill")) for t in covered_targets(plan) if t.get("focus_skill")
	]
	remaining = pending_targets(plan)
	remaining_text = (
		", ".join(f"{t['focus_skill']} ({t['question_tier']})" for t in remaining) or "none"
	)

	target = next_target(plan)
	if target is None:
		target_text = "none left - deepen an answered topic or close out"
	else:
		guidance = TIER_GUIDANCE.get(target["question_tier"], TIER_GUIDANCE["general"])
		target_text = f"{target['focus_skill']} ({target['question_tier']}) - {guidance}"

	if pressure == "wrap_up_now":
		action_text = "This is the final turn. Thank the candidate and close the interview."
	elif pressure == "tight":
		action_text = "Do not follow up. Move to the next target now."
	else:
		action_text = (
			"One follow-up on the current answer is allowed if it was incomplete "
			"or unusually strong. Otherwise move to the next target."
		)

	lines = [
		"COVERAGE DIRECTOR (steering, not a script)",
		f"Turns used        : {turns_used} of {max_turns} (minimum {min_turns})",
		f"Tier progress     : {progress_text}",
		f"Covered so far    : {', '.join(covered_names) or 'nothing yet'}",
		f"Remaining targets : {remaining_text}",
		f"Next target       : {target_text}",
		f"Budget pressure   : {pressure}",
		f"Guidance          : {action_text}",
	]

	recent = [str(t.get("question_text") or "").strip() for t in covered_topics[-3:]]
	recent = [t for t in recent if t]
	if recent:
		lines.append("Recently asked    : " + " / ".join(t[:70] for t in recent))

	return "\n".join(lines)


def fallback_question(plan: Mapping[str, Any]) -> str:
	"""A deterministic question for when the director stream fails outright.

	The round must never dead-end on a provider error: the candidate is mid
	interview and still deserves a next question.
	"""

	target = next_target(plan)
	if target is None:
		return "Thanks. Before we finish, what part of this role are you most confident about, and why?"
	skill = target["focus_skill"]
	return f"Let's move on to {skill}. Can you explain what {skill} is, and when you would reach for it?"


__all__ = [
	"CoveragePlan",
	"CoverageTarget",
	"DEFAULT_HARD_MAX_TURNS",
	"DEFAULT_MAX_TURNS",
	"DEFAULT_MIN_TURNS",
	"TIER_GUIDANCE",
	"build_coverage_plan",
	"build_director_block",
	"covered_targets",
	"enforce_absent_coverage",
	"fallback_question",
	"mark_covered",
	"next_target",
	"pending_targets",
	"resolve_budget_pressure",
	"resolve_turn_target",
	"tier_progress",
]
