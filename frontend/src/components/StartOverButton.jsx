import { useState } from "react";
import { RotateCcw } from "lucide-react";

import { buildApiHeaders } from "../lib/api";

/**
 * Start the whole interview again: clears the assessment, every round, the
 * coding round and the report, keeps the resume and chosen role. Works after
 * "Finish interview" too - the server reopens the interview.
 */
export default function StartOverButton({ accessToken, apiBaseUrl, sessionId, onRestarted, className = "secondary-button" }) {
	const [confirming, setConfirming] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const base = String(apiBaseUrl || "").replace(/\/+$/, "");

	async function restart() {
		setBusy(true);
		setError("");
		try {
			const response = await fetch(`${base}/workflow/reset`, {
				method: "POST",
				headers: buildApiHeaders(accessToken, { "Content-Type": "application/json" }),
				body: JSON.stringify({ session_id: sessionId, target: "entire_interview" }),
			});
			const payload = await response.json().catch(() => ({}));
			if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Couldn't start over.");
			setConfirming(false);
			onRestarted?.(payload);
		} catch (err) {
			setError(err.message || "Couldn't start over. Check your connection and try again.");
		} finally {
			setBusy(false);
		}
	}

	return (
		<>
			<button type="button" className={className} onClick={() => setConfirming(true)} disabled={!sessionId || busy}>
				<RotateCcw size={16} aria-hidden="true" />
				Start over
			</button>
			{confirming ? (
				<div className="round-shell-overlay" role="alertdialog" aria-modal="true" aria-labelledby="start-over-title">
					<div className="round-shell-overlay__card">
						<h2 id="start-over-title">Start the interview again?</h2>
						<p>
							Your assessment, every interview round, the coding round and this report are cleared. Your resume and
							chosen role stay, and you begin again from the assessment.
						</p>
						<div className="round-shell-overlay__actions">
							<button type="button" className="secondary-button" onClick={() => setConfirming(false)} disabled={busy} autoFocus>
								Cancel
							</button>
							<button type="button" className="primary-button round-shell-overlay__danger" onClick={restart} disabled={busy}>
								{busy ? "Clearing…" : "Start over"}
							</button>
						</div>
						{error ? <p className="error-banner">{error}</p> : null}
					</div>
				</div>
			) : null}
		</>
	);
}
