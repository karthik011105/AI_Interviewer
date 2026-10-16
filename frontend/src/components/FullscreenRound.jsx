import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Maximize, Minimize } from "lucide-react";

import { buildApiHeaders } from "../lib/api";
import { normalizeInterviewEnd } from "../lib/interviewEnd";

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
 * Wraps every round: asks the candidate to go full screen when the round
 * starts and gives the round the whole screen (no top bar or step list).
 * Leaving full screen is allowed and has no consequence; a small button
 * offers the way back in. "Finish interview" ends the whole interview early
 * (stored on the server, so every round then shows "Interview finished").
 */
export default function FullscreenRound({ roundKey, roundLabel, sessionId, apiBaseUrl, accessToken, onNavigate, onStateChange, children }) {
	const [ended, setEnded] = useState(() => normalizeInterviewEnd(null));
	const [loaded, setLoaded] = useState(false);
	const [started, setStarted] = useState(isFullscreen);
	const [fullscreen, setFullscreen] = useState(isFullscreen);
	const [confirmFinish, setConfirmFinish] = useState(false);
	const [finishing, setFinishing] = useState(false);
	const [finishError, setFinishError] = useState("");
	const base = String(apiBaseUrl || "").replace(/\/+$/, "");

	const publish = useCallback(
		(next) => {
			const normalized = normalizeInterviewEnd(next);
			setEnded(normalized);
			onStateChange?.(normalized);
		},
		[onStateChange],
	);

	useEffect(() => {
		let cancelled = false;
		fetch(`${base}/interview-end/${encodeURIComponent(sessionId)}`, { headers: buildApiHeaders(accessToken) })
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

	useEffect(() => {
		const onChange = () => setFullscreen(isFullscreen());
		document.addEventListener("fullscreenchange", onChange);
		return () => document.removeEventListener("fullscreenchange", onChange);
	}, []);

	// The round gets the whole screen (no top bar / step list) while it runs.
	useEffect(() => {
		const active = started && !ended.finished;
		document.body.classList.toggle("pp-focus-mode", active);
		return () => document.body.classList.remove("pp-focus-mode");
	}, [started, ended.finished]);

	async function start() {
		// Full screen is asked for, not required: if the browser refuses, the
		// round starts anyway.
		await enterFullscreen();
		setStarted(true);
	}

	async function finishInterview() {
		setFinishing(true);
		setFinishError("");
		try {
			const response = await fetch(`${base}/interview-end/finish`, {
				method: "POST",
				headers: buildApiHeaders(accessToken, { "Content-Type": "application/json" }),
				body: JSON.stringify({ session_id: sessionId, round: roundKey }),
			});
			if (!response.ok) throw new Error("finish failed");
			publish(await response.json());
			if (isFullscreen()) await document.exitFullscreen().catch(() => {});
			onNavigate?.("report");
		} catch {
			setFinishing(false);
			setFinishError("Couldn't finish the interview. Check your connection and try again.");
		}
	}

	if (ended.finished) {
		return (
			<section className="pp-card round-shell round-shell--finished" role="status">
				<CheckCircle2 size={28} aria-hidden="true" />
				<h2>Interview finished</h2>
				<p>You ended this interview early. Your answers up to that point are saved in your report.</p>
				<button type="button" className="primary-button" onClick={() => onNavigate?.("report")}>
					View my report
				</button>
			</section>
		);
	}

	if (!started) {
		return (
			<section className="pp-card round-shell">
				<Maximize size={26} aria-hidden="true" />
				<h2>{roundLabel} works best in full screen</h2>
				<p>Full screen hides everything else so you can focus, like a real interview. You can leave it any time with Esc.</p>
				<button type="button" className="primary-button" onClick={start} disabled={!loaded}>
					Enter full screen and start
				</button>
			</section>
		);
	}

	return (
		<>
			{confirmFinish ? null : (
				<div className="round-shell-bar">
					<button type="button" className="round-shell-bar__button round-shell-bar__finish" onClick={() => setConfirmFinish(true)}>
						Finish interview
					</button>
					<button
						type="button"
						className="round-shell-bar__button"
						onClick={() => (fullscreen ? document.exitFullscreen().catch(() => {}) : enterFullscreen())}
						aria-label={fullscreen ? "Exit full screen" : "Enter full screen"}
						title={fullscreen ? "Exit full screen" : "Enter full screen"}
					>
						{fullscreen ? <Minimize size={16} aria-hidden="true" /> : <Maximize size={16} aria-hidden="true" />}
					</button>
				</div>
			)}
			{children}
			{confirmFinish ? (
				<div className="round-shell-overlay" role="alertdialog" aria-modal="true" aria-labelledby="round-finish-title">
					<div className="round-shell-overlay__card">
						<h2 id="round-finish-title">Finish the interview now?</h2>
						<p>
							Rounds you haven't completed will be left out, and your report will cover what you've done so far.
							You can't come back to this interview afterwards.
						</p>
						<div className="round-shell-overlay__actions">
							<button type="button" className="secondary-button" onClick={() => setConfirmFinish(false)} disabled={finishing} autoFocus>
								Keep going
							</button>
							<button type="button" className="primary-button round-shell-overlay__danger" onClick={finishInterview} disabled={finishing}>
								{finishing ? "Finishing…" : "Finish interview"}
							</button>
						</div>
						{finishError ? <p className="error-banner">{finishError}</p> : null}
					</div>
				</div>
			) : null}
		</>
	);
}
