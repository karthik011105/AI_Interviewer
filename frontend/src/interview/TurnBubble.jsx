export default function TurnBubble({ turn }) {
	const isInterviewer = turn.role === "interviewer";

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
				{turn.text}
				{turn.streaming ? <span className="turn-bubble__cursor" aria-hidden="true" /> : null}
			</p>

		</article>
	);
}
