import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Maximize, ShieldAlert } from "lucide-react";

import { buildApiHeaders } from "../lib/api";
import { normalizeProctoring, warningCopy } from "../lib/proctoring";

// A brief blur happens when the mic permission prompt or a native file dialog
// opens; only a sustained one means the candidate went to another window.
const BLUR_GRACE_MS = 1500;
// After returning to full screen, ignore the events the transition itself fires.
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
	const watching = useRef(false);
	const blurTimer = useRef(null);
	const base = String(apiBaseUrl || "").replace(/\/+$/, "");

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
			if (!watching.current) return;
			watching.current = false;
			clearTimeout(blurTimer.current);
			let next = { ...state, warnings: state.warnings + 1 };
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
		},
		[accessToken, base, publish, roundKey, sessionId, state],
	);

	// Watch only while the round is running in full screen with no warning up.
	useEffect(() => {
		if (!started || warning || state.terminated || !isFullscreen()) return undefined;
		const arm = setTimeout(() => {
			watching.current = true;
		}, RESUME_GRACE_MS);

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
			watching.current = false;
			clearTimeout(arm);
			clearTimeout(blurTimer.current);
			document.removeEventListener("fullscreenchange", onFullscreen);
			document.removeEventListener("visibilitychange", onVisibility);
			window.removeEventListener("blur", onBlur);
			window.removeEventListener("focus", onFocus);
		};
	}, [started, warning, state.terminated, report]);

	// Distraction-free layout (no top bar / sidebar) while the round runs.
	useEffect(() => {
		const active = started && !state.terminated;
		document.body.classList.toggle("pp-focus-mode", active);
		return () => document.body.classList.remove("pp-focus-mode");
	}, [started, state.terminated]);

	async function goFullscreen() {
		setEnterFailed(false);
		if (await enterFullscreen()) {
			setWarning(null);
			setStarted(true);
		} else {
			setEnterFailed(true);
		}
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
			{children}
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
