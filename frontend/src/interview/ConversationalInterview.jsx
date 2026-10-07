import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import WorkflowResetControl from "../components/WorkflowResetControl";
import { buildApiHeaders } from "../lib/api";
import { buildWorkflowResetPatch } from "../lib/workflowReset";

import ConversationThread from "./ConversationThread";
import CoverageRail from "./CoverageRail";
import MicControlBar from "./MicControlBar";
import { INITIAL_STATE, currentQuestionIndex, interviewReducer } from "./interviewReducer.js";
import {
	fetchInterviewSocketTicket,
	resolveInterviewSocketUrl,
	useInterviewSocket,
} from "./useInterviewSocket.js";
import { useAudioPlayback } from "./useAudioPlayback.js";
import { float32ToInt16, useMicVad } from "./useMicVad.js";
import "./conversation.css";

const API_DEFAULT = "http://127.0.0.1:8000";

/** Phases in which the candidate is allowed to send an answer. */
const ANSWERABLE = new Set(["listening", "speaking", "clarifying"]);

// One screen serves every conversational round; only the wording changes.
const ROUND_COPY = {
	technical: {
		kicker: "Technical",
		label: "Technical Interview",
		heading: (role) => (role ? `${role} technical interview` : "Technical interview"),
		intro:
			"This round is a conversation. The interviewer follows up on what you actually say, so answer as you would out loud. Tap the mic to answer (it also interrupts), then the arrow to send.",
	},
	hr: {
		kicker: "HR",
		label: "HR Interview",
		heading: () => "HR interview",
		intro:
			"A conversation about how you work: real situations, what you did, and what you learned. Tap the mic to answer with specific examples, then the arrow to send.",
	},
	project_discussion: {
		kicker: "Projects",
		label: "Project Discussion",
		heading: () => "Project discussion",
		intro:
			"A conversation about the projects on your resume: what you built, the decisions you made, and why. Tap the mic to answer, then the arrow to send.",
	},
};

