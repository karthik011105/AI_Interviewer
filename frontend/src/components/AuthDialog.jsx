import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

import GoogleSignInButton from "./GoogleSignInButton";

const COPY = {
	signin: { title: "Welcome back", subtitle: "Log in to continue your practice.", submit: "Log in", busy: "Logging in…" },
	signup: { title: "Create your account", subtitle: "Free. Takes under a minute.", submit: "Create account", busy: "Creating account…" },
	forgot: { title: "Reset your password", subtitle: "We will email you a link to set a new one.", submit: "Send reset link", busy: "Sending…" },
};

export default function AuthDialog({
	open,
	mode,
	onModeChange,
	onClose,
	authState,
	googleClientId,
	theme,
	onSignIn,
	onSignUp,
	onForgotPassword,
	onGoogle,
}) {
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [googleError, setGoogleError] = useState("");
	const dialogRef = useRef(null);
	const emailRef = useRef(null);

	const busy = authState?.busyAction && authState.busyAction !== "idle";
	const copy = COPY[mode] || COPY.signin;

	useEffect(() => {
		if (!open) return undefined;
		setGoogleError("");
		const timer = setTimeout(() => emailRef.current?.focus(), 30);
		function onKey(event) {
			if (event.key === "Escape") onClose();
		}
		document.addEventListener("keydown", onKey);
		document.body.style.overflow = "hidden";
		return () => {
			clearTimeout(timer);
			document.removeEventListener("keydown", onKey);
			document.body.style.overflow = "";
		};
	}, [open, mode, onClose]);

	if (!open) return null;

	function submit(event) {
		event.preventDefault();
		if (mode === "forgot") onForgotPassword(email);
		else if (mode === "signup") onSignUp({ email, password });
		else onSignIn({ email, password });
	}

	return (
		<div
			className="auth-dialog__scrim"
			onMouseDown={(event) => {
				if (event.target === event.currentTarget) onClose();
			}}
		>
			<div className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-dialog-title" ref={dialogRef}>
				<button type="button" className="auth-dialog__close" onClick={onClose} aria-label="Close">
					<X size={18} />
				</button>

				<h2 id="auth-dialog-title">{copy.title}</h2>
				<p className="auth-dialog__subtitle">{copy.subtitle}</p>

				{mode !== "forgot" ? (
					<>
						<div className="auth-dialog__tabs" role="tablist">
							<button type="button" role="tab" aria-selected={mode === "signin"} className={mode === "signin" ? "is-active" : ""} onClick={() => onModeChange("signin")}>
								Log in
							</button>
							<button type="button" role="tab" aria-selected={mode === "signup"} className={mode === "signup" ? "is-active" : ""} onClick={() => onModeChange("signup")}>
								Sign up
							</button>
						</div>

						<GoogleSignInButton
							clientId={googleClientId}
							mode={mode}
							theme={theme}
							disabled={busy}
							onCredential={onGoogle}
							onError={setGoogleError}
						/>
						{googleError ? <p className="auth-dialog__error">{googleError}</p> : null}

						<div className="auth-dialog__divider"><span>or with email</span></div>
					</>
				) : null}

				<form className="auth-dialog__form" onSubmit={submit}>
					<label>
						<span>Email</span>
						<input
							ref={emailRef}
							type="email"
							autoComplete="email"
							required
							value={email}
							onChange={(event) => setEmail(event.target.value)}
							placeholder="you@gmail.com"
						/>
					</label>
					{mode !== "forgot" ? (
						<label>
							<span className="auth-dialog__label-row">
								Password
								{mode === "signin" ? (
									<button type="button" className="auth-dialog__link" onClick={() => onModeChange("forgot")}>
										Forgot?
									</button>
								) : null}
							</span>
							<input
								type="password"
								autoComplete={mode === "signup" ? "new-password" : "current-password"}
								required
								minLength={mode === "signup" ? 8 : undefined}
								value={password}
								onChange={(event) => setPassword(event.target.value)}
								placeholder={mode === "signup" ? "At least 8 characters" : "Your password"}
							/>
						</label>
					) : null}

					{authState?.errorMessage ? <p className="auth-dialog__error">{authState.errorMessage}</p> : null}
					{authState?.infoMessage && mode === "forgot" ? <p className="auth-dialog__info">{authState.infoMessage}</p> : null}

					<button type="submit" className="landing-btn landing-btn--primary landing-btn--block" disabled={busy}>
						{busy ? copy.busy : copy.submit}
					</button>
				</form>

				{mode === "forgot" ? (
					<button type="button" className="auth-dialog__link auth-dialog__back" onClick={() => onModeChange("signin")}>
						Back to log in
					</button>
				) : (
					<p className="auth-dialog__fineprint">
						{mode === "signin" ? "New here? " : "Already have an account? "}
						<button type="button" className="auth-dialog__link" onClick={() => onModeChange(mode === "signin" ? "signup" : "signin")}>
							{mode === "signin" ? "Create an account" : "Log in"}
						</button>
					</p>
				)}
			</div>
		</div>
	);
}
