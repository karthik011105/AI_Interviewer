import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Maximize, ShieldAlert } from "lucide-react";

import { buildApiHeaders } from "../lib/api";
import { normalizeProctoring, warningCopy } from "../lib/proctoring";

// A brief blur happens when the mic permission prompt or a native file dialog
// opens; only a sustained one means the candidate went to another window.
const BLUR_GRACE_MS = 1500;
// Entering full screen can itself fire a stray blur/visibility event, so those
// are ignored for a moment afterwards. Leaving full screen is never ignored:
// an Esc pressed inside this window used to slip through uncounted and leave
// the round usable outside full screen.
const RESUME_GRACE_MS = 800;

function isFullscreen() {
	return Boolean(document.fullscreenElement);
}

async function enterFullscreen() {
	try {
		await document.documentElement.requestFullscreen({ navigationUI: "hide" });
		return true;
	} catch {
		return false;
	}
}

/**
 * Runs a round in full screen. Leaving full screen or switching tab/window is
 * a warning; the third ends the whole interview (counted on the server, so a
 * refresh doesn't reset it). The round stays mounted under the warning screen
 * so a live interview or code draft isn't lost.
 */
export default function ProctoredRound({ roundKey, roundLabel, sessionId, apiBaseUrl, accessToken, onNavigate, onStateChange, children }) {
	const [state, setState] = useState(() => normalizeProctoring(null));
	const [loaded, setLoaded] = useState(false);
	const [started, setStarted] = useState(isFullscreen);
	const [warning, setWarning] = useState(null);
	const [enterFailed, setEnterFailed] = useState(false);
	const [confirmFinish, setConfirmFinish] = useState(false);
	const [finishing, setFinishing] = useState(false);
	const [finishError, setFinishError] = useState("");
	// True while a warning is being recorded, so one tab switch (which fires
	// fullscreenchange, visibilitychange and blur together) counts once.
	const recording = useRef(false);
	const graceUntil = useRef(0);
	const stateRef = useRef(state);
	stateRef.current = state;
	const blurTimer = useRef(null);
	const base = String(apiBaseUrl || "").replace(/\/+$/, "");

	const ended = state.terminated || state.finished;

	const publish = useCallback(
		(next) => {
			const normalized = normalizeProctoring(next);
			setState(normalized);
			onStateChange?.(normalized);
			return normalized;
		},
		[onStateChange],
	);

	useEffect(() => {
		let cancelled = false;
		fetch(`${base}/proctoring/${encodeURIComponent(sessionId)}`, { headers: buildApiHeaders(accessToken) })
			.then((response) => (response.ok ? response.json() : null))
			.then((payload) => {
				if (!cancelled && payload) publish(payload);
			})
			.catch(() => {})
			.finally(() => !cancelled && setLoaded(true));
		return () => {
			cancelled = true;
		};
	}, [base, sessionId, accessToken, publish]);

	const report = useCallback(
		async (reason) => {
			if (recording.current) return;
			if (reason !== "exit_fullscreen" && Date.now() < graceUntil.current) return;
			recording.current = true;
			clearTimeout(blurTimer.current);
			const current = stateRef.current;
			let next = { ...current, warnings: current.warnings + 1 };
			try {
				const response = await fetch(`${base}/proctoring/warning`, {
					method: "POST",
					headers: buildApiHeaders(accessToken, { "Content-Type": "application/json" }),
					body: JSON.stringify({ session_id: sessionId, round: roundKey, reason }),
				});
				if (response.ok) next = await response.json();
			} catch {
				// Offline: still count it locally so the rule holds on screen.
			}
			const normalized = publish(next);
			setWarning(normalized.terminated ? null : { reason, ...warningCopy(reason, normalized.warnings, normalized.limit) });
			if (normalized.terminated && isFullscreen()) document.exitFullscreen().catch(() => {});
			recording.current = false;
		},
		[accessToken, base, publish, roundKey, sessionId],
	);

	// Watch while the round is running with no warning up.
	useEffect(() => {
		if (!started || warning || ended || confirmFinish) return undefined;
		graceUntil.current = Date.now() + RESUME_GRACE_MS;
		// Never let the round run outside full screen without a warning, even if
		// the exit event was missed.
		if (!isFullscreen()) {
			report("exit_fullscreen");
			return undefined;
		}

		const onFullscreen = () => !isFullscreen() && report("exit_fullscreen");
		const onVisibility = () => document.visibilityState === "hidden" && report("tab_hidden");
		const onBlur = () => {
			clearTimeout(blurTimer.current);
			blurTimer.current = setTimeout(() => report("window_blur"), BLUR_GRACE_MS);
		};
		const onFocus = () => clearTimeout(blurTimer.current);

		document.addEventListener("fullscreenchange", onFullscreen);
		document.addEventListener("visibilitychange", onVisibility);
		window.addEventListener("blur", onBlur);
		window.addEventListener("focus", onFocus);
		return () => {
			clearTimeout(blurTimer.current);
			document.removeEventListener("fullscreenchange", onFullscreen);
			document.removeEventListener("visibilitychange", onVisibility);
			window.removeEventListener("blur", onBlur);
			window.removeEventListener("focus", onFocus);
		};
	}, [started, warning, ended, confirmFinish, report]);

	// Distraction-free layout (no top bar / sidebar) while the round runs.
	useEffect(() => {
		const active = started && !ended;
		document.body.classList.toggle("pp-focus-mode", active);
		return () => document.body.classList.remove("pp-focus-mode");
	}, [started, ended]);

	async function goFullscreen() {
		setEnterFailed(false);
		if (await enterFullscreen()) {
			setWarning(null);
			setStarted(true);
		} else {
			setEnterFailed(true);
		}
	}

	async function finishInterview() {
		setFinishing(true);
		setFinishError("");
		// No warnings while we leave full screen on purpose.
		recording.current = true;
		try {
			const response = await fetch(`${base}/proctoring/finish`, {
				method: "POST",
				headers: buildApiHeaders(accessToken, { "Content-Type": "application/json" }),
				body: JSON.stringify({ session_id: sessionId, round: roundKey }),
			});
			if (!response.ok) throw new Error("finish failed");
			publish(await response.json());
			if (isFullscreen()) await document.exitFullscreen().catch(() => {});
			onNavigate?.("report");
		} catch {
			recording.current = false;
			setFinishing(false);
			setFinishError("Couldn't finish the interview. Check your connection and try again.");
		}
	}

	if (state.finished && !state.terminated) {
		return (
			<section className="pp-card proctor proctor--finished" role="status">
				<CheckCircle2 size={28} aria-hidden="true" />
				<h2>Interview finished</h2>
				<p>You ended this interview early. Your answers up to that point are saved in your report.</p>
				<button type="button" className="primary-button" onClick={() => onNavigate?.("report")}>
					View my report
				</button>
			</section>
		);
	}

	if (state.terminated) {
		return (
			<section className="pp-card proctor proctor--ended" role="alert">
				<ShieldAlert size={28} aria-hidden="true" />
				<h2>Interview ended</h2>
				<p>
					You left full screen or switched tabs {state.limit} times, so the interview was stopped. Your answers up
					to that point are saved.
				</p>
				<button type="button" className="primary-button" onClick={() => onNavigate?.("report")}>
					View my report
				</button>
			</section>
		);
	}

	if (!started) {
		const used = state.warnings;
		return (
			<section className="pp-card proctor">
				<Maximize size={26} aria-hidden="true" />
				<h2>{roundLabel} runs in full screen</h2>
				<ul className="proctor__rules">
					<li>Stay in full screen until the round is over.</li>
					<li>Don't switch tabs or open other windows.</li>
					<li>
						Each time you do counts as a warning. After {state.limit} warnings across the whole interview, it ends.
					</li>
				</ul>
				{used ? (
					<p className="proctor__used">
						Warnings used so far: {used} of {state.limit}
					</p>
				) : null}
				<button type="button" className="primary-button" onClick={goFullscreen} disabled={!loaded}>
					Enter full screen and start
				</button>
				{enterFailed ? (
					<p className="error-banner">Your browser blocked full screen. Allow it for this site and try again.</p>
				) : null}
			</section>
		);
	}

	return (
		<>
			{confirmFinish || warning ? null : (
				<div className="proctor-bar">
					<button type="button" className="proctor-bar__finish" onClick={() => setConfirmFinish(true)}>
						Finish interview
					</button>
				</div>
			)}
			{children}
			{confirmFinish ? (
				<div className="proctor-overlay" role="alertdialog" aria-modal="true" aria-labelledby="proctor-finish-title">
					<div className="proctor-overlay__card">
						<h2 id="proctor-finish-title">Finish the interview now?</h2>
						<p>
							Rounds you haven't completed will be left out, and your report will cover what you've done so far.
							You can't come back to this interview afterwards.
						</p>
						<div className="proctor-overlay__actions">
							<button type="button" className="secondary-button" onClick={() => setConfirmFinish(false)} disabled={finishing} autoFocus>
								Keep going
							</button>
							<button type="button" className="primary-button proctor-overlay__danger" onClick={finishInterview} disabled={finishing}>
								{finishing ? "Finishing…" : "Finish interview"}
							</button>
						</div>
						{finishError ? <p className="error-banner">{finishError}</p> : null}
					</div>
				</div>
			) : null}
			{warning ? (
				<div className="proctor-overlay" role="alertdialog" aria-modal="true" aria-labelledby="proctor-warning-title">
					<div className="proctor-overlay__card">
						<AlertTriangle size={30} aria-hidden="true" />
						<h2 id="proctor-warning-title">{warning.title}</h2>
						<p>
							{warning.what} {warning.consequence}
						</p>
						<button type="button" className="primary-button" onClick={goFullscreen}>
							Return to full screen
						</button>
						{enterFailed ? <p className="error-banner">Full screen was blocked. Click the button again.</p> : null}
					</div>
				</div>
			) : null}
		</>
	);
}
