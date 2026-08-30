/**
 * State for the conversational interview.
 *
 * The socket is authoritative: almost every action here mirrors a server frame,
 * and the reducer's job is to fold those into something renderable. Keeping it
 * a pure function means the whole turn lifecycle - streaming text, a score that
 * arrives after the next question, a barge-in mid-sentence - is unit-testable
 * without a browser, a microphone, or a WebSocket.
 */

export const INITIAL_STATE = {
	// Where the interview is in the current turn.
	phase: "setup", // setup|connecting|thinking|speaking|listening|transcribing|clarifying|complete
	connection: "idle", // idle|connecting|open|closed

	turns: [], // [{ turnIndex, role, text, streaming, action, focusSkill, tier, score, evaluation, scoring }]
	activeTurnIndex: null,
	liveCaption: "",

	coverage: {
		tierProgress: {},
		remainingTargets: [],
		turnsUsed: 0,
		minTurns: 0,
		maxTurns: 0,
	},

	input: {
		mode: "voice", // voice|typed
		typedAnswer: "",
		captureReady: false,
		sttMisses: 0,
	},

	tts: { playing: false, encoding: null },

	round: {
		mode: null, // "dynamic" | "scripted"
		done: false,
		feedback: null,
		feedbackDegraded: false,
		totalScore: null,
		responseCount: 0,
	},

	ui: { busy: false, error: "", info: "" },
};

/** Server states map onto the phases the UI actually distinguishes. */
const PHASE_BY_STATE = {
	IDLE: "connecting",
	PLAYING: "speaking",
	CLARIFYING: "clarifying",
	LISTENING: "listening",
	TRANSCRIBING: "transcribing",
	EVALUATING: "listening", // scoring is concurrent now; it is not a turn phase
	FEEDBACK: "listening",
	COMPLETE: "complete",
};

function upsertTurn(turns, turnIndex, role, patch) {
	const at = turns.findIndex((t) => t.turnIndex === turnIndex && t.role === role);
	if (at === -1) {
		return [...turns, { turnIndex, role, text: "", streaming: false, score: null, ...patch }];
	}
	const next = turns.slice();
	next[at] = { ...next[at], ...patch };
	return next;
}

