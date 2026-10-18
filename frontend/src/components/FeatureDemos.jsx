/*
 * Small looping demos shown inside each landing feature card, so the card
 * shows the idea rather than only describing it. Decorative (aria-hidden);
 * motion is CSS-only and switched off under prefers-reduced-motion.
 */

export function ResumeDemo() {
	return (
		<div className="feature-demo feature-demo--resume" aria-hidden="true">
			<div className="feature-demo__resume">
				<span className="feature-demo__line feature-demo__line--title" />
				<span className="feature-demo__chip">Python</span>
				<span className="feature-demo__chip">FastAPI</span>
				<span className="feature-demo__chip">MongoDB</span>
				<span className="feature-demo__line" />
				<span className="feature-demo__line feature-demo__line--short" />
			</div>
			<span className="feature-demo__arrow">→</span>
			<div className="feature-demo__questions">
				<span>Why async I/O in FastAPI?</span>
				<span>When would you index in MongoDB?</span>
				<span>How do you test an API?</span>
			</div>
		</div>
	);
}

export function VoiceDemo() {
	return (
		<div className="feature-demo feature-demo--voice" aria-hidden="true">
			<div className="feature-demo__voice-head">
				<span className="feature-demo__rec" /> Listening
			</div>
			<div className="feature-demo__bars">
				{Array.from({ length: 22 }, (_, index) => (
					<i key={index} style={{ animationDelay: `${(index * 73) % 900}ms` }} />
				))}
			</div>
			<p className="feature-demo__transcript">
				"I'd cache the hot reads in Redis and<span className="feature-demo__typing"> invalidate on write…</span>"
			</p>
		</div>
	);
}

export function ScoreDemo() {
	const rows = [
		["Clarity", 82],
		["Depth", 68],
		["Confidence", 90],
	];
	return (
		<div className="feature-demo feature-demo--score" aria-hidden="true">
			{rows.map(([label, value], index) => (
				<div key={label} className="feature-demo__row">
					<span>{label}</span>
					<span className="feature-demo__track">
						<i style={{ "--w": `${value}%`, animationDelay: `${index * 180}ms` }} />
					</span>
					<b>{(value / 10).toFixed(1)}</b>
				</div>
			))}
			<p className="feature-demo__tip">Tip: add a concrete example to show depth.</p>
		</div>
	);
}
