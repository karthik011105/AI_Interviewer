import { useEffect, useRef, useState } from "react";

import TurnBubble from "./TurnBubble";

const PIN_TOLERANCE_PX = 80;

export default function ConversationThread({ turns, liveCaption, phase }) {
	const scrollerRef = useRef(null);
	const pinnedRef = useRef(true);
	const [expanded, setExpanded] = useState(() => new Set());

	// Autoscroll only while the reader is already at the bottom. Yanking them
	// back down mid-scroll would make it impossible to re-read an earlier answer
	// while the interviewer is still talking.
	useEffect(() => {
		const scroller = scrollerRef.current;
		if (!scroller || !pinnedRef.current) return;
		scroller.scrollTop = scroller.scrollHeight;
	}, [turns, liveCaption]);

	function handleScroll() {
		const scroller = scrollerRef.current;
		if (!scroller) return;
		const distanceFromBottom =
			scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
		pinnedRef.current = distanceFromBottom <= PIN_TOLERANCE_PX;
	}

	function toggle(key) {
		setExpanded((current) => {
			const next = new Set(current);
			if (next.has(key)) next.delete(key);
			else next.add(key);
			return next;
		});
	}

	const ordered = [...turns].sort((a, b) => {
		if (a.turnIndex !== b.turnIndex) return a.turnIndex - b.turnIndex;
		// Within a turn the question always precedes the answer.
		return a.role === "interviewer" ? -1 : 1;
	});

	return (
		<div className="interview-thread">
			<div
				className="interview-thread__scroller"
				ref={scrollerRef}
				onScroll={handleScroll}
				role="log"
				aria-live="polite"
			>
				{ordered.length === 0 ? (
					<p className="interview-thread__empty">
						The interviewer will open the conversation in a moment.
					</p>
				) : null}

				{ordered.map((turn) => {
					const key = `${turn.role}-${turn.turnIndex}`;
					return (
						<TurnBubble
							key={key}
							turn={turn}
							expanded={expanded.has(key)}
							onToggle={() => toggle(key)}
						/>
					);
				})}

				{liveCaption ? (
					<article className="turn-bubble turn-bubble--candidate turn-bubble--caption">
						<header className="turn-bubble__meta">
							<span className="turn-bubble__who">You</span>
							<span className="turn-bubble__chip turn-bubble__chip--pending">listening…</span>
						</header>
						<p className="turn-bubble__text">{liveCaption}</p>
					</article>
				) : null}

				{phase === "thinking" ? (
					<p className="interview-thread__thinking">Interviewer is thinking…</p>
				) : null}
			</div>
		</div>
	);
}
