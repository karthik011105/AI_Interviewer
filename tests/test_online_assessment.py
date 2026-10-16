"""Online assessment: 15-25 mixed, harder questions per attempt, checked keys."""

from __future__ import annotations

import json
import random
import re
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from backend.api import routes_assessment
from backend.assessment.oa_blueprint import (
	CODE_OUTPUT,
	MAX_QUESTIONS,
	MCQ_MULTI,
	MIN_QUESTIONS,
	NUMERICAL,
	build_oa_blueprint,
	oa_family,
)
from backend.assessment.oa_generator import (
	CROSS_CHECKED,
	OABuild,
	build_oa_batch,
	check_all,
	cross_check,
	section_results,
	validate_generated,
)
from backend.assessment.oa_verify import REJECTED, UNVERIFIED, VERIFIED, parse_number, safe_eval, verify_question

_SLOT = re.compile(r"^\d+\. type=(\w+), difficulty=(\w+), topic=([^,\n]+)", re.MULTILINE)


def _fake_generate(prompt: str, model: str) -> str:
	"""A model that follows the slots exactly, with checkable answers, and
	agrees with its own keys when asked for a second opinion."""
	if "checking an assessment's answer key" in prompt:
		blocks = re.findall(r"^Q(\d+)\. (.*)$", prompt, re.MULTILINE)
		def key(text: str) -> list[int]:
			if "Which hold" in text:
				return [0, 2]
			return [0] if ("what is" in text or "What is printed" in text) else [1]

		return json.dumps({"answers": [{"q": int(n), "correct": key(text)} for n, text in blocks]})
	questions = []
	for index, (qtype, difficulty, topic) in enumerate(_SLOT.findall(prompt)):
		base = {"slot": index + 1, "type": qtype, "difficulty": difficulty, "topic": topic, "explanation": "Because."}
		n = index + 3
		if qtype == "numerical":
			base |= {"prompt": f"Q{index} {topic}: what is {n} * 4?", "options": [f"{n * 4}", f"{n * 4 + 1}", f"{n * 5}", f"{n}"], "correct": [0], "verify_expression": f"{n} * 4"}
		elif qtype in {"code_output", "pseudocode"}:
			key = "verify_python" if qtype == "pseudocode" else "code"
			base |= {"prompt": f"Q{index} What is printed?", "code": f"print({n} + {n})", "language": "python", "options": [str(2 * n), str(n), f"{n}{n}", "0"], "correct": [0], key: f"print({n} + {n})"}
			if qtype == "pseudocode":
				base["code"] = f"SET x = {n}\nPRINT x + x"
		elif qtype == "mcq_multi":
			base |= {"prompt": f"Q{index} Which hold for {topic}?", "options": ["w", "x", "y", "z", "v"], "correct": [0, 2]}
		else:
			base |= {"prompt": f"Q{index} About {topic}?", "code": "x = 1\ny = 2" if qtype == "find_bug" else "", "options": ["a", "b", "c", "d"], "correct": [1]}
		questions.append(base)
	return json.dumps({"questions": questions})


def _fake_run(code: str, language: str) -> dict:
	match = re.search(r"print\((\d+) \+ (\d+)\)", code)
	return {"status": {"id": 3}, "stdout": f"{int(match.group(1)) + int(match.group(2))}\n" if match else ""}


class BlueprintTests(TestCase):
	def test_size_is_random_within_15_to_25_and_sections_add_up(self) -> None:
		sizes = set()
		for seed in range(40):
			bp = build_oa_blueprint("java_backend_developer", "Java Backend Developer", rng=random.Random(seed))
			sizes.add(bp.total_questions)
			self.assertTrue(MIN_QUESTIONS <= bp.total_questions <= MAX_QUESTIONS)
			self.assertEqual(sum(s.count for s in bp.sections), bp.total_questions)
			self.assertTrue(all(s.count >= 2 and len(s.slots) == s.count for s in bp.sections))
		self.assertGreater(len(sizes), 5)

	def test_mostly_medium_and_hard(self) -> None:
		bp = build_oa_blueprint("software_engineer", rng=random.Random(1), total_questions=25)
		difficulties = [slot.difficulty for s in bp.sections for slot in s.slots]
		self.assertGreaterEqual(sum(d != "easy" for d in difficulties) / len(difficulties), 0.75)

	def test_sections_follow_the_role(self) -> None:
		def titles(key: str) -> list[str]:
			return [s.title for s in build_oa_blueprint(key, rng=random.Random(2)).sections]

		self.assertEqual(titles("java_backend_developer")[0], "Aptitude & reasoning")
		self.assertIn("CS fundamentals", titles("java_backend_developer"))
		self.assertIn("Maths & statistics", titles("machine_learning_engineer"))
		self.assertIn("SQL", titles("data_analyst"))
		self.assertIn("Shell & config", titles("devops_engineer"))
		self.assertIn("C programming", titles("embedded_systems_engineer"))
		self.assertEqual(oa_family("vlsi_design_engineer"), "core")

	def test_aptitude_calculations_get_quant_topics(self) -> None:
		from backend.assessment.oa_blueprint import _LOGICAL_TOPICS, _QUANT_TOPICS

		for seed in range(10):
			aptitude = build_oa_blueprint("software_engineer", rng=random.Random(seed)).sections[0]
			for slot in aptitude.slots:
				expected = _QUANT_TOPICS if slot.question_type == NUMERICAL else _LOGICAL_TOPICS
				self.assertIn(slot.topic, expected)

	def test_java_code_section_is_in_java(self) -> None:
		code = next(s for s in build_oa_blueprint("java_backend_developer", rng=random.Random(3)).sections if s.key == "code")
		self.assertEqual(code.language, "java")


