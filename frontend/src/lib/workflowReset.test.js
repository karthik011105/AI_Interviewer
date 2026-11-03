import { test } from "node:test";
import assert from "node:assert/strict";

import { buildWorkflowResetPatch } from "./workflowReset.js";

const EVERYTHING = ["assessment", "technical", "dsa", "project_discussion", "hr", "report"];

test("starting over clears every round and reopens a finished interview", () => {
	const current = {
		interviewRoundsDone: { technical: 0.6, project_discussion: 0.7, hr: 0.8 },
		interviewEnd: { sessionId: "s1", finished: true, finishedRound: "hr" },
		assessmentStatus: "completed",
		dsaRoundCompleted: true,
	};
	const patch = buildWorkflowResetPatch(current, EVERYTHING);

	assert.deepEqual(patch.interviewRoundsDone, {});
	assert.equal(patch.interviewEnd, null);
	assert.equal(patch.assessmentStatus, "idle");
	assert.equal(patch.dsaRoundCompleted, false);
	assert.equal(patch.activeInterviewRound, "technical");
});

test("resetting only the report keeps the interview as it was", () => {
	const patch = buildWorkflowResetPatch({ interviewEnd: { finished: true } }, ["report"]);
	assert.equal("interviewEnd" in patch, false);
});
