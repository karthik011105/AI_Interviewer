"""Tests for the conversational interview engine and its mode boundaries.

The highest-value invariant here is that the two engines never inherit each
other's state: a conversational round that adopts a scripted batch starts
mid-count and burns its whole turn budget before asking anything, which is a
failure that only shows up as "the interview ended early".
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from backend.api import interview_runtime
from backend.api.interview_engines import dynamic
from backend.nlp.coverage_director import build_coverage_plan, pending_targets, tier_progress
from backend.nlp.interview_director import DirectorTurn

_ALLOCATION = [
	{"focus_skill": "data structures", "question_tier": "absent"},
	{"focus_skill": "sql", "question_tier": "absent"},
	{"focus_skill": "programming", "question_tier": "familiar"},
]


def _turn(**overrides) -> DirectorTurn:
	base = DirectorTurn(
		say="What is a B-tree and when would you use one?",
		action="new_topic",
		focus_skill="data structures",
		question_tier="absent",
		difficulty="medium",
		topic_key="b-tree",
		ideal_points=["balanced tree", "range scans", "disk friendly"],
		covers_target=True,
	)
	base.update(overrides)  # type: ignore[arg-type]
	return base


class QuestionItemShapeTests(TestCase):
	def test_turn_is_shaped_like_a_generated_question(self) -> None:
		# The report, _get_question_at_index, and covered-topic derivation all
		# read this shape. Diverging from it would need a schema migration.
		item = dynamic._question_item(_turn())

		for key in ("question", "ideal_points", "follow_up", "difficulty"):
			self.assertIn(key, item, msg=key)
		self.assertEqual(item["question"], "What is a B-tree and when would you use one?")
		self.assertEqual(item["question_tier"], "absent")
		self.assertEqual(item["focus_skill"], "data structures")
		self.assertEqual(item["turn_action"], "new_topic")
		self.assertEqual(item["follow_up"], "")

	def test_ideal_points_survive_for_scoring(self) -> None:
		item = dynamic._question_item(_turn())
		# These feed evaluate_answer; losing them silently degrades scoring.
		self.assertEqual(len(item["ideal_points"]), 3)


class RoundStateTests(TestCase):
	def _runtime(self, questions_json: dict) -> SimpleNamespace:
		return SimpleNamespace(
			session_id="s1",
			round_type="technical",
			parent_session={"role_selected": "software_engineer"},
			questions_json=questions_json,
			engine_state={},
			round_record={"state_version": 1, "difficulty_signal": 0.5},
		)

	def test_round_state_is_seeded_once(self) -> None:
		runtime = self._runtime({})
		context = {"absent_skills": ["sql"], "skill_profile": {"role_title": "Engineer"}}

		with patch.object(dynamic, "_get_parsed_resume", return_value={}), patch.object(
			dynamic, "_build_round_context", return_value=context
		):
			dynamic._ensure_round_state(runtime)  # type: ignore[arg-type]

		self.assertEqual(runtime.questions_json["mode"], "dynamic")
		self.assertEqual(runtime.questions_json["questions"], [])
		self.assertIn("coverage_plan", runtime.questions_json)
		self.assertIn("system", runtime.engine_state)

	def test_existing_plan_is_not_rebuilt(self) -> None:
		plan = build_coverage_plan(_ALLOCATION)
		runtime = self._runtime({"mode": "dynamic", "coverage_plan": plan, "questions": []})

		with patch.object(dynamic, "_get_parsed_resume", return_value={}), patch.object(
			dynamic, "_build_round_context", return_value={}
		):
			dynamic._ensure_round_state(runtime)  # type: ignore[arg-type]

		self.assertEqual(runtime.questions_json["coverage_plan"], plan)

	def test_turns_used_counts_accreted_questions(self) -> None:
		runtime = self._runtime({"questions": [{}, {}, {}]})
		self.assertEqual(dynamic._turns_used(runtime), 3)  # type: ignore[arg-type]


class ModeTransitionTests(TestCase):
	"""_load_or_create_round_session must never mix the two engines' state."""

	SCRIPTED = {
		"id": "r1",
		"status": "in_progress",
		"current_question_index": 6,
		"questions_json": {"questions": [{"question": f"q{i}"} for i in range(6)]},
	}
	DYNAMIC = {
		"id": "r1",
		"status": "in_progress",
		"current_question_index": 2,
		"questions_json": {"mode": "dynamic", "questions": [{"question": "q0"}, {"question": "q1"}]},
	}

	def _load(self, existing, *, dynamic_rounds: str):
		created: dict = {}

		def _create(**kwargs):
			created.update(kwargs)
			return {"id": "new", "current_question_index": 0, "state_version": 1,
			        "questions_json": kwargs["questions_json"]}

		with patch.dict(os.environ, {"INTERVIEW_DYNAMIC_ROUNDS": dynamic_rounds}, clear=False), \
			patch.object(interview_runtime, "get_interview_round_session", return_value=existing), \
			patch.object(interview_runtime, "_get_parsed_resume", return_value={}), \
			patch.object(interview_runtime, "_build_round_context", return_value={}), \
			patch.object(interview_runtime, "_prepare_questions_json_for_persistence",
			             side_effect=lambda _r, qj, _c: dict(qj)), \
			patch.object(interview_runtime, "get_or_generate_questions",
			             return_value={"questions": [{"question": "gen"}]}), \
			patch.object(interview_runtime, "create_interview_round_session", side_effect=_create):
			record, questions_json = interview_runtime._load_or_create_round_session(
				session_id="s1", round_type="technical", parent_session={},
			)
		return record, questions_json, created

	def test_scripted_round_is_not_adopted_by_the_dynamic_engine(self) -> None:
		# Regression: the conversational engine appended onto a scripted batch,
		# so its first turn was index 6 and the round ended after four turns.
		_record, questions_json, created = self._load(self.SCRIPTED, dynamic_rounds="technical")

		self.assertTrue(created, "a fresh dynamic round should have been created")
		self.assertEqual(questions_json.get("mode"), "dynamic")
		self.assertEqual(questions_json.get("questions"), [])

	def test_dynamic_round_in_progress_is_resumed(self) -> None:
		record, questions_json, created = self._load(self.DYNAMIC, dynamic_rounds="technical")

		self.assertFalse(created, "an in-progress dynamic round must not be rebuilt")
		self.assertEqual(len(questions_json["questions"]), 2)
		self.assertEqual(record["current_question_index"], 2)

	def test_dynamic_round_is_rebuilt_when_the_flag_is_removed(self) -> None:
		_record, questions_json, created = self._load(self.DYNAMIC, dynamic_rounds="")

		self.assertTrue(created)
		self.assertNotEqual(questions_json.get("mode"), "dynamic")

	def test_completed_round_is_never_rebuilt(self) -> None:
		finished = {**self.SCRIPTED, "status": "complete"}
		_record, questions_json, created = self._load(finished, dynamic_rounds="technical")

		self.assertFalse(created, "a finished round must be returned as-is")
		self.assertEqual(len(questions_json["questions"]), 6)

	def test_new_dynamic_round_gets_a_coverage_plan_up_front(self) -> None:
		# The connect frame reports max_turns as the turn budget, so the plan
		# has to exist before the first question is asked.
		# The allocator is imported inside the function, so patch it at source.
		from backend.nlp import question_generator

		with patch.object(question_generator, "_build_skill_allocation_plan", return_value=_ALLOCATION):
			_record, questions_json, created = self._load(None, dynamic_rounds="technical")

		self.assertTrue(created)
		plan = questions_json.get("coverage_plan")
		self.assertIsNotNone(plan)
		self.assertGreater(int(plan["max_turns"]), 0)