export function interviewReducer(state, action) {
	switch (action.type) {
		case "RESET":
			return { ...INITIAL_STATE, input: { ...INITIAL_STATE.input, ...(action.input || {}) } };

		case "CONNECTION":
			return {
				...state,
				connection: action.status,
				phase: action.status === "connecting" ? "connecting" : state.phase,
				ui: {
					...state.ui,
					error: action.status === "closed" && !state.round.done ? state.ui.error : state.ui.error,
				},
			};

		case "CONNECTED":
			return {
				...state,
				connection: "open",
				round: { ...state.round, mode: action.payload.mode || null },
				coverage: {
					...state.coverage,
					maxTurns: Number(action.payload.total_questions) || state.coverage.maxTurns,
				},
			};

		case "STATE_CHANGE": {
			const phase = PHASE_BY_STATE[action.payload.state];
			return phase ? { ...state, phase } : state;
		}

		case "TURN_START":
			return {
				...state,
				phase: "speaking",
				activeTurnIndex: action.payload.turn_index,
				turns: upsertTurn(state.turns, action.payload.turn_index, "interviewer", {
					text: "",
					streaming: true,
					action: action.payload.action || null,
				}),
			};

		case "TURN_DELTA": {
			const { turn_index: turnIndex, text } = action.payload;
			const at = state.turns.findIndex(
				(t) => t.turnIndex === turnIndex && t.role === "interviewer",
			);
			if (at === -1) {
				return {
					...state,
					turns: upsertTurn(state.turns, turnIndex, "interviewer", { text, streaming: true }),
				};
			}
			const turns = state.turns.slice();
			const existing = turns[at];
			turns[at] = {
				...existing,
				text: existing.text ? `${existing.text} ${text}` : text,
			};
			return { ...state, turns };
		}

		case "TURN_END":
			return {
				...state,
				turns: upsertTurn(state.turns, action.payload.turn_index, "interviewer", {
					// Authoritative text; replaces the accumulated deltas so a dropped
					// frame cannot leave a hole in what is displayed.
					text: action.payload.text || "",
					streaming: false,
					action: action.payload.action || null,
					focusSkill: action.payload.focus_skill || null,
					tier: action.payload.question_tier || null,
					difficulty: action.payload.difficulty || null,
					fallbackUsed: Boolean(action.payload.fallback_used),
				}),
			};

		case "TRANSCRIPT": {
			const { text, no_speech: noSpeech } = action.payload;
			if (noSpeech || !String(text || "").trim()) {
				return {
					...state,
					liveCaption: "",
					input: { ...state.input, sttMisses: state.input.sttMisses + 1 },
					ui: { ...state.ui, info: "I did not catch that. Try again, or switch to typing." },
				};
			}
			return {
				...state,
				liveCaption: "",
				input: { ...state.input, sttMisses: 0 },
				turns: upsertTurn(state.turns, action.turnIndex ?? state.activeTurnIndex, "candidate", {
					text,
					streaming: false,
					scoring: true,
				}),
			};
		}

		case "STT_PARTIAL":
			return { ...state, liveCaption: action.payload.text || "" };

		case "LOCAL_ANSWER":
			// A typed answer echoes into the thread immediately; the server will
			// not send it back as a transcript.
			return {
				...state,
				turns: upsertTurn(state.turns, action.turnIndex, "candidate", {
					text: action.text,
					streaming: false,
					scoring: true,
				}),
				input: { ...state.input, typedAnswer: "" },
			};

		case "SCORING_STATE":
			return {
				...state,
				turns: upsertTurn(state.turns, action.payload.turn_index, "candidate", {
					scoring: action.payload.status === "running",
					scoringFailed: action.payload.status === "failed",
				}),
			};

		case "ANSWER_SCORED":
			// May arrive after the next question is already rendered, which is why
			// it is addressed by turn index rather than appended.
			return {
				...state,
				turns: upsertTurn(state.turns, action.payload.turn_index, "candidate", {
					score: action.payload.score,
					evaluation: action.payload.evaluation || null,
					scoring: false,
				}),
			};

		case "COVERAGE_UPDATE":
			return {
				...state,
				coverage: {
					tierProgress: action.payload.tier_progress || {},
					remainingTargets: action.payload.remaining_targets || [],
					turnsUsed: Number(action.payload.turns_used) || 0,
					minTurns: Number(action.payload.min_turns) || 0,
					maxTurns: Number(action.payload.max_turns) || state.coverage.maxTurns,
				},
			};

		case "TTS_START":
			return {
				...state,
				tts: { playing: true, encoding: action.payload.encoding || null },
			};

		case "TTS_END":
			return { ...state, tts: { ...state.tts, playing: false } };

		case "INTERRUPTED":
			return { ...state, tts: { ...state.tts, playing: false }, phase: "listening" };

		case "ROUND_COMPLETE":
			return {
				...state,
				phase: "complete",
				tts: { ...state.tts, playing: false },
				round: {
					...state.round,
					done: true,
					feedback: action.payload.round_feedback || null,
					feedbackDegraded: Boolean(action.payload.feedback_degraded),
					totalScore: action.payload.total_score ?? null,
					responseCount: Number(action.payload.response_count) || 0,
				},
			};

		case "SERVER_ERROR": {
			const { code, message } = action.payload;
			// These mean the microphone path is unusable; fall back to typing
			// rather than leaving the candidate talking to nothing.
			const forcesTyped = code === "stt_unavailable" || code === "stt_failed";
			return {
				...state,
				input: forcesTyped ? { ...state.input, mode: "typed" } : state.input,
				ui: { ...state.ui, error: message || "Something went wrong." },
			};
		}

		case "SET_TYPED":
			return { ...state, input: { ...state.input, typedAnswer: action.value } };

		case "SET_INPUT_MODE":
			return { ...state, input: { ...state.input, mode: action.mode } };

		case "SET_CAPTURE_READY":
			return {
				...state,
				input: {
					...state.input,
					captureReady: action.ready,
					mode: action.ready ? state.input.mode : "typed",
				},
			};

		case "SET_BUSY":
			return { ...state, ui: { ...state.ui, busy: action.busy } };

		case "NOTICE":
			return { ...state, ui: { ...state.ui, info: action.info || "", error: action.error || "" } };

		case "CLEAR_NOTICES":
			return { ...state, ui: { ...state.ui, info: "", error: "" } };

		default:
			return state;
	}
}

/** Socket frame type -> reducer action type. */
export const SOCKET_ACTIONS = {
	connected: "CONNECTED",
	state_change: "STATE_CHANGE",
	turn_start: "TURN_START",
	turn_delta: "TURN_DELTA",
	turn_end: "TURN_END",
	transcript: "TRANSCRIPT",
	stt_partial: "STT_PARTIAL",
	scoring_state: "SCORING_STATE",
	answer_scored: "ANSWER_SCORED",
	coverage_update: "COVERAGE_UPDATE",
	tts_start: "TTS_START",
	tts_end: "TTS_END",
	interrupt_ack: "INTERRUPTED",
	round_complete: "ROUND_COMPLETE",
	error: "SERVER_ERROR",
};

/** The last interviewer turn, i.e. the question currently being answered. */
export function currentQuestionIndex(state) {
	let index = null;
	for (const turn of state.turns) {
		if (turn.role === "interviewer") index = turn.turnIndex;
	}
	return index;
}
