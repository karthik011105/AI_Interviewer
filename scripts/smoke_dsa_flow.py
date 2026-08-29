"""Integration smoke test for the persisted DSA flow.

This script exercises the FastAPI app in-process while using the real
Supabase repository and real Judge0 execution path.

Prerequisites:
- SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must point to a real project
- DSA_SMOKE_USER_ID must be set to an existing auth user id in that project
- Judge0 must be reachable at the configured backend base URL

Run it with:
e:/interview_simulator/.venv/Scripts/python.exe scripts/smoke_dsa_flow.py
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
	sys.path.insert(0, str(WORKSPACE_ROOT))

from backend.api.auth import AuthenticatedUser, require_current_user
from backend.database.queries import create_session
from backend.database.db_errors import DatabaseClientError
from backend.dsa.code_executor import judge0_health_check
from backend.dsa.problem_selector import get_certified_problem
from backend.main import app


class SmokePrerequisiteError(RuntimeError):
	"""Raised when the environment is missing required external prerequisites."""


def _skip(reason: str) -> int:
	print(f"dsa smoke test skipped: {reason}")
	return 0


def _build_current_user(user_id: str) -> AuthenticatedUser:
	payload = {
		"id": user_id,
		"email": "dsa-smoke@example.com",
		"app_metadata": {"provider": "smoke-test"},
		"user_metadata": {},
	}
	return AuthenticatedUser(user_id=user_id, email="dsa-smoke@example.com", raw_user=payload)


def _assert_ok(response):
	payload = response.json()
	detail = payload.get("detail") if isinstance(payload, dict) else None
	if isinstance(detail, str) and "Supabase table 'dsa_sessions' is missing" in detail:
		raise SmokePrerequisiteError(
			"Supabase table 'dsa_sessions' is missing. Apply backend/database/dsa_sessions.sql in the target project first."
		)
	assert response.status_code == 200, payload
	return payload


def _assert_all_cases_passed(*, label: str, payload: dict[str, object], problem_id: str) -> None:
	execution_results = payload.get("execution_results")
	assert isinstance(execution_results, dict), payload
	passed_count = execution_results.get("passed_count")
	total_count = execution_results.get("total_count")
	assert passed_count == total_count, (
		f"{label} failed for problem_id={problem_id}: "
		f"passed_count={passed_count}, total_count={total_count}, execution_results={execution_results}"
	)


def main() -> int:
	user_id = str(os.getenv("DSA_SMOKE_USER_ID") or "").strip()
	if not user_id:
		return _skip("set DSA_SMOKE_USER_ID to a real Supabase auth user id first")

	judge0 = judge0_health_check()
	if not judge0.get("available"):
		return _skip(str(judge0.get("detail") or "Judge0 is unavailable"))

	role_key = str(os.getenv("DSA_SMOKE_ROLE_KEY") or "backend_python_developer").strip() or "backend_python_developer"
	current_user = _build_current_user(user_id)
	original_overrides = dict(app.dependency_overrides)

	try:
		parent_session = create_session(user_id=user_id, role_selected=role_key, status="dsa_smoke_test")
	except DatabaseClientError as exc:
		return _skip(str(exc))

	app.dependency_overrides[require_current_user] = lambda: current_user

	try:
		client = TestClient(app)
		session_id = str(parent_session["id"])

		start_payload = _assert_ok(
			client.post(
				"/dsa/start",
				json={
					"session_id": session_id,
					"question_number": 1,
					"role_key": role_key,
				},
			)
		)
		assert start_payload["created_dsa_session"] is True
		problem = get_certified_problem(start_payload["problem_id"])
		correct_code = problem.reference_solution
		print(f"q1_problem_id={problem.problem_id}")

		approach_message = "I will normalize the input, derive the invariant, and then print the final answer from solve()."
		message_payload = _assert_ok(
			client.post(
				"/dsa/message",
				json={
					"session_id": session_id,
					"question_number": 1,
					"message": approach_message,
					"message_kind": "approach",
				},
			)
		)
		assert message_payload["message_channel"] == "approach"
		assert message_payload["stage"] == "approach_discussion"
		assert approach_message in str(message_payload["approach_text"])

		run_payload = _assert_ok(
			client.post(
				"/dsa/run",
				json={
					"session_id": session_id,
					"question_number": 1,
					"code": correct_code,
				},
			)
		)
		assert run_payload["submission"]["submission_kind"] == "sample_run"
		assert len(run_payload["all_code_submissions"]) == 1
		_assert_all_cases_passed(label="sample run", payload=run_payload, problem_id=problem.problem_id)

		submit_payload = _assert_ok(
			client.post(
				"/dsa/submit",
				json={
					"session_id": session_id,
					"question_number": 1,
					"code": correct_code,
				},
			)
		)
		assert submit_payload["submission"]["submission_kind"] == "final_submit"
		assert submit_payload["next_stage"] == "debrief"
		assert len(submit_payload["all_code_submissions"]) == 2
		_assert_all_cases_passed(label="final submit", payload=submit_payload, problem_id=problem.problem_id)

		debrief_message = "The reference solution passed the hidden tests, and the debrief should now persist against this session."
		debrief_payload = _assert_ok(
			client.post(
				"/dsa/message",
				json={
					"session_id": session_id,
					"question_number": 1,
					"message": debrief_message,
					"message_kind": "debrief",
				},
			)
		)
		assert debrief_payload["message_channel"] == "debrief"
		assert debrief_payload["stage"] == "complete"
		assert debrief_payload["progress_summary"]["completed_question_count"] == 1
		assert debrief_payload["progress_summary"]["round_completed"] is False

		q1_final_payload = _assert_ok(client.get(f"/dsa/session/{session_id}/1"))
		q1_state_json = q1_final_payload["state_json"]
		assert q1_final_payload["stage"] == "complete"
		assert q1_final_payload["current_code_draft"] == correct_code
		assert q1_final_payload["last_submission_id"] == submit_payload["submission"]["submission_id"]
		assert len(q1_final_payload["all_code_submissions"]) == 2
		assert len(q1_state_json.get("approach_messages") or []) >= 1
		assert len(q1_state_json.get("debrief_messages") or []) >= 1
		_assert_all_cases_passed(label="q1 final session state", payload=q1_final_payload, problem_id=problem.problem_id)
		assert q1_final_payload["execution_results"]["total_count"] > 0

		q2_start_payload = _assert_ok(
			client.post(
				"/dsa/start",
				json={
					"session_id": session_id,
					"question_number": 2,
					"role_key": role_key,
				},
			)
		)
		assert q2_start_payload["question_number"] == 2
		q2_problem = get_certified_problem(q2_start_payload["problem_id"])
		q2_correct_code = q2_problem.reference_solution
		print(f"q2_problem_id={q2_problem.problem_id}")

		q2_approach_message = "I will discuss the invariant, then implement the final solution for question two."
		q2_message_payload = _assert_ok(
			client.post(
				"/dsa/message",
				json={
					"session_id": session_id,
					"question_number": 2,
					"message": q2_approach_message,
					"message_kind": "approach",
				},
			)
		)
		assert q2_message_payload["message_channel"] == "approach"
		assert q2_message_payload["stage"] == "approach_discussion"

		q2_run_payload = _assert_ok(
			client.post(
				"/dsa/run",
				json={
					"session_id": session_id,
					"question_number": 2,
					"code": q2_correct_code,
				},
			)
		)
		assert q2_run_payload["submission"]["submission_kind"] == "sample_run"
		_assert_all_cases_passed(label="q2 sample run", payload=q2_run_payload, problem_id=q2_problem.problem_id)

		q2_submit_payload = _assert_ok(
			client.post(
				"/dsa/submit",
				json={
					"session_id": session_id,
					"question_number": 2,
					"code": q2_correct_code,
				},
			)
		)
		assert q2_submit_payload["submission"]["submission_kind"] == "final_submit"
		assert q2_submit_payload["next_stage"] == "debrief"
		_assert_all_cases_passed(label="q2 final submit", payload=q2_submit_payload, problem_id=q2_problem.problem_id)

		q2_debrief_payload = _assert_ok(
			client.post(
				"/dsa/message",
				json={
					"session_id": session_id,
					"question_number": 2,
					"message": "Question two is complete and should finalize the DSA round summary.",
					"message_kind": "debrief",
				},
			)
		)
		assert q2_debrief_payload["message_channel"] == "debrief"
		assert q2_debrief_payload["stage"] == "complete"
		assert q2_debrief_payload["progress_summary"]["completed_question_count"] == 2
		assert q2_debrief_payload["progress_summary"]["round_completed"] is True
		assert q2_debrief_payload["progress_summary"]["aggregate_score"] is not None
		assert q2_debrief_payload["report_state"]["session_status"] == "dsa_complete"

		final_payload = _assert_ok(client.get(f"/dsa/session/{session_id}/2"))
		state_json = final_payload["state_json"]
		assert final_payload["stage"] == "complete"
		assert final_payload["current_code_draft"] == q2_correct_code
		assert final_payload["last_submission_id"] == q2_submit_payload["submission"]["submission_id"]
		assert len(final_payload["all_code_submissions"]) == 2
		assert len(state_json.get("approach_messages") or []) >= 1
		assert len(state_json.get("debrief_messages") or []) >= 1
		_assert_all_cases_passed(label="q2 final session state", payload=final_payload, problem_id=q2_problem.problem_id)
		assert final_payload["execution_results"]["total_count"] > 0
		assert final_payload["progress_summary"]["round_completed"] is True
		assert final_payload["progress_summary"]["aggregate_score"] is not None

		print("dsa smoke test passed")
		print(f"session_id={session_id}")
		print(f"problem_id={final_payload['problem_id']}")
		print(f"stage={final_payload['stage']}")
		print(f"submissions={len(final_payload['all_code_submissions'])}")
		return 0
	except SmokePrerequisiteError as exc:
		return _skip(str(exc))
	finally:
		app.dependency_overrides.clear()
		app.dependency_overrides.update(original_overrides)


if __name__ == "__main__":
	raise SystemExit(main())