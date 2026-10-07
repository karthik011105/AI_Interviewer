import { lazy } from "react";

import ScriptedInterview from "../interview/ScriptedInterview";

// Must match DEFAULT_DYNAMIC_ROUNDS in backend/api/interview_engines/__init__.py:
// this screen only understands the conversational engine's messages.
const ConversationalInterview = lazy(() => import("../interview/ConversationalInterview"));

const CONVERSATIONAL_ROUNDS = new Set(["technical", "hr", "project_discussion"]);

function resolveRound(props) {
	const raw = props.forcedRound ?? props.round ?? props.workflowState?.activeInterviewRound;
	return String(raw || "").trim().toLowerCase();
}

export default function InterviewPage(props) {
	return CONVERSATIONAL_ROUNDS.has(resolveRound(props)) ? (
		<ConversationalInterview {...props} />
	) : (
		<ScriptedInterview {...props} />
	);
}
