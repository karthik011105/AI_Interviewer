import { useEffect, useState } from "react";

import { revealState } from "./speechReveal.js";

const LANGUAGE_LABELS = {
	java: "Java",
	python: "Python",
	javascript: "JavaScript",
	typescript: "TypeScript",
	cpp: "C++",
	"c++": "C++",
	csharp: "C#",
	go: "Go",
	sql: "SQL",
	bash: "Shell",
	sh: "Shell",
	yaml: "YAML",
	dockerfile: "Dockerfile",
	text: "Problem",
};

// ~25 frames a second is smooth enough for typing and cheap to render.
const TYPING_TICK_MS = 40;

export default function TurnBubble({ turn }) {
	const isInterviewer = turn.role === "interviewer";
	const [now, setNow] = useState(() => performance.now());
	const reveal = revealState(turn, now);

	useEffect(() => {
		if (reveal.done) return undefined;
		const timer = setInterval(() => setNow(performance.now()), TYPING_TICK_MS);
		return () => clearInterval(timer);
	}, [reveal.done]);

	return (
		<article
			className={[
				"turn-bubble",
				isInterviewer ? "turn-bubble--interviewer" : "turn-bubble--candidate",
				turn.streaming ? "turn-bubble--streaming" : "",
			]
				.filter(Boolean)
				.join(" ")}
		>
			<header className="turn-bubble__meta">
				<span className="turn-bubble__who">{isInterviewer ? "Maya" : "You"}</span>

				{/*
				 * No topic/tier tags and no per-answer score here: those are the
				 * engine's planning and grading, and a real interviewer does not
				 * read them out mid-interview. Scores and feedback live in the report.
				 */}
			</header>

			<p className="turn-bubble__text">
				{reveal.done ? (
					turn.text
				) : (
					<>
						{/* Typed character by character; screen readers get whole sentences. */}
						<span aria-hidden="true">{reveal.text}</span>
						<span className="visually-hidden">{reveal.spokenText}</span>
					</>
				)}
				{turn.streaming || !reveal.done ? <span className="turn-bubble__cursor" aria-hidden="true" /> : null}
			</p>

			{/* The code appears once Maya starts talking about it. */}
			{turn.snippet?.code && (reveal.done || reveal.text) ? (
				<figure className="turn-snippet">
					<figcaption>{LANGUAGE_LABELS[turn.snippet.language] || turn.snippet.language || "Code"}</figcaption>
					<pre>
						<code>{turn.snippet.code}</code>
					</pre>
				</figure>
			) : null}

		</article>
	);
}
