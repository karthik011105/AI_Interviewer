"""Interview engine selection.

An engine decides what happens once a candidate turn has been transcribed:
the scripted engine scores it against a pre-generated question, while the
conversational engine also chooses the next question from the dialogue.

Which engine a round uses is controlled by INTERVIEW_DYNAMIC_ROUNDS, so the
new behavior can be turned off entirely without a code change.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from backend.api.interview_engines import scripted


@dataclass(frozen=True)
class InterviewEngine:
	"""The hooks the shared transport calls into."""

	name: str
	start: Callable[[Any], Awaitable[None]]
	handle_transcript: Callable[[Any, str], Awaitable[None]]
	handle_control: Callable[[Any, dict[str, Any]], Awaitable[None]]


SCRIPTED_ENGINE = InterviewEngine(
	name="scripted",
	start=scripted.start,
	handle_transcript=scripted.handle_transcript,
	handle_control=scripted.handle_control,
)


def dynamic_rounds() -> frozenset[str]:
	"""Rounds configured to use the conversational engine."""

	raw = os.getenv("INTERVIEW_DYNAMIC_ROUNDS", "")
	return frozenset(part.strip().casefold() for part in raw.split(",") if part.strip())


def select_engine(round_type: str) -> InterviewEngine:
	"""Return the engine for a round.

	Falls back to the scripted engine whenever the dynamic engine is not
	configured or not importable, so a misconfiguration degrades to the old
	behavior instead of failing the connection.
	"""

	if str(round_type or "").strip().casefold() not in dynamic_rounds():
		return SCRIPTED_ENGINE

	try:
		from backend.api.interview_engines import dynamic
	except ImportError:
		return SCRIPTED_ENGINE

	return InterviewEngine(
		name="dynamic",
		start=dynamic.start,
		handle_transcript=dynamic.handle_transcript,
		handle_control=dynamic.handle_control,
	)


__all__ = ["InterviewEngine", "SCRIPTED_ENGINE", "dynamic_rounds", "select_engine"]
