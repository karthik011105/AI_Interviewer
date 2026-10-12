import { test } from "node:test";
import assert from "node:assert/strict";

import { normalizeProctoring, warningCopy, PROCTORED_ROUNDS } from "./proctoring.js";

test("warning copy counts down to the end", () => {
	assert.equal(warningCopy("exit_fullscreen", 1).title, "Warning 1 of 3");
	assert.equal(warningCopy("exit_fullscreen", 1).consequence, "2 more and the interview ends.");
	assert.equal(warningCopy("tab_hidden", 2).consequence, "One more and the interview ends.");
	assert.match(warningCopy("tab_hidden", 2).what, /another tab/);
});

test("reaching the limit counts as ended even before the server says so", () => {
	assert.equal(normalizeProctoring({ warnings: 3 }).terminated, true);
	assert.equal(normalizeProctoring({ warnings: 2 }).terminated, false);
	assert.equal(normalizeProctoring(null).warnings, 0);
});

test("every interview round is proctored, the resume and report are not", () => {
	for (const round of ["assessment", "technical", "dsa", "project_discussion", "hr"]) assert.ok(PROCTORED_ROUNDS.has(round));
	assert.ok(!PROCTORED_ROUNDS.has("upload"));
	assert.ok(!PROCTORED_ROUNDS.has("report"));
});
