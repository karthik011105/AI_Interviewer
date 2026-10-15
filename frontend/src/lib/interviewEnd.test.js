import { test } from "node:test";
import assert from "node:assert/strict";

import { FULLSCREEN_ROUNDS, normalizeInterviewEnd } from "./interviewEnd.js";

test("every interview round runs full screen, the resume and report don't", () => {
	for (const round of ["assessment", "technical", "dsa", "project_discussion", "hr"]) assert.ok(FULLSCREEN_ROUNDS.has(round));
	assert.ok(!FULLSCREEN_ROUNDS.has("upload"));
	assert.ok(!FULLSCREEN_ROUNDS.has("report"));
});

test("finishing early is read from the server's answer", () => {
	assert.deepEqual(normalizeInterviewEnd({ finished: true, finished_round: "technical" }), { finished: true, finishedRound: "technical" });
	assert.deepEqual(normalizeInterviewEnd(null), { finished: false, finishedRound: "" });
});