class VerifyTests(TestCase):
	def test_safe_eval_does_arithmetic_only(self) -> None:
		self.assertAlmostEqual(safe_eval("comb(5, 2) / 2**3"), 1.25)
		for bad in ("__import__('os')", "open('x')", "(lambda: 1)()", "[1, 2]", "9**999"):
			with self.subTest(bad=bad):
				with self.assertRaises(Exception):
					safe_eval(bad)

	def test_numbers_in_options(self) -> None:
		self.assertEqual(parse_number("₹1,250"), 1250)
		self.assertEqual(parse_number("37.5%"), 37.5)
		self.assertAlmostEqual(parse_number("3/8"), 0.375)
		self.assertIsNone(parse_number("none"))

	def test_numerical_key_and_formula_must_agree(self) -> None:
		q = {"type": "numerical", "options": ["12", "15", "20", "8"], "correct": [1], "verify_expression": "60 / 4"}
		self.assertEqual(verify_question(q), (VERIFIED, [1]))
		# Disagreement: one of the model's two answers is wrong, so no re-keying.
		self.assertEqual(verify_question({**q, "correct": [0]})[0], REJECTED)
		self.assertEqual(verify_question({**q, "verify_expression": "99"})[0], REJECTED)
		self.assertEqual(verify_question({**q, "verify_expression": ""})[0], UNVERIFIED)

	def test_pseudocode_translation_must_agree_with_the_key(self) -> None:
		q = {"type": "pseudocode", "code": "PRINT 2 + 2", "verify_python": "print(2 + 2)", "options": ["22", "4", "5", "0"], "correct": [0]}
		self.assertEqual(verify_question(q, _fake_run)[0], REJECTED)
		self.assertEqual(verify_question({**q, "correct": [1]}, _fake_run), (VERIFIED, [1]))

	def test_code_output_is_run(self) -> None:
		q = {"type": "code_output", "code": "print(2 + 2)", "language": "python", "options": ["22", "4", "5", "Error"], "correct": [0]}
		self.assertEqual(verify_question(q, _fake_run), (VERIFIED, [1]))  # the claimed key was wrong
		crash = lambda code, lang: {"status": {"id": 11}, "stderr": "ZeroDivisionError"}
		self.assertEqual(verify_question(q, crash)[0], REJECTED)
		no_numpy = lambda code, lang: {"status": {"id": 11}, "stderr": "ModuleNotFoundError: numpy"}
		self.assertEqual(verify_question(q, no_numpy)[0], UNVERIFIED)

		def judge0_down(code, lang):
			raise OSError("connection refused")

		self.assertEqual(verify_question(q, judge0_down)[0], UNVERIFIED)

	def test_multi_line_output_matches_spaced_option(self) -> None:
		run = lambda code, lang: {"status": {"id": 3}, "stdout": "1\n2\n3\n"}
		q = {"type": "code_output", "code": "for i in range(1, 4): print(i)", "language": "python", "options": ["1 2 3", "0 1 2", "1 2", "3"], "correct": [0]}
		self.assertEqual(verify_question(q, run), (VERIFIED, [0]))


class JsonReplyTests(TestCase):
	def test_first_object_wins_over_trailing_text_or_a_second_object(self) -> None:
		from backend.assessment.oa_generator import first_json_object

		self.assertEqual(first_json_object('Sure! {"questions": [1]} {"extra": 2} thanks'), {"questions": [1]})
		self.assertEqual(first_json_object('```json\n{"a": {"b": 1}}\n```'), {"a": {"b": 1}})
		self.assertEqual(first_json_object('{broken {"ok": true}'), {"ok": True})
		with self.assertRaises(ValueError):
			first_json_object("no json here")


