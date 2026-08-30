const TIER_LABEL = {
	strong: "depth check",
	familiar: "edge cases",
	mentioned: "beyond the name",
	absent: "fundamentals",
	general: "general",
};

function scoreTone(score) {
	if (score >= 0.7) return "good";
	if (score >= 0.45) return "warn";
	return "bad";
}

function RubricBar({ label, value }) {
	const pct = Math.round(Math.max(0, Math.min(1, Number(value) || 0)) * 100);
	return (
		<div className="turn-bubble__rubric-row">
			<span className="turn-bubble__rubric-label">{label}</span>
			<span className="turn-bubble__rubric-track">
				<span className="turn-bubble__rubric-fill" style={{ width: `${pct}%` }} />
			</span>
			<span className="turn-bubble__rubric-value">{pct}</span>
		</div>
	);
}

export default function TurnBubble({ turn, expanded, onToggle }) {
	const isInterviewer = turn.role === "interviewer";
	const rubric = turn.evaluation?.rubric || null;

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
				<span className="turn-bubble__who">{isInterviewer ? "Interviewer" : "You"}</span>

				{isInterviewer && turn.action === "follow_up" ? (
					<span className="turn-bubble__chip turn-bubble__chip--followup">follow-up</span>
				) : null}
				{isInterviewer && turn.action === "probe_deeper" ? (
					<span className="turn-bubble__chip turn-bubble__chip--followup">going deeper</span>
				) : null}
				{isInterviewer && turn.focusSkill ? (
					<span className="turn-bubble__chip">{turn.focusSkill}</span>
				) : null}
				{isInterviewer && turn.tier ? (
					<span className="turn-bubble__chip turn-bubble__chip--tier">
						{TIER_LABEL[turn.tier] || turn.tier}
					</span>
				) : null}

				{!isInterviewer && turn.scoring ? (
					<span className="turn-bubble__chip turn-bubble__chip--pending">scoring…</span>
				) : null}
				{!isInterviewer && turn.scoringFailed ? (
					<span className="turn-bubble__chip turn-bubble__chip--failed">not scored</span>
				) : null}
				{!isInterviewer && typeof turn.score === "number" ? (
					<button
						type="button"
						className={`turn-bubble__score turn-bubble__score--${scoreTone(turn.score)}`}
						onClick={onToggle}
						aria-expanded={Boolean(expanded)}
					>
						{turn.score.toFixed(2)}
						<span className="turn-bubble__score-caret">{expanded ? "▴" : "▾"}</span>
					</button>
				) : null}
			</header>

			<p className="turn-bubble__text">
				{turn.text}
				{turn.streaming ? <span className="turn-bubble__cursor" aria-hidden="true" /> : null}
			</p>

			{expanded && turn.evaluation ? (
				<div className="turn-bubble__detail">
					{rubric ? (
						<div className="turn-bubble__rubric">
							<RubricBar label="Relevance" value={rubric.relevance} />
							<RubricBar label="Depth" value={rubric.depth} />
							<RubricBar label="Accuracy" value={rubric.accuracy} />
						</div>
					) : null}
					{turn.evaluation.feedback ? (
						<p className="turn-bubble__feedback">{turn.evaluation.feedback}</p>
					) : null}
				</div>
			) : null}
		</article>
	);
}
