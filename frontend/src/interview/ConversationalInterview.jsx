import { useCallback, useEffect, useReducer, useRef } from "react";

import WorkflowResetControl from "../components/WorkflowResetControl";
import { buildApiHeaders } from "../lib/api";
import { buildWorkflowResetPatch } from "../lib/workflowReset";

import ConversationThread from "./ConversationThread";
import CoverageRail from "./CoverageRail";
import MicControlBar from "./MicControlBar";
import { INITIAL_STATE, currentQuestionIndex, interviewReducer } from "./interviewReducer.js";
import { resolveInterviewSocketUrl, useInterviewSocket } from "./useInterviewSocket.js";
import { useAudioPlayback } from "./useAudioPlayback.js";
import { useMicVad } from "./useMicVad.js";
import "./conversation.css";

const API_DEFAULT = "http://127.0.0.1:8000";

/** Phases in which the candidate is allowed to send an answer. */
const ANSWERABLE = new Set(["listening", "speaking", "clarifying"]);

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

	useEffect(() => {
		if (state.phase === "listening" || state.phase === "complete") playback.stop();
	}, [state.phase, playback]);

	const micEnabled = state.connection === "open" && !state.round.done;

	useMicVad({
		enabled: micEnabled,
		getPhase: useCallback(() => stateRef.current.phase, []),
		onSpeechStart: useCallback(() => {
			const current = stateRef.current;
			if (current.input.mode === "typed") return;
			// Always sent: it doubles as the barge-in signal and as the cancel for
			// an armed end-of-turn debounce.
			socket.send({ type: "speech_start" });
			if (current.tts.playing) playback.stop();
		}, [socket, playback]),
		onUtterance: useCallback(
			(buffer) => {
				const current = stateRef.current;
				if (current.input.mode === "typed" || current.round.done) return;
				// Audio first: speech_end arms the commit, so the utterance has to be
				// buffered server-side before it lands.
				socket.sendBinary(buffer);
				socket.send({ type: "speech_end" });
			},
			[socket],
		),
		onReady: useCallback(() => dispatch({ type: "SET_CAPTURE_READY", ready: true }), []),
		onFailure: useCallback(() => {
			dispatch({ type: "SET_CAPTURE_READY", ready: false });
			dispatch({
				type: "NOTICE",
				info: "Microphone unavailable, so answers are typed for this round.",
			});
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
				playback.ensureContext().catch(() => null);
				socket.connect(
					resolveInterviewSocketUrl(apiBaseUrl, sessionId, forcedRound, accessToken),
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

	const interrupt = useCallback(() => {
		playback.stop();
		socket.send({ type: "interrupt" });
	}, [playback, socket]);

	const finishAnswer = useCallback(() => {
		socket.send({ type: "end_answer" });
	}, [socket]);

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
			dispatch({ type: "NOTICE", info: successMessage || "Technical round reset." });
			onWorkflowStateChange?.((current) => ({
				...current,
				...buildWorkflowResetPatch(current, clearedTargets),
			}));
		},
		[onWorkflowStateChange, socket],
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
					currentLabel="Technical Interview"
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
					<p>Sign in to run the technical round against your saved session.</p>
				</section>
			) : null}

			{state.ui.error ? <p className="error-banner">{state.ui.error}</p> : null}
			{state.ui.info ? <p className="info-banner">{state.ui.info}</p> : null}

			{sessionId && isAuthenticated && notStarted ? (
				<section className="interview-launch-card glass-panel">
					<span className="section-kicker">Technical</span>
					<h2>{roleTitle ? `${roleTitle} technical interview` : "Technical interview"}</h2>
					<p>
						This round is a conversation. The interviewer follows up on what you actually say,
						so answer as you would out loud - you can interrupt at any time.
					</p>
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
								<h2>Technical round finished</h2>
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
								onInterrupt={interrupt}
								onFinishAnswer={finishAnswer}
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
