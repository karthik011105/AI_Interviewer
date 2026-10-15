// Full-screen proctoring rules shared by every round. The backend owns the
// count (POST /proctoring/warning); these helpers only word it.

export const PROCTOR_LIMIT = 3;

export const PROCTORED_ROUNDS = new Set(["assessment", "technical", "dsa", "project_discussion", "hr"]);

const REASON_TEXT = {
	exit_fullscreen: "You left full screen.",
	tab_hidden: "You switched to another tab or window.",
	window_blur: "You switched to another window.",
};

export function warningCopy(reason, warnings, limit = PROCTOR_LIMIT) {
	const remaining = Math.max(0, limit - warnings);
	return {
		title: `Warning ${Math.min(warnings, limit)} of ${limit}`,
		what: REASON_TEXT[reason] || REASON_TEXT.exit_fullscreen,
		consequence:
			remaining === 1
				? "One more and the interview ends."
				: `${remaining} more and the interview ends.`,
	};
}

export function normalizeProctoring(value) {
	const warnings = Math.max(0, Number(value?.warnings) || 0);
	const limit = Number(value?.limit) || PROCTOR_LIMIT;
	return {
		warnings,
		limit,
		terminated: Boolean(value?.terminated) || warnings >= limit,
		// The candidate ended it themselves with "Finish interview".
		finished: Boolean(value?.finished),
		finishedRound: value?.finished_round || value?.finishedRound || "",
	};
}
