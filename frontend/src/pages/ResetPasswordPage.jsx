import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { authClient } from "../lib/authClient";

export default function ResetPasswordPage() {
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const token = (searchParams.get("token") || "").trim();

	const [newPassword, setNewPassword] = useState("");
	const [confirmPassword, setConfirmPassword] = useState("");
	const [status, setStatus] = useState("idle"); // idle | submitting | success | error
	const [errorMessage, setErrorMessage] = useState("");

	const isBusy = status === "submitting";

	async function handleSubmit(event) {
		event.preventDefault();
		if (isBusy) {
			return;
		}
		if (newPassword !== confirmPassword) {
			setStatus("error");
			setErrorMessage("Those passwords don't match.");
			return;
		}

		setStatus("submitting");
		setErrorMessage("");
		try {
			await authClient.resetPassword(token, newPassword);
			setStatus("success");
		} catch (error) {
			setStatus("error");
			setErrorMessage(error.message);
		}
	}

	if (!token) {
		return (
			<section className="page-shell reset-password-shell">
				<section className="glass-panel app-gate">
					<div className="panel-head panel-head--tight">
						<div>
							<p className="section-kicker">Reset password</p>
							<h2>This link is incomplete.</h2>
						</div>
						<span className="status-pill status-pill--offline">No token</span>
					</div>
					<p className="hero-text">
						The reset link is missing its token. Request a new one from the sign-in
						panel's "Forgot password?" link.
					</p>
				</section>
			</section>
		);
	}

	if (status === "success") {
		return (
			<section className="page-shell reset-password-shell">
				<section className="glass-panel app-gate">
					<div className="panel-head panel-head--tight">
						<div>
							<p className="section-kicker">Reset password</p>
							<h2>Password updated.</h2>
						</div>
						<span className="status-pill status-pill--online">Done</span>
					</div>
					<p className="hero-text">Sign in with your new password to continue.</p>
					<button className="primary-button continue-button" onClick={() => navigate("/resume-intake")}>
						Continue to sign in
					</button>
				</section>
			</section>
		);
	}

	return (
		<section className="page-shell reset-password-shell">
			<div className="reset-password-grid">
				<section className="glass-panel reset-password-panel">
					<div className="panel-head panel-head--tight">
						<div>
							<p className="section-kicker">Reset password</p>
							<h2>Choose a new password</h2>
						</div>
					</div>

					<form className="auth-form" onSubmit={handleSubmit}>
						<label className="field-label" htmlFor="reset-new-password">New password</label>
						<input
							id="reset-new-password"
							className="text-input"
							type="password"
							value={newPassword}
							onChange={(event) => setNewPassword(event.target.value)}
							placeholder="At least 8 characters"
							disabled={isBusy}
						/>

						<label className="field-label" htmlFor="reset-confirm-password">Confirm password</label>
						<input
							id="reset-confirm-password"
							className="text-input"
							type="password"
							value={confirmPassword}
							onChange={(event) => setConfirmPassword(event.target.value)}
							placeholder="Re-enter the new password"
							disabled={isBusy}
						/>

						<button className="primary-button" type="submit" disabled={isBusy}>
							{isBusy ? "Updating..." : "Update password"}
						</button>
					</form>

					{errorMessage ? (
						<div className="message-stack">
							<p className="error-banner">{errorMessage}</p>
						</div>
					) : null}
				</section>

				<aside className="glass-panel reset-password-aside">
					<span>Why a link</span>
					<strong>One-time and time-limited</strong>
					<p>
						This reset link works once and expires shortly after it was requested.
						Request a fresh one if it has already expired.
					</p>
				</aside>
			</div>
		</section>
	);
}
