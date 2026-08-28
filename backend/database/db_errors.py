"""Database-agnostic error classes and domain enums."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class DatabaseClientError(RuntimeError):
	"""Base error for database client failures."""


class DatabaseConfigurationError(DatabaseClientError):
	"""Raised when required database configuration is missing."""


class DatabaseDependencyError(DatabaseClientError):
	"""Raised when the database SDK is unavailable."""


class RecordNotFoundError(DatabaseClientError):
	"""Raised when a requested record cannot be found."""


class ConcurrentUpdateError(DatabaseClientError):
	"""Raised when optimistic concurrency checks fail."""


class DSAStage(StrEnum):
	"""Canonical DSA stage values stored in the database."""

	PROBLEM_SETUP = "problem_setup"
	APPROACH_DISCUSSION = "approach_discussion"
	CODING = "coding"
	OPTIMIZATION = "optimization"
	DEBRIEF = "debrief"
	COMPLETE = "complete"


class UnderstandingLevel(StrEnum):
	LOW = "low"
	MEDIUM = "medium"
	HIGH = "high"


class OptimalityStatus(StrEnum):
	YES = "yes"
	NO = "no"
	UNKNOWN = "unknown"


class ExplanationQuality(StrEnum):
	VAGUE = "vague"
	PARTIAL = "partial"
	CLEAR = "clear"


class JudgeStatus(StrEnum):
	NOT_RUN = "not_run"
	RUNNING = "running"
	ACCEPTED = "ac"
	WRONG_ANSWER = "wa"
	TIME_LIMIT = "tle"
	RUNTIME_ERROR = "re"
	COMPILE_ERROR = "ce"


def _utcnow_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def build_default_candidate_model() -> dict[str, Any]:
	"""Return a consistent candidate model for new DSA sessions."""

	return {
		"understands_problem": UnderstandingLevel.LOW.value,
		"identified_correct_approach": False,
		"mentioned_optimal_data_structure": False,
		"considered_edge_cases_in_approach": False,
		"actual_approach_from_code": None,
		"code_handles_edge_cases": False,
		"is_solution_optimal": OptimalityStatus.UNKNOWN.value,
		"complexity_stated_correctly": False,
		"explanation_quality": ExplanationQuality.VAGUE.value,
		"seems_stuck": False,
		"time_pressure_visible": False,
	}


def build_default_dsa_state(
	*,
	stage: DSAStage | str = DSAStage.PROBLEM_SETUP,
	stage_started_at: str | None = None,
	deadline_at: str | None = None,
	awaiting_user_input: bool = True,
) -> dict[str, Any]:
	"""Build the baseline state persisted in dsa_sessions.state_json."""

	stage_value = str(stage)
	return {
		"stage": stage_value,
		"state_version": 1,
		"stage_started_at": stage_started_at or _utcnow_iso(),
		"deadline_at": deadline_at,
		"clarification_count": 0,
		"approach_exchange_count": 0,
		"hint_level": 0,
		"optimization_used": False,
		"last_submission_id": None,
		"last_judge_status": JudgeStatus.NOT_RUN.value,
		"awaiting_user_input": awaiting_user_input,
		"candidate_model": build_default_candidate_model(),
	}
