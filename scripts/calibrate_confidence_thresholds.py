"""Calibrate interview confidence thresholds from saved interview transcripts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.db_errors import (
	DatabaseClientError,
	DatabaseConfigurationError,
	DatabaseDependencyError,
)
from backend.database.mongo_client import get_repository
from backend.nlp.feedback_generator import (
	calibrate_confidence_thresholds,
	summarize_confidence_signal,
)

SUPPORTED_ROUNDS = ("technical", "project_discussion", "hr")


def _is_missing_table_error(exc: Exception, table_name: str) -> bool:
	text = str(exc).strip().lower()
	return (
		("could not find the table" in text or "schema cache" in text or "does not exist" in text)
		and f"public.{table_name.lower()}" in text
	)


def _row_matches_rounds(row: dict[str, Any], rounds: set[str]) -> bool:
	round_name = str(row.get("round") or "").strip()
	answer_text = str(row.get("user_answer_text") or "").strip()
	return round_name in rounds and bool(answer_text)


def _fetch_rows_from_round_sessions(rounds: set[str]) -> list[dict[str, Any]]:
	repo = get_repository()
	data = list(
		repo.db["interview_round_sessions"]
		.find({}, {"session_id": 1, "round": 1, "questions_json": 1, "created_at": 1})
		.sort("created_at", 1)
	)
	rows: list[dict[str, Any]] = []
	for row in data:
		if not isinstance(row, dict):
			continue
		round_name = str(row.get("round") or "").strip()
		if round_name not in rounds:
			continue
		questions_json = row.get("questions_json") or {}
		if not isinstance(questions_json, dict):
			continue
		for history_item in questions_json.get("_response_history") or []:
			if not isinstance(history_item, dict):
				continue
			normalized = dict(history_item)
			normalized.setdefault("session_id", row.get("session_id"))
			normalized.setdefault("round", round_name)
			normalized.setdefault("created_at", row.get("created_at"))
			if _row_matches_rounds(normalized, rounds):
				rows.append(normalized)
	return rows


def _fetch_rows(rounds: set[str]) -> list[dict[str, Any]]:
	repo = get_repository()
	try:
		data = list(repo.db["interview_responses"].find({}).sort("created_at", 1))
	except Exception as exc:
		if _is_missing_table_error(exc, "interview_responses"):
			return _fetch_rows_from_round_sessions(rounds)
		raise
	rows: list[dict[str, Any]] = []
	for row in data:
		if not isinstance(row, dict):
			continue
		normalized = dict(row)
		if _row_matches_rounds(normalized, rounds):
			rows.append(normalized)
	return rows


def _build_summary(*, rounds: tuple[str, ...], min_samples: int) -> dict[str, Any]:
	rows = _fetch_rows(set(rounds))
	rows_by_round: dict[str, list[dict[str, Any]]] = {round_name: [] for round_name in rounds}
	for row in rows:
		rows_by_round.setdefault(str(row.get("round") or ""), []).append(row)

	summary: dict[str, Any] = {
		"total_rows": len(rows),
		"rounds": {},
		"calibration_changes_scoring": False,
	}
	for round_name in rounds:
		records = rows_by_round.get(round_name, [])
		thresholds = calibrate_confidence_thresholds(
			round_name,
			records,
			min_samples=min_samples,
		)
		signal = summarize_confidence_signal(
			round_name,
			records,
			min_calibration_samples=min_samples,
		)
		summary["rounds"][round_name] = {
			"sample_count": len(records),
			"average_confidence": signal["average_confidence"],
			"average_hedging_ratio": signal["average_hedging_ratio"],
			"dominant_confidence_label": signal["dominant_confidence_label"],
			"dominant_sentiment_label": signal["dominant_sentiment_label"],
			"hesitation_detected": signal["hesitation_detected"],
			"thresholds": thresholds,
		}
	return summary


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--round",
		action="append",
		dest="rounds",
		choices=SUPPORTED_ROUNDS,
		help="Limit calibration to one or more rounds. Defaults to all interview rounds.",
	)
	parser.add_argument(
		"--min-samples",
		type=int,
		default=12,
		help="Minimum transcript count required before transcript-percentile calibration replaces defaults.",
	)
	parser.add_argument(
		"--json",
		action="store_true",
		help="Print the calibration summary as JSON.",
	)
	args = parser.parse_args(argv)

	rounds = tuple(args.rounds or SUPPORTED_ROUNDS)
	try:
		summary = _build_summary(rounds=rounds, min_samples=max(1, int(args.min_samples)))
	except (DatabaseConfigurationError, DatabaseDependencyError, DatabaseClientError) as exc:
		print(f"Confidence calibration failed: {exc}", file=sys.stderr)
		return 1

	if args.json:
		print(json.dumps(summary, indent=2))
		return 0

	print(
		f"Reviewed {summary['total_rows']} saved interview transcripts across "
		f"{len(summary['rounds'])} rounds."
	)
	for round_name, details in summary["rounds"].items():
		thresholds = details["thresholds"]
		print(
			f"{round_name}: samples={details['sample_count']}, "
			f"avg_confidence={details['average_confidence']:.2f}, "
			f"hesitant<={thresholds['hesitant_max']:.2f}, "
			f"confident>={thresholds['confident_min']:.2f}, "
			f"source={thresholds['calibration_source']}"
		)
	print("These thresholds are for narration calibration only; scoring weights remain unchanged.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())