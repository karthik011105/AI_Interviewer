import { useCallback, useState } from "react";
import { ArrowRight, BarChart3, FileText, Mic, Moon, Sun } from "lucide-react";

import AuthDialog from "../components/AuthDialog";
import BrandLogo from "../components/BrandLogo";
import ParticleField from "../components/ParticleField";
import "./landing.css";

const FEATURES = [
	{
		icon: FileText,
		tone: "sun",
		title: "Built from your resume",
		body: "Questions target the role you want and the skills you actually listed.",
	},
	{
		icon: Mic,
		tone: "sky",
		title: "Talk, don't type",
		body: "A live AI interviewer asks out loud and follows up on what you say.",
	},
	{
		icon: BarChart3,
		tone: "mint",
		title: "Know what to fix",
		body: "Every answer is scored, with a clear report on where to improve.",
	},
];


const ROUNDS = ["Technical", "Coding", "Projects", "HR"];

export default function LandingPage({
	authState,
	googleClientId,
	theme,
	onToggleTheme,
	onSignIn,
	onSignUp,
	onForgotPassword,
	onGoogle,
	onClearMessages,
}) {
	const [dialogMode, setDialogMode] = useState(null);

	const openDialog = useCallback(
		(mode) => {
			onClearMessages?.();
			setDialogMode(mode);
		},
		[onClearMessages],
	);
	const closeDialog = useCallback(() => setDialogMode(null), []);

	return (
		<div className="landing">
			<ParticleField theme={theme} />
			<header className="landing-nav">
				<a className="landing-brand" href="/" aria-label="Interview Simulator home">
					<BrandLogo size={36} />
				</a>
				<nav className="landing-nav__actions">
					<button
						type="button"
						className="landing-icon-btn"
						onClick={onToggleTheme}
						aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
					>
						{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
					</button>
					<button type="button" className="landing-btn landing-btn--ghost" onClick={() => openDialog("signin")}>
						Log in
					</button>
					<button type="button" className="landing-btn landing-btn--primary" onClick={() => openDialog("signup")}>
						Sign up
					</button>
				</nav>
			</header>

			<main>
				<section className="landing-hero">
					<div className="landing-hero__copy">
						<p className="landing-kicker">AI mock interviews for freshers</p>
						<h1>
							Practise the interview <span className="landing-highlight">before it counts.</span>
						</h1>
						<p className="landing-lede">
							A live AI interviewer that asks real questions out loud, listens to your answers, and shows you exactly what to fix.
						</p>
						<div className="landing-hero__ctas">
							<button type="button" className="landing-btn landing-btn--primary landing-btn--lg" onClick={() => openDialog("signup")}>
								Start practising free <ArrowRight size={18} />
							</button>
							<button type="button" className="landing-btn landing-btn--ghost landing-btn--lg" onClick={() => openDialog("signin")}>
								I have an account
							</button>
						</div>
						<ul className="landing-rounds" aria-label="Interview rounds">
							{ROUNDS.map((round) => (
								<li key={round}>{round}</li>
							))}
						</ul>
					</div>

					<div className="landing-preview" aria-hidden="true">
						<div className="landing-preview__card">
							<div className="landing-preview__bar">
								<span className="landing-preview__dot" />
								<span>Technical round · live</span>
							</div>
							<div className="landing-bubble landing-bubble--ai">
								<strong>Interviewer</strong>
								You listed FastAPI and MongoDB. Why choose a document store for the order service?
							</div>
							<div className="landing-bubble landing-bubble--you">
								<strong>You</strong>
								Orders vary a lot by product type, so a flexible schema kept writes simple…
							</div>
							<div className="landing-preview__footer">
								<span className="landing-mic"><Mic size={16} /></span>
								<span className="landing-wave"><i /><i /><i /><i /><i /><i /><i /></span>
								<span className="landing-score">Score 8.4</span>
							</div>
						</div>
					</div>
				</section>

				<section className="landing-features" aria-label="Features">
					{FEATURES.map(({ icon: Icon, tone, title, body }) => (
						<article key={title} className={`landing-feature landing-feature--${tone}`}>
							<span className="landing-feature__icon"><Icon size={22} strokeWidth={2.2} /></span>
							<h3>{title}</h3>
							<p>{body}</p>
						</article>
					))}
				</section>


				<section className="landing-cta">
					<h2>Your next interview starts here.</h2>
					<button type="button" className="landing-btn landing-btn--light landing-btn--lg" onClick={() => openDialog("signup")}>
						Create free account <ArrowRight size={18} />
					</button>
				</section>
			</main>

			<footer className="landing-footer">
				<span>© {new Date().getFullYear()} Interview Simulator</span>
				<span>Practice made for freshers.</span>
			</footer>

			<AuthDialog
				open={Boolean(dialogMode)}
				mode={dialogMode || "signin"}
				onModeChange={openDialog}
				onClose={closeDialog}
				authState={authState}
				googleClientId={googleClientId}
				theme={theme}
				onSignIn={onSignIn}
				onSignUp={onSignUp}
				onForgotPassword={onForgotPassword}
				onGoogle={onGoogle}
			/>
		</div>
	);
}
