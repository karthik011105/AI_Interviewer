import { useEffect, useState } from "react";
import { Clock } from "lucide-react";

function format(seconds) {
	const safe = Math.max(0, Math.floor(seconds));
	return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

/** Compact countdown to a deadline: "18:20 left", amber in the last 5 minutes. */
export default function TimerPill({ deadlineAt }) {
	const [now, setNow] = useState(() => Date.now());

	useEffect(() => {
		if (!deadlineAt) return undefined;
		const id = window.setInterval(() => setNow(Date.now()), 1000);
		return () => window.clearInterval(id);
	}, [deadlineAt]);

	const deadline = Date.parse(deadlineAt || "");
	if (!Number.isFinite(deadline)) return null;
	const remaining = (deadline - now) / 1000;
	const tone = remaining <= 0 ? "over" : remaining <= 300 ? "low" : "ok";

	return (
		<span className={`pp-timer pp-timer--${tone}`} role="timer" aria-live="off">
			<Clock size={15} aria-hidden="true" />
			{remaining <= 0 ? "Time's up" : `${format(remaining)} left`}
		</span>
	);
}
