"""Compact, bounded transcript for the conversational interview director.

Every director turn resends the conversation so far, so the transcript has to
stay small. The round session already accumulates `_response_history`, but those
are full evaluation documents — rubric, communication metrics, dimension
scores — and a handful of them would exhaust the prompt budget on their own.

This module keeps a parallel, deliberately thin record: who said what, plus the
few fields that steer the next question. Older turns fold into one-line digest
entries so the model keeps a sense of the whole interview without carrying its
full text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

TurnRole = Literal["interviewer", "candidate"]

# Roughly four characters per token. Deliberately crude: it only has to be a
# safe over-estimate, and a real tokenizer would mean loading one per turn.
_CHARS_PER_TOKEN = 4

MAX_DIALOGUE_TURNS = 40
_TRUNCATED_ANSWER_CHARS = 400
_DIGEST_ANSWER_CHARS = 90

_CANDIDATE_OPEN = "<candidate_answer>"
_CANDIDATE_CLOSE = "</candidate_answer>"

# Anything that could let a spoken answer impersonate protocol structure.
_INJECTION_MARKERS = ("<<<META>>>", _CANDIDATE_OPEN, _CANDIDATE_CLOSE)


class CompactTurn(TypedDict, total=False):
	"""One line of dialogue, trimmed to what the director actually needs."""

	i: int
	role: TurnRole
	text: str
	action: str | None
	focus_skill: str | None
	tier: str | None
	score: float | None
	at: str


def sanitize_candidate_text(text: str) -> str:
	"""Strip protocol markers from candidate speech before it enters a prompt.

	The candidate's words reach the model as transcribed audio. Without this a
	spoken "ignore previous instructions" wrapped in the protocol's own markers
	could end a turn early or fake metadata.
	"""

	cleaned = str(text or "")
	for marker in _INJECTION_MARKERS:
		cleaned = cleaned.replace(marker, " ")
	return " ".join(cleaned.split())


def make_interviewer_turn(
	*,
	index: int,
	text: str,
	action: str | None = None,
	focus_skill: str | None = None,
	tier: str | None = None,
	at: str = "",
) -> CompactTurn:
	return CompactTurn(
		i=index,
		role="interviewer",
		text=str(text or "").strip(),
		action=action,
		focus_skill=focus_skill,
		tier=tier,
		score=None,
		at=at,
	)


def make_candidate_turn(
	*,
	index: int,
	text: str,
	score: float | None = None,
	at: str = "",
) -> CompactTurn:
	return CompactTurn(
		i=index,
		role="candidate",
		text=sanitize_candidate_text(text),
		action=None,
		focus_skill=None,
		tier=None,
		score=score,
		at=at,
	)


def append_turn(
	dialogue: Sequence[CompactTurn],
	turn: CompactTurn,
	*,
	digest: Sequence[str] = (),
	max_turns: int = MAX_DIALOGUE_TURNS,
) -> tuple[list[CompactTurn], list[str]]:
	"""Append a turn, folding anything past `max_turns` into the digest."""

	turns = [*dialogue, turn]
	digest_lines = list(digest)

	while len(turns) > max_turns:
		overflow = turns.pop(0)
		line = summarize_turn(overflow)
		if line:
			digest_lines.append(line)

	return turns, digest_lines


def summarize_turn(turn: Mapping[str, Any]) -> str:
	"""Render one turn as a single digest line."""

	index = turn.get("i")
	text = str(turn.get("text") or "").strip()
	if not text:
		return ""

	snippet = text if len(text) <= _DIGEST_ANSWER_CHARS else text[:_DIGEST_ANSWER_CHARS].rstrip() + "..."

	if turn.get("role") == "interviewer":
		skill = str(turn.get("focus_skill") or "").strip()
		tier = str(turn.get("tier") or "").strip()
		label = f"{skill}({tier})" if skill and tier else skill or "general"
		return f"T{index} asked {label}: {snippet}"

	score = turn.get("score")
	scored = f", scored {float(score):.2f}" if isinstance(score, (int, float)) else ""
	return f"T{index} answered{scored}: {snippet}"


def _estimate_tokens(text: str) -> int:
	return max(1, len(text) // _CHARS_PER_TOKEN)


def _render_candidate(turn: Mapping[str, Any], *, max_chars: int | None = None) -> str:
	text = str(turn.get("text") or "").strip()
	if max_chars is not None and len(text) > max_chars:
		text = text[:max_chars].rstrip() + " [...]"

	body = f"{_CANDIDATE_OPEN}{text}{_CANDIDATE_CLOSE}"
	score = turn.get("score")
	if isinstance(score, (int, float)):
		body = f"{body}\n[score {float(score):.2f}]"
	return body


def build_messages(
	*,
	system: str,
	dialogue: Sequence[CompactTurn],
	digest: Sequence[str] = (),
	director_block: str = "",
	budget_tokens: int = 2500,
	verbatim_pairs: int = 6,
) -> list[dict[str, str]]:
	"""Assemble the director's message array within a token budget.

	Three rules shape the result:

	* Assistant turns carry spoken prose only, never the metadata tail. Echoing
	  the tail back teaches the model to speak JSON and doubles the history.
	* The steering block is attached to the final user message only, so the
	  earlier history stays byte-identical between turns and stale steering
	  cannot compete with current steering.
	* Candidate text is wrapped in tags and labelled as data, never instructions.
	"""

	messages: list[dict[str, str]] = [{"role": "system", "content": system}]

	verbatim_limit = max(0, verbatim_pairs) * 2
	if verbatim_limit and len(dialogue) > verbatim_limit:
		older = dialogue[: len(dialogue) - verbatim_limit]
		recent = list(dialogue[len(dialogue) - verbatim_limit :])
	else:
		older = []
		recent = list(dialogue)

	digest_lines = [*digest, *(line for line in (summarize_turn(t) for t in older) if line)]
	if digest_lines:
		messages.append(
			{
				"role": "user",
				"content": "EARLIER IN THIS INTERVIEW:\n" + "\n".join(digest_lines),
			}
		)

	rendered: list[dict[str, str]] = []
	for turn in recent:
		if turn.get("role") == "interviewer":
			text = str(turn.get("text") or "").strip()
			if text:
				rendered.append({"role": "assistant", "content": text})
		else:
			rendered.append({"role": "user", "content": _render_candidate(turn)})

	# Trim oldest candidate answers first if the budget is still exceeded. The
	# interviewer's own turns are never trimmed: it has to know what it asked.
	def _total_tokens(parts: Sequence[Mapping[str, str]]) -> int:
		return sum(_estimate_tokens(part["content"]) for part in parts)

	overhead = _estimate_tokens(system) + _estimate_tokens(director_block)
	if digest_lines:
		overhead += _estimate_tokens(messages[-1]["content"])

	for index, part in enumerate(rendered):
		if overhead + _total_tokens(rendered) <= budget_tokens:
			break
		if part["role"] != "user":
			continue
		source = recent[index]
		if source.get("role") != "candidate":
			continue
		rendered[index] = {
			"role": "user",
			"content": _render_candidate(source, max_chars=_TRUNCATED_ANSWER_CHARS),
		}

	messages.extend(rendered)

	if director_block:
		if messages[-1]["role"] == "user":
			messages[-1] = {
				"role": "user",
				"content": messages[-1]["content"] + "\n\n" + director_block,
			}
		else:
			messages.append({"role": "user", "content": director_block})

	return messages


__all__ = [
	"CompactTurn",
	"MAX_DIALOGUE_TURNS",
	"append_turn",
	"build_messages",
	"make_candidate_turn",
	"make_interviewer_turn",
	"sanitize_candidate_text",
	"summarize_turn",
]