class _ControlRuntime:
	def __init__(self) -> None:
		self.state = "LISTENING"
		self.prompt_phase = "idle"
		self.interrupt_event = asyncio.Event()
		self.tts_task = None
		self.turn_commit_task = None
		self.pending_pcm_frames: list[bytes] = []
		self.clarification_mode = False
		self.sent: list[dict] = []
		self.questions_json = {"mode": "dynamic", "questions": []}

	async def send_json(self, payload: dict) -> None:
		self.sent.append(payload)

	async def set_state(self, state: str) -> None:
		self.state = state

	def reset_audio_capture(self) -> None:
		self.pending_pcm_frames.clear()

	def types(self) -> list[str]:
		return [item["type"] for item in self.sent]


class ControlHandlingTests(TestCase):
	def test_request_next_is_a_no_op_rather_than_an_error(self) -> None:
		# The conversational engine advances on its own, but a client written
		# for the scripted engine still sends request_next.
		runtime = _ControlRuntime()
		asyncio.run(dynamic.handle_control(runtime, {"type": "request_next"}))  # type: ignore[arg-type]

		self.assertNotIn("error", runtime.types())
		self.assertIn("info", runtime.types())

	def test_end_answer_without_audio_reports_it(self) -> None:
		runtime = _ControlRuntime()
		asyncio.run(dynamic.handle_control(runtime, {"type": "end_answer"}))  # type: ignore[arg-type]

		errors = [m for m in runtime.sent if m["type"] == "error"]
		self.assertEqual(errors[0]["code"], "no_answer_audio")

	def test_unknown_message_is_rejected(self) -> None:
		runtime = _ControlRuntime()
		asyncio.run(dynamic.handle_control(runtime, {"type": "nonsense"}))  # type: ignore[arg-type]

		errors = [m for m in runtime.sent if m["type"] == "error"]
		self.assertEqual(errors[0]["code"], "unknown_message")

	def test_ping_still_works(self) -> None:
		runtime = _ControlRuntime()
		asyncio.run(dynamic.handle_control(runtime, {"type": "ping"}))  # type: ignore[arg-type]
		self.assertIn("pong", runtime.types())


