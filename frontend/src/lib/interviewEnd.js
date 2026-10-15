// Every interview round runs inside FullscreenRound; the resume and report
// pages don't.
export const FULLSCREEN_ROUNDS = new Set(["assessment", "technical", "dsa", "project_discussion", "hr"]);

/** Whether the candidate ended the interview early with "Finish interview". */
export function normalizeInterviewEnd(value) {
	return {
		finished: Boolean(value?.finished),
		finishedRound: value?.finished_round || value?.finishedRound || "",
	};
}
