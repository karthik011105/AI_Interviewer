/**
 * Reducer tests. Run with:  node --test frontend/src/interview/
 *
 * These cover the cases that are awkward to reproduce by hand in a browser:
 * text arriving one sentence at a time, a score landing after the next
 * question is already on screen, and the microphone falling back to typing.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
	INITIAL_STATE,
	SOCKET_ACTIONS,
	currentQuestionIndex,
	interviewReducer as reduce,
} from "./interviewReducer.js";

const apply = (state, actions) => actions.reduce(reduce, state);

const frame = (payload) => ({ type: SOCKET_ACTIONS[payload.type], payload });

describe("streaming interviewer turns", () => {
	it("accumulates deltas into one bubble", () => {
		const state = apply(INITIAL_STATE, [
			frame({ type: "turn_start", turn_index: 0 }),
			frame({ type: "turn_delta", turn_index: 0, text: "What is a B-tree?" }),
			frame({ type: "turn_delta", turn_index: 0, text: "When would you use one?" }),
		]);

		const turn = state.turns[0];
		assert.equal(turn.role, "interviewer");
		assert.equal(turn.text, "What is a B-tree? When would you use one?");
		assert.equal(turn.streaming, true);
		assert.equal(state.phase, "speaking");
	});

	it("turn_end replaces accumulated text with the authoritative copy", () => {
		// Guards against a dropped delta leaving a hole in what is displayed.
		const state = apply(INITIAL_STATE, [
			frame({ type: "turn_start", turn_index: 0 }),
			frame({ type: "turn_delta", turn_index: 0, text: "partial" }),
			frame({
				type: "turn_end",
				turn_index: 0,
				text: "The complete question?",
				action: "new_topic",
				focus_skill: "SQL",
				question_tier: "absent",
			}),
		]);

		const turn = state.turns[0];
		assert.equal(turn.text, "The complete question?");
		assert.equal(turn.streaming, false);
		assert.equal(turn.focusSkill, "SQL");
		assert.equal(turn.tier, "absent");
	});

	it("a delta arriving before turn_start still creates the bubble", () => {
		const state = reduce(INITIAL_STATE, frame({ type: "turn_delta", turn_index: 2, text: "hi" }));
		assert.equal(state.turns.length, 1);
		assert.equal(state.turns[0].text, "hi");
	});

	it("keeps separate bubbles per turn", () => {
		const state = apply(INITIAL_STATE, [
			frame({ type: "turn_start", turn_index: 0 }),
			frame({ type: "turn_delta", turn_index: 0, text: "first" }),
			frame({ type: "turn_start", turn_index: 1 }),
			frame({ type: "turn_delta", turn_index: 1, text: "second" }),
		]);

		assert.equal(state.turns.length, 2);
		assert.equal(state.turns[0].text, "first");
		assert.equal(state.turns[1].text, "second");
	});
});

describe("answers and out-of-order scoring", () => {
	it("attaches a score that arrives after the next question", () => {
		// The backend runs scoring concurrently with the next director turn, so
		// this ordering is the normal case, not an edge case.
		const state = apply(INITIAL_STATE, [
			frame({ type: "turn_start", turn_index: 0 }),
			frame({ type: "turn_end", turn_index: 0, text: "Q0?" }),
			{ type: "LOCAL_ANSWER", turnIndex: 0, text: "my answer" },
			frame({ type: "turn_start", turn_index: 1 }),
			frame({ type: "turn_end", turn_index: 1, text: "Q1?" }),
			frame({ type: "answer_scored", turn_index: 0, score: 0.72, evaluation: { final_score: 0.72 } }),
		]);

		const answer = state.turns.find((t) => t.role === "candidate" && t.turnIndex === 0);
		assert.equal(answer.score, 0.72);
		assert.equal(answer.scoring, false);
		// The later question is untouched.
		assert.equal(state.turns.find((t) => t.role === "interviewer" && t.turnIndex === 1).text, "Q1?");
	});

	it("marks an answer as scoring while it is in flight", () => {
		const state = apply(INITIAL_STATE, [
			{ type: "LOCAL_ANSWER", turnIndex: 0, text: "answer" },
			frame({ type: "scoring_state", turn_index: 0, status: "running" }),
		]);
		assert.equal(state.turns[0].scoring, true);
	});

	it("surfaces a scoring failure without losing the answer", () => {
		const state = apply(INITIAL_STATE, [
			{ type: "LOCAL_ANSWER", turnIndex: 0, text: "answer" },
			frame({ type: "scoring_state", turn_index: 0, status: "failed" }),
		]);
		assert.equal(state.turns[0].text, "answer");
		assert.equal(state.turns[0].scoringFailed, true);
		assert.equal(state.turns[0].scoring, false);
	});

	it("a typed answer clears the input box", () => {
		const state = apply(INITIAL_STATE, [
			{ type: "SET_TYPED", value: "draft" },
			{ type: "LOCAL_ANSWER", turnIndex: 0, text: "draft" },
		]);
		assert.equal(state.input.typedAnswer, "");
	});

	it("no_speech does not create an empty bubble", () => {
		const state = reduce(
			INITIAL_STATE,
			frame({ type: "transcript", text: "", no_speech: true }),
		);
		assert.equal(state.turns.length, 0);
		assert.equal(state.input.sttMisses, 1);
		assert.match(state.ui.info, /did not catch/i);
	});
});

describe("phases", () => {
	it("maps server states onto UI phases", () => {
		const cases = [
			["PLAYING", "speaking"],
			["LISTENING", "listening"],
			["TRANSCRIBING", "transcribing"],
			["CLARIFYING", "clarifying"],
			["COMPLETE", "complete"],
		];
		for (const [serverState, phase] of cases) {
			const state = reduce(INITIAL_STATE, frame({ type: "state_change", state: serverState }));
			assert.equal(state.phase, phase, `${serverState} -> ${phase}`);
		}
	});

	it("EVALUATING is not a turn phase now that scoring is concurrent", () => {
		const listening = reduce(INITIAL_STATE, frame({ type: "state_change", state: "LISTENING" }));
		const evaluating = reduce(listening, frame({ type: "state_change", state: "EVALUATING" }));
		assert.equal(evaluating.phase, "listening");
	});

	it("an interrupt stops playback and returns to listening", () => {
		const playing = apply(INITIAL_STATE, [
			frame({ type: "tts_start", encoding: "mp3" }),
			frame({ type: "state_change", state: "PLAYING" }),
		]);
		assert.equal(playing.tts.playing, true);

		const barged = reduce(playing, frame({ type: "interrupt_ack" }));
		assert.equal(barged.tts.playing, false);
		assert.equal(barged.phase, "listening");
	});
});

describe("coverage and completion", () => {
	it("tracks tier progress and remaining targets", () => {
		const state = reduce(
			INITIAL_STATE,
			frame({
				type: "coverage_update",
				tier_progress: { absent: { covered: 2, target: 4 } },
				remaining_targets: [{ focus_skill: "sql", question_tier: "absent" }],
				turns_used: 2,
				min_turns: 6,
				max_turns: 9,
			}),
		);

		assert.deepEqual(state.coverage.tierProgress, { absent: { covered: 2, target: 4 } });
		assert.equal(state.coverage.remainingTargets.length, 1);
		assert.equal(state.coverage.maxTurns, 9);
	});

	it("records round completion and degraded feedback", () => {
		const state = reduce(
			INITIAL_STATE,
			frame({
				type: "round_complete",
				total_score: 0.61,
				response_count: 8,
				round_feedback: null,
				feedback_degraded: true,
			}),
		);

		assert.equal(state.phase, "complete");
		assert.equal(state.round.done, true);
		assert.equal(state.round.totalScore, 0.61);
		assert.equal(state.round.feedbackDegraded, true);
	});
});

describe("input fallbacks", () => {
	it("an STT failure forces typed mode", () => {
		const state = reduce(
			INITIAL_STATE,
			frame({ type: "error", code: "stt_unavailable", message: "no whisper" }),
		);
		assert.equal(state.input.mode, "typed");
		assert.equal(state.ui.error, "no whisper");
	});

	it("an unrelated error does not change input mode", () => {
		const state = reduce(
			INITIAL_STATE,
			frame({ type: "error", code: "quota_exceeded", message: "slow down" }),
		);
		assert.equal(state.input.mode, "voice");
	});

	it("losing the microphone switches to typing", () => {
		const state = reduce(INITIAL_STATE, { type: "SET_CAPTURE_READY", ready: false });
		assert.equal(state.input.mode, "typed");
	});
});

describe("currentQuestionIndex", () => {
	it("returns the latest interviewer turn", () => {
		const state = apply(INITIAL_STATE, [
			frame({ type: "turn_end", turn_index: 0, text: "a" }),
			frame({ type: "turn_end", turn_index: 1, text: "b" }),
		]);
		assert.equal(currentQuestionIndex(state), 1);
	});

	it("is null before anything is asked", () => {
		assert.equal(currentQuestionIndex(INITIAL_STATE), null);
	});
});
