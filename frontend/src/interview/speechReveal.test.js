import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { FALLBACK_CHARS_PER_SECOND, NO_AUDIO_GRACE_MS, revealState } from "./speechReveal.js";

const turn = (patch = {}) => ({
	role: "interviewer",
	sentences: ["Tell me about caching.", "What would you check first?"],
	text: "Tell me about caching. What would you check first?",
	speech: {},
	endedAt: null,
	revealAll: false,
	...patch,
});

describe("typing the question while it is spoken", () => {
	it("shows nothing before the voice starts", () => {
		const state = revealState(turn(), 1000);
		assert.equal(state.text, "");
		assert.equal(state.done, false);
	});

	it("types each sentence across its own audio", () => {
		const speech = { 0: { start: 1000, duration: 2200 } };
		assert.equal(revealState(turn({ speech }), 1000).text, "");
		assert.equal(revealState(turn({ speech }), 2100).text, "Tell me abo"); // halfway through its audio
		const end = revealState(turn({ speech }), 3200);
		assert.equal(end.text, "Tell me about caching.");
		assert.equal(end.spokenText, "Tell me about caching.");
		assert.equal(end.done, false); // the second sentence hasn't been scheduled yet
	});

	it("finishes with the full wording once every sentence has played", () => {
		const speech = { 0: { start: 0, duration: 1000 }, 1: { start: 1000, duration: 1000 } };
		const state = revealState(turn({ speech }), 5000);
		assert.deepEqual(state, { text: turn().text, spokenText: turn().text, done: true });
	});

	it("types at reading speed when no audio comes", () => {
		const endedAt = 10_000;
		const start = endedAt + NO_AUDIO_GRACE_MS;
		assert.equal(revealState(turn({ endedAt }), endedAt + 100).text, "");
		const firstLength = "Tell me about caching.".length;
		const halfway = start + (firstLength / FALLBACK_CHARS_PER_SECOND) * 500;
		assert.ok(revealState(turn({ endedAt }), halfway).text.length > 0);
		assert.equal(revealState(turn({ endedAt }), start + 60_000).done, true);
	});

	it("shows everything at once when the candidate cuts in", () => {
		assert.equal(revealState(turn({ revealAll: true }), 0).text, turn().text);
	});

	it("leaves restored and candidate turns alone", () => {
		assert.equal(revealState({ role: "interviewer", text: "Earlier question?" }, 0).text, "Earlier question?");
		assert.equal(revealState({ role: "candidate", text: "My answer" }, 0).text, "My answer");
	});
});