class ValidationTests(TestCase):
	def _section(self):
		return build_oa_blueprint("java_backend_developer", rng=random.Random(4)).sections[0]

	def test_bad_shapes_are_dropped(self) -> None:
		good = {"type": "mcq_single", "prompt": "Q?", "options": ["a", "b", "c", "d"], "correct": [2]}
		self.assertIsNotNone(validate_generated(good, None, self._section()))
		for broken in (
			{**good, "options": ["a", "b", "c"]},
			{**good, "options": ["a", "a", "c", "d"]},
			{**good, "correct": [0, 1]},
			{**good, "correct": [7]},
			{**good, "type": "code_output", "code": ""},
		):
			with self.subTest(broken=broken):
				self.assertIsNone(validate_generated(broken, None, self._section()))

	def test_all_number_options_are_treated_as_a_calculation(self) -> None:
		q = validate_generated({"type": "mcq_single", "prompt": "Days?", "options": ["15 days", "17.5 days", "12 days", "20 days"], "correct": [0]}, None, self._section())
		self.assertEqual(q["type"], NUMERICAL)
		# No formula: it goes to the second opinion, which solves it (17.5) and drops the wrong key.
		[pending] = check_all([q], None)
		self.assertEqual(pending["verified"], UNVERIFIED)
		solver = lambda prompt, model: json.dumps({"answers": [{"q": 1, "correct": [1]}]})
		self.assertEqual(cross_check([pending], model="m", generate=solver), [])

	def test_select_all_questions_say_so(self) -> None:
		q = validate_generated({"type": "mcq_multi", "prompt": "Which?", "options": list("abcde"), "correct": [0, 3]}, None, self._section())
		self.assertIn("(Select all that apply)", q["prompt"])


class CrossCheckTests(TestCase):
	def _q(self, correct):
		return {"type": "mcq_single", "prompt": "TLS?", "code": "", "options": ["a", "b", "c", "d"], "correct": correct, "verified": UNVERIFIED}

	def test_agreement_keeps_disagreement_drops(self) -> None:
		agree = lambda prompt, model: json.dumps({"answers": [{"q": 1, "correct": [1]}, {"q": 2, "correct": [1]}]})
		kept = cross_check([self._q([1]), self._q([3])], model="m", generate=agree)
		self.assertEqual([q["correct"] for q in kept], [[1]])
		self.assertEqual(kept[0]["verified"], CROSS_CHECKED)

	def test_no_correct_option_drops(self) -> None:
		none_right = lambda prompt, model: json.dumps({"answers": [{"q": 1, "correct": []}]})
		self.assertEqual(cross_check([self._q([1])], model="m", generate=none_right), [])

	def test_without_a_second_opinion_unchecked_questions_are_not_used(self) -> None:
		def down(prompt, model):
			raise RuntimeError("rate limited")

		ran = {**self._q([1]), "type": "code_output", "verified": VERIFIED}
		with patch("backend.assessment.oa_generator.time.sleep"):
			self.assertEqual(cross_check([self._q([1]), ran], model="m", generate=down), [ran])

	def test_only_code_judge0_ran_skips_the_second_opinion(self) -> None:
		ran = {**self._q([1]), "type": "code_output", "verified": VERIFIED}
		calls = []
		self.assertEqual(cross_check([ran], model="m", generate=lambda p, m: calls.append(p) or "{}"), [ran])
		self.assertEqual(calls, [])

	def test_self_consistent_numerical_still_needs_agreement(self) -> None:
		numeric = {**self._q([0]), "type": "numerical", "options": ["57600", "72000", "18000", "43200"], "verified": VERIFIED}
		solver = lambda prompt, model: json.dumps({"answers": [{"q": 1, "correct": [2]}]})
		self.assertEqual(cross_check([numeric], model="m", generate=solver), [])