export default function ConversationalInterview({
	authState,
	workflowState,
	onWorkflowStateChange,
	onNavigate,
	forcedRound = "technical",
	nextPageTarget,
	nextPageLabel,
}) {
	const [state, dispatch] = useReducer(interviewReducer, INITIAL_STATE);

	// One ref holding the whole state. MicVAD captures its callbacks once, so a
	// value read directly from render scope would be frozen at whatever it was
	// when the microphone started. This replaces the four mirror refs the old
	// component needed.
	const stateRef = useRef(state);
	stateRef.current = state;

	const apiBaseUrl = workflowState?.apiBaseUrl || API_DEFAULT;
	const sessionId = workflowState?.sessionId || "";
	const accessToken = authState?.accessToken || "";
	const isAuthenticated = Boolean(accessToken);
	const roleTitle = workflowState?.selectedRoleTitle || "";
	const copy = ROUND_COPY[forcedRound] || ROUND_COPY.technical;

	const playback = useAudioPlayback({
		onError: useCallback((message) => dispatch({ type: "NOTICE", error: message }), []),
	});

	const socket = useInterviewSocket({
		dispatch,
		onAudio: useCallback((buffer) => playback.enqueue(buffer), [playback]),
		onClose: useCallback((manual) => {
			playback.stop();
			if (!manual && !stateRef.current.round.done) {
				dispatch({ type: "NOTICE", error: "The interview connection closed." });
			}
		}, [playback]),
	});

	// Playback bookkeeping follows the tts frames.
	useEffect(() => {
		if (state.tts.playing) playback.beginTurn();
		else playback.endTurn();
	}, [state.tts.playing, playback]);

	// Deliberately NOT tied to the phase. The server moves to LISTENING as soon
	// as it has *sent* the last audio chunk, while the client still has seconds
	// of it scheduled ahead on the audio clock - stopping there cut the
	// interviewer off mid-sentence. Audio is only ever stopped by a real
	// barge-in, by the round ending, or on unmount.
	useEffect(() => {
		if (state.interruptSeq > 0) playback.stop();
	}, [state.interruptSeq, playback]);

	useEffect(() => {
		if (state.round.done) playback.stop();
	}, [state.round.done, playback]);

	const micEnabled = state.connection === "open" && !state.round.done;

	// Push-to-talk: the mic only feeds the interview between a tap on the mic
	// button and a tap on send. Nothing is submitted on a pause any more.
	const [recording, setRecording] = useState(false);
	const recordingRef = useRef(false);
	recordingRef.current = recording;
	const recordedFramesRef = useRef([]);

	const mic = useMicVad({
		enabled: micEnabled,
		autoStart: false,
		getPhase: useCallback(() => stateRef.current.phase, []),
		// Push-to-talk records every frame between the taps rather than only
		// what the VAD classifies as speech: with the candidate marking start
		// and end themselves, a quiet or hesitant answer must not be dropped.
		onFrame: useCallback((frame) => {
			if (recordingRef.current) recordedFramesRef.current.push(new Float32Array(frame));
		}, []),
		onReady: useCallback(() => dispatch({ type: "SET_CAPTURE_READY", ready: true }), []),
		onFailure: useCallback((error) => {
			// MicVAD.new() throws for several unrelated reasons (permission denied,
			// no device present, or its Silero/onnxruntime assets failing to load
			// from their CDN) and previously all of them looked identical to the
			// user. Logging the real error is the only way to tell which one
			// actually happened without reproducing it by hand.
			console.error("Microphone/VAD initialization failed:", error);
			const name = error?.name || "";
			const message = String(error?.message || error || "").slice(0, 160);
			const hint =
				name === "NotAllowedError" || name === "SecurityError"
					? "Microphone permission was denied. Click the lock icon in the address bar, allow the microphone, and reload. On Windows also check Settings > Privacy & security > Microphone > 'Let desktop apps access your microphone'."
					: name === "NotFoundError" || name === "OverconstrainedError"
						? "No microphone was found. Plug one in (or pick it in Windows sound settings) and reload."
						: name === "NotReadableError" || name === "AbortError"
							? "The microphone is busy or blocked by Windows. Close other apps using it (Zoom, Teams, Discord, other tabs), check Settings > Privacy & security > Microphone, then reload."
							: !window.isSecureContext
								? "Microphones only work on https:// or http://localhost. Open the app at http://localhost:5173 instead."
								: // Show the real reason: the generic message alone made this
									// impossible to diagnose from a user's screenshot.
									`Microphone unavailable (${name || "error"}: ${message}). Answers are typed for this round.`;
			dispatch({ type: "SET_CAPTURE_READY", ready: false });
			dispatch({ type: "NOTICE", info: hint });
		}, []),
	});

	const startRound = useCallback(
		async (forceRestart = false) => {
			if (!sessionId || !isAuthenticated) return;

			dispatch({ type: "RESET" });
			dispatch({ type: "SET_BUSY", busy: true });

			try {
				const response = await fetch(`${apiBaseUrl}/interview/start`, {
					method: "POST",
					headers: buildApiHeaders(accessToken, { "Content-Type": "application/json" }),
					body: JSON.stringify({
						session_id: sessionId,
						round: forcedRound,
						force_restart: forceRestart,
					}),
				});
				if (!response.ok) {
					const detail = await response.json().catch(() => ({}));
					throw new Error(detail.detail || "Could not start the interview round.");
				}

				// Prime audio on the click that started the round, since browsers
				// only allow it from a gesture - but deliberately do not await it.
				// With no output device `resume()` can hang indefinitely, and the
				// interview must not be held hostage to that: the thread is
				// readable without sound, and the first audio chunk re-primes.
				playback.ensureContext();
				const ticket = await fetchInterviewSocketTicket(apiBaseUrl, accessToken);
				socket.connect(
					resolveInterviewSocketUrl(apiBaseUrl, sessionId, forcedRound, ticket),
				);
			} catch (error) {
				dispatch({ type: "NOTICE", error: String(error.message || error) });
			} finally {
				dispatch({ type: "SET_BUSY", busy: false });
			}
		},
		[accessToken, apiBaseUrl, forcedRound, isAuthenticated, playback, sessionId, socket],
	);

	const submitTyped = useCallback(() => {
		const text = stateRef.current.input.typedAnswer.trim();
		if (!text) return;
		const turnIndex = currentQuestionIndex(stateRef.current);
		if (turnIndex === null) return;

		dispatch({ type: "LOCAL_ANSWER", turnIndex, text });
		playback.stop();
		if (!socket.send({ type: "typed_answer", text })) {
			dispatch({ type: "NOTICE", error: "The interview socket is not connected." });
		}
	}, [playback, socket]);

	// Mic button: cut the interviewer off if they are talking, then listen.
	const startRecording = useCallback(() => {
		if (stateRef.current.tts.playing) playback.stop();
		// Doubles as the barge-in signal: the server stops speaking and listens.
		socket.send({ type: "speech_start" });
		recordedFramesRef.current = [];
		recordingRef.current = true;
		setRecording(true);
		mic.resume();
	}, [mic, playback, socket]);

	// Send button: stop listening, send everything recorded since the mic tap,
	// then ask the server to transcribe and score it.
	const sendRecording = useCallback(() => {
		recordingRef.current = false;
		mic.pause();
		setRecording(false);
		const frames = recordedFramesRef.current;
		recordedFramesRef.current = [];
		const total = frames.reduce((sum, frame) => sum + frame.length, 0);
		const pcm = new Float32Array(total);
		let offset = 0;
		for (const frame of frames) {
			pcm.set(frame, offset);
			offset += frame.length;
		}
		const int16 = float32ToInt16(pcm);
		// ~1 s chunks at 16 kHz; the server concatenates them before transcribing.
		for (let start = 0; start < int16.length; start += 16000) {
			socket.sendBinary(int16.slice(start, start + 16000).buffer);
		}
		socket.send({ type: "end_answer" });
	}, [mic, socket]);

	// Stop listening whenever answering is no longer possible (scoring, the
	// next question being prepared, round over, switched to typing).
	useEffect(() => {
		const answerable = ANSWERABLE.has(state.phase) && !state.round.done && state.input.mode !== "typed";
		if (!answerable && recordingRef.current) {
			recordingRef.current = false;
			recordedFramesRef.current = [];
			mic.pause();
			setRecording(false);
		}
	}, [state.phase, state.round.done, state.input.mode, mic]);

	const endRound = useCallback(() => {
		playback.stop();
		socket.send({ type: "end_round" });
	}, [playback, socket]);

	// Publish completion up into the workflow so the rest of the app advances.
	const roundDone = state.round.done;
	useEffect(() => {
		if (!roundDone) return;
		onWorkflowStateChange?.((current) => ({
			...current,
			interviewRoundsDone: {
				...(current.interviewRoundsDone || {}),
				[forcedRound]: state.round.totalScore ?? 0,
			},
		}));
	}, [roundDone, forcedRound, state.round.totalScore, onWorkflowStateChange]);

	const handleWorkflowReset = useCallback(
		(payload, { successMessage } = {}) => {
			const clearedTargets = Array.isArray(payload?.cleared_targets)
				? payload.cleared_targets
				: [];
			socket.close();
			dispatch({ type: "RESET" });
			dispatch({ type: "NOTICE", info: successMessage || `${copy.label} reset.` });
			onWorkflowStateChange?.((current) => ({
				...current,
				...buildWorkflowResetPatch(current, clearedTargets),
			}));
		},
		[copy.label, onWorkflowStateChange, socket],
	);

	const notStarted = state.connection === "idle" && state.turns.length === 0;
	const canAnswer = ANSWERABLE.has(state.phase) && !state.round.done;

	return (
		<div className="page-shell interview-shell">
			<div className="action-row">
				<WorkflowResetControl
					accessToken={accessToken}
					apiBaseUrl={apiBaseUrl}
					sessionId={sessionId}
					currentTarget={forcedRound}
					currentLabel={copy.label}
					onResetApplied={handleWorkflowReset}
					triggerClassName="secondary-button action-row__button"
					triggerLabel="Reset"
					disabled={!sessionId || !accessToken}
				/>
			</div>

			{!sessionId ? (
				<section className="interview-notice interview-notice--warning">
					<h2>No active session</h2>
					<p>Upload a resume first so the interviewer knows what to ask about.</p>
					<button type="button" className="primary-button" onClick={() => onNavigate?.("resume")}>
						Go to Resume Intake
					</button>
				</section>
			) : null}

			{sessionId && !isAuthenticated ? (
				<section className="interview-notice interview-notice--warning">
					<h2>Sign in required</h2>
					<p>Sign in to run the {copy.label.toLowerCase()} against your saved session.</p>
				</section>
			) : null}

			{state.ui.error ? <p className="error-banner">{state.ui.error}</p> : null}
			{state.ui.info ? <p className="info-banner">{state.ui.info}</p> : null}

			{sessionId && isAuthenticated && notStarted ? (
				<section className="interview-launch-card glass-panel">
					<span className="section-kicker">{copy.kicker}</span>
					<h2>{copy.heading(roleTitle)}</h2>
					<p>{copy.intro}</p>
					<button
						type="button"
						className="primary-button"
						onClick={() => startRound(false)}
						disabled={state.ui.busy}
					>
						{state.ui.busy ? "Starting…" : "Start interview"}
					</button>
				</section>
			) : null}

			{!notStarted ? (
				<div className="conversation-shell">
					<div className="conversation-main">
						<ConversationThread
							turns={state.turns}
							liveCaption={state.liveCaption}
							phase={state.phase}
						/>

						{state.round.done ? (
							<section className="interview-complete-card glass-panel">
								<span className="section-kicker">Round complete</span>
								<h2>{copy.label} finished</h2>
								{typeof state.round.totalScore === "number" ? (
									<p>
										Average score across {state.round.responseCount} answers:{" "}
										<strong>{state.round.totalScore.toFixed(2)}</strong>
									</p>
								) : null}
								{state.round.feedback?.coaching_note ? (
									<p>{state.round.feedback.coaching_note}</p>
								) : null}
								{state.round.feedbackDegraded ? (
									<p className="info-banner">
										Coaching feedback was unavailable, but every answer was saved and scored.
									</p>
								) : null}
								<div className="interview-inline-actions">
									{nextPageTarget ? (
										<button
											type="button"
											className="primary-button"
											onClick={() => onNavigate?.(nextPageTarget)}
										>
											{nextPageLabel || "Continue"}
										</button>
									) : null}
									<button
										type="button"
										className="secondary-button"
										onClick={() => startRound(true)}
									>
										Restart round
									</button>
								</div>
							</section>
						) : (
							<MicControlBar
								phase={state.phase}
								inputMode={state.input.mode}
								captureReady={state.input.captureReady}
								typedAnswer={state.input.typedAnswer}
								canAnswer={canAnswer}
								ttsPlaying={state.tts.playing}
								recording={recording}
								onStartRecording={startRecording}
								onSendRecording={sendRecording}
								onTypedChange={(value) => dispatch({ type: "SET_TYPED", value })}
								onSubmitTyped={submitTyped}
								onToggleInputMode={() =>
									dispatch({
										type: "SET_INPUT_MODE",
										mode: state.input.mode === "typed" ? "voice" : "typed",
									})
								}
								onEndRound={endRound}
							/>
						)}

						{state.connection === "closed" && !state.round.done ? (
							<div className="interview-inline-actions">
								<button type="button" className="secondary-button" onClick={() => startRound(false)}>
									Reconnect
								</button>
							</div>
						) : null}
					</div>

					<CoverageRail coverage={state.coverage} />
				</div>
			) : null}
		</div>
	);
}