class CoverageIntegrationTests(TestCase):
	def test_walking_the_plan_covers_every_tier(self) -> None:
		plan = build_coverage_plan(_ALLOCATION)
		from backend.nlp.coverage_director import mark_covered, next_target

		turns = 0
		while pending_targets(plan) and turns < plan["hard_max_turns"]:
			target = next_target(plan)
			plan = mark_covered(
				plan,
				focus_skill=target["focus_skill"],
				question_tier=target["question_tier"],
				consumes_target=True,
			)
			turns += 1

		for tier, counts in tier_progress(plan).items():
			self.assertEqual(counts["covered"], counts["target"], msg=tier)


class ConcurrentPersistenceTests(TestCase):
	"""The director and the evaluator write questions_json at the same time.

	`_persist` applies its mutation inside the lock against whatever is current,
	so neither writer can overwrite the other with a snapshot taken earlier.
	"""

	def _runtime(self) -> SimpleNamespace:
		return SimpleNamespace(
			session_id="s1",
			round_type="technical",
			questions_json={"mode": "dynamic", "questions": [], "_dialogue": []},
			round_record={"state_version": 1, "difficulty_signal": 0.5},
			persist_lock=asyncio.Lock(),
			current_index=0,
			engine_state={},
		)

	def test_concurrent_mutations_are_both_applied(self) -> None:
		runtime = self._runtime()

		def _advance(**kwargs):
			# Stand in for the DB: echo the document back with a bumped version.
			return {
				"state_version": kwargs["expected_state_version"] + 1,
				"current_question_index": kwargs["new_index"],
				"difficulty_signal": kwargs["difficulty_signal"],
				"questions_json": kwargs["questions_json"],
			}

		def _append_question(current):
			current["questions"] = [*current.get("questions", []), {"question": "q"}]
			return current

		def _append_dialogue(current):
			current["_dialogue"] = [*current.get("_dialogue", []), {"role": "candidate"}]
			return current

		async def _run():
			await asyncio.gather(
				dynamic._persist(runtime, _append_question),
				dynamic._persist(runtime, _append_dialogue),
				dynamic._persist(runtime, _append_question),
			)

		with patch.object(dynamic, "advance_interview_question", side_effect=_advance):
			asyncio.run(_run())

		# All three mutations survive; none clobbered another.
		self.assertEqual(len(runtime.questions_json["questions"]), 2)
		self.assertEqual(len(runtime.questions_json["_dialogue"]), 1)

	def test_state_version_is_reread_inside_the_lock(self) -> None:
		runtime = self._runtime()
		seen_versions: list[int] = []

		def _advance(**kwargs):
			seen_versions.append(kwargs["expected_state_version"])
			return {
				"state_version": kwargs["expected_state_version"] + 1,
				"current_question_index": kwargs["new_index"],
				"questions_json": kwargs["questions_json"],
			}

		async def _run():
			await asyncio.gather(
				dynamic._persist(runtime, lambda c: c),
				dynamic._persist(runtime, lambda c: c),
			)

		with patch.object(dynamic, "advance_interview_question", side_effect=_advance):
			asyncio.run(_run())

		# A stale version would make the second write lose with ConcurrentUpdateError.
		self.assertEqual(seen_versions, [1, 2])


class ScoringTaskTests(TestCase):
	def test_scoring_failure_is_reported_without_killing_the_round(self) -> None:
		sent: list[dict] = []

		runtime = SimpleNamespace(
			send_json=lambda payload: _append(sent, payload),
			round_type="technical",
		)

		async def _append(store, payload):
			store.append(payload)

		async def _boom(*_args, **_kwargs):
			raise RuntimeError("evaluator exploded")

		with patch.object(dynamic, "_score_answer", _boom):
			asyncio.run(
				dynamic._run_scoring(runtime, "answer", turn_index=3, question={})  # type: ignore[arg-type]
			)

		codes = [m.get("code") for m in sent if m.get("type") == "error"]
		self.assertIn("scoring_failed", codes)
		statuses = [m.get("status") for m in sent if m.get("type") == "scoring_state"]
		self.assertIn("failed", statuses)

	def test_tracker_prunes_finished_tasks(self) -> None:
		runtime = SimpleNamespace(engine_state={})

		async def _run():
			async def _noop():
				return None

			done = asyncio.create_task(_noop())
			await done
			dynamic._track_scoring(runtime, done)  # type: ignore[arg-type]

			pending = asyncio.create_task(asyncio.sleep(0.01))
			dynamic._track_scoring(runtime, pending)  # type: ignore[arg-type]
			await pending
			return runtime.engine_state["score_tasks"]

		tasks = asyncio.run(_run())
		# The completed task is dropped rather than accumulating for the round.
		self.assertEqual(len(tasks), 1)
