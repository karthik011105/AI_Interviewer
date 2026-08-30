import { lazy } from "react";

import ScriptedInterview from "../interview/ScriptedInterview";

// The conversational engine is technical-only for now. Keeping the split here
// means HR and project discussion execute exactly the code they always have.
const ConversationalInterview = lazy(() => import("../interview/ConversationalInterview"));

const CONVERSATIONAL_ROUNDS = new Set(["technical"]);

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
