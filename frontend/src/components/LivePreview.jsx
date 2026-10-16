import { useEffect, useRef, useState } from "react";
import { Mic } from "lucide-react";

// One continuous conversation, so follow-ups read naturally as it scrolls.
const SCRIPT = [
	{
		round: "Technical round",
		question: "You listed FastAPI and MongoDB. Why choose a document store for the order service?",
		answer: "Orders vary a lot by product type, so a flexible schema kept writes simple and fast.",
		score: 8.4,
	},
	{
		round: "Technical round",
		question: "Good. How would you stop that flexibility from turning into messy data?",
		answer: "Validate at the API with Pydantic models, and add indexes on the fields we query most.",
		score: 8.7,
	},
	{
		round: "Project discussion",
		question: "Let's talk about your chat app. What was the hardest bug you hit?",
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

const MAX_MESSAGES = 7;
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The sample interview as a live, scrolling chat: each question streams in,
 * the answer is transcribed word by word, and older
 * messages slide up and fade out of the top. Static under reduced motion.
 */
export default function LivePreview() {
	const reduceMotion =
		typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
	const [messages, setMessages] = useState(() =>
		reduceMotion
			? [
					{ id: 1, role: "ai", text: SCRIPT[0].question },
					{ id: 2, role: "you", text: SCRIPT[0].answer },
				]
			: [],
	);
	const [phase, setPhase] = useState("idle");
	const [round, setRound] = useState(SCRIPT[0].round);
	const nextId = useRef(10);

	useEffect(() => {
		if (reduceMotion) return undefined;
		let cancelled = false;

		const push = (message) => {
			const id = nextId.current++;
			setMessages((current) => [...current, { id, ...message }].slice(-MAX_MESSAGES));
			return id;
		};
		const update = (id, text) =>
			setMessages((current) => current.map((m) => (m.id === id ? { ...m, text } : m)));

		async function run() {
			// Start clean: in development React mounts effects twice, and the
			// first (cancelled) run has already pushed its typing bubble.
			setMessages([]);
			let turn = 0;
			while (!cancelled) {
				const step = SCRIPT[turn % SCRIPT.length];
				setRound(step.round);

				setPhase("typing");
				const qId = push({ role: "ai", text: "", typing: true });
				await wait(900);
				if (cancelled) return;
				setPhase("asking");
				for (let i = 1; i <= step.question.length; i += 1) {
					if (cancelled) return;
					update(qId, step.question.slice(0, i));
					await wait(20);
				}
				setMessages((current) => current.map((m) => (m.id === qId ? { ...m, typing: false } : m)));
				await wait(700);
				if (cancelled) return;

				setPhase("answering");
				const aId = push({ role: "you", text: "" });
				const words = step.answer.split(" ");
				for (let i = 1; i <= words.length; i += 1) {
					if (cancelled) return;
					update(aId, words.slice(0, i).join(" "));
					await wait(160);
				}
				await wait(400);
				if (cancelled) return;

				setPhase("scoring");
				await wait(900);
				if (cancelled) return;
				setPhase("idle");
				await wait(1600);
				turn += 1;
			}
		}

		run();
		return () => {
			cancelled = true;
		};
	}, [reduceMotion]);

	const listening = phase === "answering";
	const latestId = messages.length ? messages[messages.length - 1].id : null;

	return (
		<div className="landing-preview__card live-preview" aria-hidden="true">
			<div className="landing-preview__bar">
				<span className="landing-preview__dot live-preview__rec" />
				<span key={round} className="live-preview__round">{round} · live</span>
			</div>

			<div className="live-thread">
				<div className="live-thread__list">
					{messages.map((message) => {
						const isLatest = message.id === latestId;
						const mine = message.role === "you";
						return (
							<div key={message.id} className={`live-msg live-msg--${mine ? "you" : "ai"}`}>
								<div className={`landing-bubble landing-bubble--${mine ? "you" : "ai"}`}>
									<strong>
										{mine ? "You" : "Maya"}
										{mine && isLatest && listening ? <em className="live-preview__live">● live</em> : null}
									</strong>
									{message.typing && !message.text ? (
										<span className="live-preview__typing"><i /><i /><i /></span>
									) : (
										<>
											{message.text}
											{isLatest && (phase === "asking" || (mine && listening)) ? (
												<span className={`live-preview__caret${mine ? " live-preview__caret--dark" : ""}`} />
											) : null}
										</>
									)}
								</div>
							</div>
						);
					})}
				</div>
			</div>

			<div className="landing-preview__footer">
				<span className={`landing-mic${listening ? " landing-mic--hot" : ""}`}>
					<Mic size={16} />
				</span>
				<span className={`landing-wave${listening ? "" : " landing-wave--idle"}`}>
					<i /><i /><i /><i /><i /><i /><i /><i /><i />
				</span>
			</div>
		</div>
	);
}