class BatchTests(TestCase):
	def _batch(self, role="java_backend_developer", generate=_fake_generate, total=None, seed=5):
		return build_oa_batch(role_key=role, role_title=role.replace("_", " ").title(), total_questions=total,
							  generate=generate, run=_fake_run, use_pool=False, rng=random.Random(seed))

	def test_full_attempt_with_sections_and_consistent_keys(self) -> None:
		batch = self._batch()
		questions = batch["questions"]
		self.assertTrue(MIN_QUESTIONS <= len(questions) <= MAX_QUESTIONS)
		self.assertEqual(batch["format"], "oa")
		self.assertEqual(sum(s["count"] for s in batch["sections"]), len(questions))
		self.assertEqual(len({q["question_id"] for q in questions}), len(questions))
		for q in questions:
			option_ids = {o["id"] for o in q["options"]}
			self.assertTrue(set(q["correct_option_ids"]) <= option_ids)
			self.assertEqual(q["multi_select"], q["question_type"] == MCQ_MULTI)
		types = {q["question_type"] for q in questions}
		self.assertTrue({NUMERICAL, CODE_OUTPUT} <= types, types)
		code_q = next(q for q in questions if q["question_type"] == CODE_OUTPUT)
		self.assertEqual(code_q["verified"], VERIFIED)

	def test_generation_failure_falls_back_to_the_bank(self) -> None:
		def down(prompt, model):
			raise RuntimeError("groq down")

		batch = self._batch(generate=down, total=16)
		self.assertEqual(len(batch["questions"]), 16)
		self.assertTrue(all(q["selection_source"] == "bank" for q in batch["questions"]))
		self.assertTrue(all("```" not in q["prompt"] for q in batch["questions"]))

	def test_two_attempts_differ(self) -> None:
		a = [q["prompt"] for q in self._batch(seed=1)["questions"]]
		b = [q["prompt"] for q in self._batch(seed=2)["questions"]]
		self.assertNotEqual(a, b)

	def test_section_results(self) -> None:
		batch = self._batch(total=16)
		first = batch["questions"][0]
		record = {"batch_json": batch, "state_json": {"answers": {first["question_id"]: {"is_correct": True}}}}
		results = section_results(record)
		self.assertEqual(results[0]["title"], first["section_title"])
		self.assertEqual((results[0]["correct"], results[0]["answered"]), (1, 1))
		self.assertEqual(sum(r["total"] for r in results), 16)


_USER = routes_assessment.AuthenticatedUser(user_id="u1", email="c@example.com", raw_user={"id": "u1"})


class RouteTests(TestCase):
	def _snapshot(self):
		return build_oa_batch(role_key="java_backend_developer", total_questions=15, generate=_fake_generate,
							  run=_fake_run, use_pool=False, rng=random.Random(9))

	def test_start_uses_the_background_build_and_attaches_to_it(self) -> None:
		build = OABuild(role_key="java_backend_developer", total_questions=15, generate=_fake_generate,
						run=_fake_run, use_pool=False, rng=random.Random(9)).run()
		created = {}

		def create(**kwargs):
			created.update(kwargs)
			return {**kwargs, "state_json": kwargs["state_json"], "batch_json": kwargs["batch_json"]}

		request = routes_assessment.AssessmentStartRequest(session_id="s1")
		with patch("backend.api.routes_assessment._require_parent_session", return_value={"role_selected": "java_backend_developer"}), \
			 patch("backend.api.routes_assessment.fetch_assessment_session", return_value=None), \
			 patch("backend.api.routes_assessment.take_build", return_value=build) as taken, \
			 patch("backend.api.routes_assessment.start_build") as started, \
			 patch("backend.api.routes_assessment.create_assessment_session", side_effect=create):
			response = routes_assessment.start_assessment(request, _USER)
		taken.assert_called_once()
		started.assert_not_called()
		self.assertEqual(created["total_questions"], 15)
		self.assertEqual(created["batch_json"]["build_id"], build.build_id)
		self.assertEqual(response["total_questions"], 15)
		self.assertFalse(response["pending"])
		self.assertNotIn("correct_option_ids", response["questions"][0])
		self.assertTrue(response["questions"][0]["section_title"])
		self.assertIsNotNone(build._on_append)

	def test_select_all_answers_are_all_or_nothing(self) -> None:
		snapshot = self._snapshot()
		multi = next(q for q in snapshot["questions"] if q["multi_select"])
		record = {"session_id": "s1", "status": "in_progress", "total_questions": 15, "batch_json": snapshot,
				  "state_json": {"answers": {}, "answered_question_ids": []}}
		saved = {}

		def persist(**kwargs):
			saved.update(kwargs)
			return {**record, **kwargs}

		def submit(ids):
			request = routes_assessment.AssessmentAnswerRequest(session_id="s1", question_id=multi["question_id"], selected_option_ids=ids)
			with patch("backend.api.routes_assessment._require_parent_session", return_value={}), \
				 patch("backend.api.routes_assessment.fetch_assessment_session", return_value=record), \
				 patch("backend.api.routes_assessment.persist_assessment_session", side_effect=persist):
				return routes_assessment.submit_assessment_answer(request, _USER)["submitted_result"]

		self.assertTrue(submit(list(multi["correct_option_ids"]))["is_correct"])
		self.assertFalse(submit(multi["correct_option_ids"][:1])["is_correct"])
		single = next(q for q in snapshot["questions"] if not q["multi_select"])
		request = routes_assessment.AssessmentAnswerRequest(session_id="s1", question_id=single["question_id"], selected_option_ids=["a", "b"])
		with patch("backend.api.routes_assessment._require_parent_session", return_value={}), \
			 patch("backend.api.routes_assessment.fetch_assessment_session", return_value=record):
			with self.assertRaises(HTTPException) as ctx:
				routes_assessment.submit_assessment_answer(request, _USER)
		self.assertEqual(ctx.exception.status_code, 400)


