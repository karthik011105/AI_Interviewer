import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Eye, EyeOff, X } from "lucide-react";

import BrandLogo from "./BrandLogo";
import GoogleSignInButton from "./GoogleSignInButton";
import ParticleField from "./ParticleField";

const SLIDES = [
	"Practise out loud. Walk in ready.",
	"Questions built from your own resume.",
	"Know exactly what to fix next.",
];

const COPY = {
	signin: {
		title: "Welcome back",
		switchText: "Don't have an account?",
		switchLabel: "Sign up",
		switchTo: "signup",
		submit: "Log in",
		busy: "Logging in…",
		divider: "Or continue with",
	},
	signup: {
		title: "Create an account",
		switchText: "Already have an account?",
		switchLabel: "Log in",
		switchTo: "signin",
		submit: "Create account",
		busy: "Creating account…",
		divider: "Or register with",
	},
	forgot: {
		title: "Reset password",
		switchText: "Remembered it?",
		switchLabel: "Log in",
		switchTo: "signin",
		submit: "Send reset link",
		busy: "Sending…",
	},
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
	const [firstName, setFirstName] = useState("");
	const [lastName, setLastName] = useState("");
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [showPassword, setShowPassword] = useState(false);
	const [googleError, setGoogleError] = useState("");
	const [slide, setSlide] = useState(0);
	const firstFieldRef = useRef(null);

	const busy = authState?.busyAction && authState.busyAction !== "idle";
	const copy = COPY[mode] || COPY.signin;

	useEffect(() => {
		if (!open) return undefined;
		setGoogleError("");
		const focusTimer = setTimeout(() => firstFieldRef.current?.focus(), 40);
		function onKey(event) {
			if (event.key === "Escape") onClose();
		}
		document.addEventListener("keydown", onKey);
		document.body.style.overflow = "hidden";
		return () => {
			clearTimeout(focusTimer);
			document.removeEventListener("keydown", onKey);
			document.body.style.overflow = "";
		};
	}, [open, mode, onClose]);

	useEffect(() => {
		if (!open) return undefined;
		const timer = setInterval(() => setSlide((current) => (current + 1) % SLIDES.length), 4500);
		return () => clearInterval(timer);
	}, [open]);

	if (!open) return null;

	function submit(event) {
		event.preventDefault();
		if (mode === "forgot") onForgotPassword(email);
		else if (mode === "signup") onSignUp({ email, password, firstName, lastName });
		else onSignIn({ email, password });
	}

	return (
		<div
			className="auth-modal__scrim"
			onMouseDown={(event) => {
				if (event.target === event.currentTarget) onClose();
			}}
		>
			<div className="auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-modal-title">
				<aside className="auth-modal__visual" aria-hidden="true">
					<ParticleField theme="dark" contained density={1.4} />
					<div className="auth-modal__visual-top">
						<BrandLogo size={32} />
						<button type="button" className="auth-modal__back" onClick={onClose} tabIndex={-1}>
							Back to website <ArrowLeft size={15} style={{ transform: "rotate(180deg)" }} />
						</button>
					</div>
					<div className="auth-modal__orb" />
					<div className="auth-modal__visual-bottom">
						<p key={slide} className="auth-modal__slide">{SLIDES[slide]}</p>
						<div className="auth-modal__dots">
							{SLIDES.map((text, index) => (
								<button
									key={text}
									type="button"
									tabIndex={-1}
									className={index === slide ? "is-active" : ""}
									onClick={() => setSlide(index)}
								/>
							))}
						</div>
					</div>
				</aside>

				<section className="auth-modal__form-side">
					<button type="button" className="auth-modal__close" onClick={onClose} aria-label="Close">
						<X size={18} />
					</button>

					<h2 id="auth-modal-title">{copy.title}</h2>
					<p className="auth-modal__switch">
						{copy.switchText}{" "}
						<button type="button" onClick={() => onModeChange(copy.switchTo)}>
							{copy.switchLabel}
						</button>
					</p>

					<form className="auth-modal__form" onSubmit={submit}>
						{mode === "signup" ? (
							<div className="auth-modal__row">
								<input
									ref={firstFieldRef}
									aria-label="First name"
									placeholder="First name"
									autoComplete="given-name"
									value={firstName}
									onChange={(event) => setFirstName(event.target.value)}
								/>
								<input
									aria-label="Last name"
									placeholder="Last name"
									autoComplete="family-name"
									value={lastName}
									onChange={(event) => setLastName(event.target.value)}
								/>
							</div>
						) : null}

						<input
							ref={mode === "signup" ? undefined : firstFieldRef}
							type="email"
							aria-label="Email"
							placeholder="Email"
							autoComplete="email"
							required
							value={email}
							onChange={(event) => setEmail(event.target.value)}
						/>

						{mode !== "forgot" ? (
							<div className="auth-modal__password">
								<input
									type={showPassword ? "text" : "password"}
									aria-label="Password"
									placeholder={mode === "signup" ? "Create a password (8+ characters)" : "Enter your password"}
									autoComplete={mode === "signup" ? "new-password" : "current-password"}
									required
									minLength={mode === "signup" ? 8 : undefined}
									value={password}
									onChange={(event) => setPassword(event.target.value)}
								/>
								<button
									type="button"
									className="auth-modal__eye"
									onClick={() => setShowPassword((value) => !value)}
									aria-label={showPassword ? "Hide password" : "Show password"}
								>
									{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
								</button>
							</div>
						) : (
							<p className="auth-modal__hint">We will email you a link to set a new password.</p>
						)}

						{mode === "signin" ? (
							<button type="button" className="auth-modal__forgot" onClick={() => onModeChange("forgot")}>
								Forgot password?
							</button>
						) : null}

						{authState?.errorMessage ? <p className="auth-modal__error">{authState.errorMessage}</p> : null}
						{authState?.infoMessage && mode === "forgot" ? <p className="auth-modal__info">{authState.infoMessage}</p> : null}

						<button type="submit" className="auth-modal__submit" disabled={busy}>
							{busy ? copy.busy : copy.submit}
						</button>
					</form>

					{mode !== "forgot" ? (
						<>
							<div className="auth-modal__divider"><span>{copy.divider}</span></div>
							<GoogleSignInButton
								clientId={googleClientId}
								mode={mode}
								theme={theme}
								disabled={busy}
								onCredential={onGoogle}
								onError={setGoogleError}
							/>
							{googleError ? <p className="auth-modal__error">{googleError}</p> : null}
						</>
					) : null}
				</section>
			</div>
		</div>
	);
}
