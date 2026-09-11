import { useState } from "react";

function describeAuthState(authState) {
	if (authState.status === "loading") {
		return "Checking saved session. You can still type while auth initializes.";
	}
	if (authState.status === "authenticated") {
		return "Account linked.";
	}
	return "Use password to sign in or sign up.";
}

export default function AuthPanel({ authState, onSignIn, onSignUp, onSignOut, onForgotPassword, compact = false }) {
	const [mode, setMode] = useState("sign-in");
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [showForgotPassword, setShowForgotPassword] = useState(false);
	const [forgotPasswordEmail, setForgotPasswordEmail] = useState("");

	const isBusy = authState?.busyAction && authState.busyAction !== "idle";
	const isForgotPasswordBusy = authState?.busyAction === "forgot-password";
	const isAuthenticated = authState?.status === "authenticated";
	const isInitializing = authState?.status === "loading";
	const isInputLocked = Boolean(isBusy);
	const isSubmitLocked = Boolean(isBusy) || isInitializing;
	const statusTone = isAuthenticated ? "online" : "checking";
	const activeMethodLabel = mode === "sign-up" ? "Password sign up" : "Password sign in";
	const activeMethodHint = mode === "sign-up"
		? "Creates an account for saved sessions."
		: "Uses the password for an existing account.";

	async function handleSubmit(event) {
		event.preventDefault();
		if (isSubmitLocked) {
			return;
		}
		const trimmedEmail = email.trim();
		if (!trimmedEmail || !password) {
			return;
		}
		const credentials = {
			email: trimmedEmail,
			password,
		};
		if (mode === "sign-up") {
			await onSignUp?.(credentials);
			return;
		}
		await onSignIn?.(credentials);
	}

	async function handleForgotPasswordSubmit(event) {
		event.preventDefault();
		const trimmedEmail = forgotPasswordEmail.trim();
		if (!trimmedEmail || isForgotPasswordBusy) {
			return;
		}
		await onForgotPassword?.(trimmedEmail);
	}

	if (isAuthenticated && compact) {
		return (
			<section className="auth-panel auth-panel--compact auth-shell auth-shell--compact glass-panel">
				<div className="auth-compact auth-compact--console">
					<div className="auth-compact__identity">
						<p className="section-kicker">Account</p>
						<strong>{authState.user?.email || "No email returned"}</strong>
						<p>Ready.</p>
					</div>
					<button
						type="button"
						className="secondary-button secondary-button--inline"
						onClick={() => onSignOut?.()}
						disabled={isBusy}
					>
						{isBusy ? "Signing out..." : "Sign out"}
					</button>
				</div>
			</section>
		);
	}

	return (
		<section className="auth-panel auth-shell glass-panel">
			<div className="auth-shell__hero">
				<div>
					<p className="section-kicker">Account</p>
					<h3>{isAuthenticated ? "Account linked" : "Access the workspace"}</h3>
					<p className="auth-panel__summary">{describeAuthState(authState)}</p>
				</div>
				<span className={`status-pill status-pill--${statusTone}`}>
					{isAuthenticated ? "Signed in" : "Auth ready"}
				</span>
			</div>

			{isAuthenticated ? (
				<div className="auth-shell__linked">
					<div className="auth-shell__linked-card">
						<span>Primary email</span>
						<strong>{authState.user?.email || "No email returned"}</strong>
						<p>New sessions, assessments, and reports stay tied to this account.</p>
					</div>
					<div className="auth-shell__linked-card">
						<span>Status</span>
						<strong>Workspace unlocked</strong>
						<p>You can move through protected interview stages without losing ownership.</p>
					</div>
					<div className="auth-shell__linked-actions">
						<button
							type="button"
							className="secondary-button"
							onClick={() => onSignOut?.()}
							disabled={isBusy}
						>
							{isBusy ? "Signing out..." : "Sign out"}
						</button>
					</div>
				</div>
			) : showForgotPassword ? (
				<div className="auth-shell__workspace">
					<div className="auth-shell__access-rail">
						<div className="auth-shell__guide">
							<span>Current mode</span>
							<strong>Reset password</strong>
							<p>We'll email a reset link if that address has an account.</p>
						</div>
					</div>

					<form className="auth-form auth-form--studio" onSubmit={handleForgotPasswordSubmit}>
						<label className="field-label" htmlFor="auth-forgot-email">Email</label>
						<input
							id="auth-forgot-email"
							className="text-input"
							type="email"
							value={forgotPasswordEmail}
							onChange={(event) => setForgotPasswordEmail(event.target.value)}
							placeholder="you@example.com"
							disabled={isForgotPasswordBusy}
						/>

						<button className="primary-button" type="submit" disabled={isForgotPasswordBusy}>
							{isForgotPasswordBusy ? "Sending..." : "Send reset link"}
						</button>
						<button
							type="button"
							className="secondary-button"
							onClick={() => setShowForgotPassword(false)}
							disabled={isForgotPasswordBusy}
						>
							Back to sign in
						</button>
					</form>
				</div>
			) : (
				<div className="auth-shell__workspace">
					<div className="auth-shell__access-rail">
						<div className="auth-shell__guide">
							<span>Current mode</span>
							<strong>{activeMethodLabel}</strong>
							<p>{activeMethodHint}</p>
						</div>
					</div>

					<form className="auth-form auth-form--studio" onSubmit={handleSubmit}>
						<label className="field-label" htmlFor="auth-email">Email</label>
						<input
							id="auth-email"
							className="text-input"
							type="email"
							value={email}
							onChange={(event) => setEmail(event.target.value)}
							placeholder="you@example.com"
							disabled={isInputLocked}
						/>

						<div className="auth-panel__modes auth-panel__modes--studio">
							<button
								type="button"
								className={`mode-button ${mode === "sign-in" ? "mode-button--active" : ""}`}
								onClick={() => setMode("sign-in")}
								disabled={isBusy}
							>
								Sign in
							</button>
							<button
								type="button"
								className={`mode-button ${mode === "sign-up" ? "mode-button--active" : ""}`}
								onClick={() => setMode("sign-up")}
								disabled={isBusy}
							>
								Sign up
							</button>
						</div>

						<label className="field-label" htmlFor="auth-password">Password</label>
						<input
							id="auth-password"
							className="text-input"
							type="password"
							value={password}
							onChange={(event) => setPassword(event.target.value)}
							placeholder={mode === "sign-up" ? "At least 6 characters" : "Enter your password"}
							disabled={isInputLocked}
						/>

						{mode === "sign-in" ? (
							<button
								type="button"
								className="auth-forgot-link"
								onClick={() => {
									setForgotPasswordEmail(email);
									setShowForgotPassword(true);
								}}
								disabled={isBusy}
							>
								Forgot password?
							</button>
						) : null}

						<button className="primary-button" type="submit" disabled={isSubmitLocked}>
							{isBusy
								? mode === "sign-up"
									? "Creating account..."
									: "Signing in..."
								: isInitializing
									? "Checking session..."
									: mode === "sign-up"
										? "Create account"
										: "Sign in"}
						</button>
					</form>
				</div>
			)}

			<div className="message-stack auth-panel__messages">
				{authState?.infoMessage ? <p className="info-banner">{authState.infoMessage}</p> : null}
				{authState?.errorMessage ? <p className="error-banner">{authState.errorMessage}</p> : null}
			</div>
		</section>
	);
}