class ProgressiveBuildTests(TestCase):
	def _build(self, generate=_fake_generate):
		return OABuild(role_key="java_backend_developer", total_questions=16, generate=generate,
					   run=_fake_run, use_pool=False, rng=random.Random(3))

	def test_sections_are_handed_over_in_test_order(self) -> None:
		build = self._build()
		appended = []
		build.attach(lambda items, meta: appended.append(([q["section"] for q in items], dict(meta))))
		build.run()
		self.assertEqual([sections[0] for sections, _ in appended], [s.key for s in build.blueprint.sections])
		self.assertTrue(all(meta["pending"] for _, meta in appended[:-1]))
		self.assertFalse(appended[-1][1]["pending"])
		self.assertEqual(appended[-1][1]["total_questions"], len(build.questions))

	def test_the_candidate_can_start_after_the_first_section(self) -> None:
		import threading

		gate = threading.Event()
		first_prompt = []

		def slow_after_first(prompt, model):
			if first_prompt and "checking an assessment's answer key" not in prompt:
				gate.wait(5)  # later sections are still being written
			first_prompt.append(prompt)
			return _fake_generate(prompt, model)

		build = self._build(generate=slow_after_first)
		threading.Thread(target=build.run, daemon=True).start()
		self.assertTrue(build.wait_first_section(timeout=5))
		snapshot = build.snapshot()
		self.assertTrue(snapshot["pending"])
		self.assertEqual({q["section"] for q in snapshot["questions"]}, {"aptitude"})
		self.assertEqual(build.planned_total(), 16)
		gate.set()
		self.assertTrue(build.wait_finished(timeout=10))
		self.assertFalse(build.snapshot()["pending"])


class AppendRepositoryTests(TestCase):
	def _repo(self, after_append, after_complete=None):
		from unittest.mock import MagicMock

		from backend.database.mongo_client import MongoRepository

		repo = MongoRepository.__new__(MongoRepository)
		sessions = MagicMock()
		sessions.find_one_and_update.side_effect = [after_append, after_complete]
		repo.db = {"assessment_sessions": sessions}
		return repo, sessions

	def test_append_is_tied_to_the_build_that_started_the_attempt(self) -> None:
		repo, sessions = self._repo({"_id": "x", "status": "in_progress", "answered_count": 2})
		repo.append_assessment_questions(session_id="s1", build_id="b1", questions=[{"question_id": "q9"}],
										 pending=True, sections=[], total_questions=20)
		filters, update = sessions.find_one_and_update.call_args.args[:2]
		self.assertEqual(filters, {"session_id": "s1", "batch_json.build_id": "b1"})
		self.assertEqual(update["$push"]["batch_json.questions"]["$each"], [{"question_id": "q9"}])

	def test_last_section_completes_an_attempt_already_fully_answered(self) -> None:
		repo, sessions = self._repo(
			{"_id": "x", "status": "in_progress", "answered_count": 18, "correct_count": 9},
			{"_id": "x", "status": "completed"},
		)
		doc = repo.append_assessment_questions(session_id="s1", build_id="b1", questions=[], pending=False,
											   sections=[], total_questions=18)
		self.assertEqual(doc["status"], "completed")
		completion = sessions.find_one_and_update.call_args.args[1]["$set"]
		self.assertEqual(completion["score_percent"], 50.0)

	def test_a_stale_build_changes_nothing(self) -> None:
		repo, sessions = self._repo(None)
		self.assertIsNone(repo.append_assessment_questions(session_id="s1", build_id="old", questions=[],
														   pending=False, sections=[], total_questions=18))
		self.assertEqual(sessions.find_one_and_update.call_count, 1)
