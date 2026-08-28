"""Verify the certified DSA problem bank and deterministic selector behavior."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from backend.dsa.problem_selector import (
	CertifiedProblemBankError,
	ProblemSelectionError,
	list_certified_problems,
	list_supported_dsa_role_keys,
	select_problem_pair,
)


def _build_summary(*, session_prefix: str) -> dict[str, Any]:
	problems = list_certified_problems()
	roles = list_supported_dsa_role_keys()
	role_checks: list[dict[str, str]] = []

	for role_key in roles:
		q1_problem, q2_problem = select_problem_pair(
			role_key,
			session_id=f"{session_prefix}:{role_key}",
		)
		if q1_problem.problem_id == q2_problem.problem_id:
			raise ProblemSelectionError(
				f"Role {role_key} received the same problem for both question slots."
			)
		role_checks.append(
			{
				"role_key": role_key,
				"q1_problem_id": q1_problem.problem_id,
				"q1_difficulty": q1_problem.difficulty,
				"q2_problem_id": q2_problem.problem_id,
				"q2_difficulty": q2_problem.difficulty,
			}
		)

	difficulty_counts: dict[str, int] = {}
	for problem in problems:
		difficulty_counts[problem.difficulty] = difficulty_counts.get(problem.difficulty, 0) + 1

	return {
		"problem_count": len(problems),
		"supported_role_count": len(roles),
		"difficulty_counts": difficulty_counts,
		"role_checks": role_checks,
	}


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--session-prefix",
		default="verification",
		help="Deterministic prefix used while selecting Q1 and Q2 pairs.",
	)
	parser.add_argument(
		"--json",
		action="store_true",
		help="Print the full verification summary as JSON.",
	)
	args = parser.parse_args(argv)

	try:
		summary = _build_summary(session_prefix=args.session_prefix)
	except (CertifiedProblemBankError, ProblemSelectionError) as exc:
		print(f"DSA problem bank verification failed: {exc}", file=sys.stderr)
		return 1

	if args.json:
		print(json.dumps(summary, indent=2))
		return 0

	print(
		f"Verified {summary['problem_count']} certified DSA problems across "
		f"{summary['supported_role_count']} DSA-enabled roles."
	)
	print(f"Difficulty distribution: {summary['difficulty_counts']}")
	for role_check in summary["role_checks"]:
		print(
			f"{role_check['role_key']}: "
			f"Q1={role_check['q1_problem_id']} ({role_check['q1_difficulty']}), "
			f"Q2={role_check['q2_problem_id']} ({role_check['q2_difficulty']})"
		)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
