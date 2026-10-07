import { useEffect, useState } from "react";
import { Mic } from "lucide-react";

const SCENARIOS = [
	{
		round: "Technical round",
		question: "You listed FastAPI and MongoDB. Why choose a document store for the order service?",
		answer: "Orders vary a lot by product type, so a flexible schema kept writes simple and fast.",
		score: 8.4,
	},
	{
		round: "Project discussion",
		question: "What was the hardest bug in your chat app, and how did you track it down?",
		answer: "Messages arrived out of order under load, so I added sequence numbers and replayed gaps.",
		score: 8.9,
	},
	{
		round: "HR round",
		question: "Tell me about a time you got tough feedback. What did you change?",
		answer: "My mentor said my pull requests were too large, so I split work into small reviewed steps.",
		score: 9.1,
	},
];

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The hero's sample interview, played live on a loop: the interviewer types,
 * the question streams in, the answer is "transcribed" word by word while the
 * mic is hot, then it is scored. Static final frame under reduced motion.
 */
export default function LivePreview() {
	const reduceMotion =
		typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
	const [index, setIndex] = useState(0);
	const [phase, setPhase] = useState(reduceMotion ? "done" : "typing");
	const [question, setQuestion] = useState(reduceMotion ? SCENARIOS[0].question : "");
	const [answer, setAnswer] = useState(reduceMotion ? SCENARIOS[0].answer : "");
	const [score, setScore] = useState(reduceMotion ? SCENARIOS[0].score : 0);

	useEffect(() => {
		if (reduceMotion) return undefined;
		let cancelled = false;
		const scenario = SCENARIOS[index];

		async function play() {
			setPhase("typing");
			setQuestion("");
			setAnswer("");
			setScore(0);
			await wait(900);
			setPhase("asking");
			for (let i = 1; i <= scenario.question.length; i += 1) {
				if (cancelled) return;
				setQuestion(scenario.question.slice(0, i));
				await wait(22);
			}
			await wait(600);
			if (cancelled) return;
			setPhase("answering");
			const words = scenario.answer.split(" ");
			for (let i = 1; i <= words.length; i += 1) {
				if (cancelled) return;
				setAnswer(words.slice(0, i).join(" "));
				await wait(170);
			}
			await wait(400);
			if (cancelled) return;
			setPhase("scoring");
			await wait(900);
			const steps = 18;
			for (let i = 1; i <= steps; i += 1) {
				if (cancelled) return;
				setScore((scenario.score * i) / steps);
				await wait(35);
			}
			setPhase("done");
			await wait(2600);
			if (!cancelled) setIndex((current) => (current + 1) % SCENARIOS.length);
		}

		play();
		return () => {
			cancelled = true;
		};
	}, [index, reduceMotion]);

	const scenario = SCENARIOS[index];
	const listening = phase === "answering";

	return (
		<div className="landing-preview__card live-preview" aria-hidden="true">
			<div className="landing-preview__bar">
				<span className="landing-preview__dot live-preview__rec" />
				<span>{scenario.round} · live</span>
			</div>

			<div className="landing-bubble landing-bubble--ai">
				<strong>Interviewer</strong>
				{phase === "typing" ? (
					<span className="live-preview__typing"><i /><i /><i /></span>
				) : (
					<>
						{question}
						{phase === "asking" ? <span className="live-preview__caret" /> : null}
					</>
				)}
			</div>

			{answer || listening ? (
				<div className="landing-bubble landing-bubble--you">
					<strong>You {listening ? <em className="live-preview__live">● live</em> : null}</strong>
					{answer}
					{listening ? <span className="live-preview__caret live-preview__caret--dark" /> : null}
				</div>
			) : (
				<div className="live-preview__spacer" />
			)}

			<div className="landing-preview__footer">
				<span className={`landing-mic${listening ? " landing-mic--hot" : ""}`}>
					<Mic size={16} />
				</span>
				<span className={`landing-wave${listening ? "" : " landing-wave--idle"}`}>
					<i /><i /><i /><i /><i /><i /><i /><i /><i />
				</span>
				<span className={`landing-score${phase === "scoring" ? " landing-score--pending" : ""}`}>
					{phase === "scoring"
						? "Scoring…"
						: phase === "done" || score > 0
							? `Score ${score.toFixed(1)}`
							: phase === "answering"
								? "Listening…"
								: "Waiting"}
				</span>
			</div>
		</div>
	);
}
