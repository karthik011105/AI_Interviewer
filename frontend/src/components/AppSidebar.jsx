import { NavLink } from "react-router-dom";
import { Check, Lock } from "lucide-react";

const STEP_COPY = {
	upload: { label: "Resume", hint: "Upload and pick a role" },
	assessment: { label: "Assessment", hint: "Quick skills check" },
	technical: { label: "Technical", hint: "Live interview" },
	dsa: { label: "Coding", hint: "Two problems" },
	project_discussion: { label: "Projects", hint: "Talk through your work" },
	hr: { label: "HR", hint: "Behavioural round" },
	report: { label: "Report", hint: "Your results" },
};

const STATUS_TEXT = {
	complete: "Done",
	active: "In progress",
	ready: "Up next",
	pending: "Locked",
};

/**
 * Vertical step list: a circle before every step name shows where the
 * candidate is. Done = green tick, the current step = a spinning ring,
 * available = empty circle, not yet unlocked = faded with a lock.
 */
export default function AppSidebar({ steps }) {
	const doneCount = steps.filter((step) => step.routeState === "complete").length;
	const pct = steps.length ? Math.round((doneCount / steps.length) * 100) : 0;

	return (
		<aside className="pp-sidebar" aria-label="Interview progress">
			<div className="pp-sidebar__progress">
				<div className="pp-sidebar__progress-head">
					<span>Your progress</span>
					<strong>
						{doneCount} of {steps.length}
					</strong>
				</div>
				<div className="pp-sidebar__bar" aria-hidden="true">
					<i style={{ width: `${pct}%` }} />
				</div>
			</div>

			<ol className="pp-sidebar__steps">
				{steps.map(({ page, routeState }, index) => {
					const copy = STEP_COPY[page.key] || { label: page.label, hint: "" };
					const isLast = index === steps.length - 1;
					return (
						<li key={page.key} className={`pp-sstep pp-sstep--${routeState}${isLast ? " pp-sstep--last" : ""}`}>
							<NavLink
								to={page.path}
								className="pp-sstep__link"
								aria-current={routeState === "active" ? "step" : undefined}
							>
								<span className="pp-sstep__marker" aria-hidden="true">
									{routeState === "complete" ? (
										<Check size={14} strokeWidth={3.2} />
									) : routeState === "pending" ? (
										<Lock size={11} strokeWidth={2.6} />
									) : routeState === "active" ? (
										<span className="pp-sstep__pulse" />
									) : null}
								</span>
								<span className="pp-sstep__text">
									<strong>{copy.label}</strong>
									<small>{STATUS_TEXT[routeState] || copy.hint}</small>
								</span>
								<span className="visually-hidden">{`, ${STATUS_TEXT[routeState] || ""}`}</span>
							</NavLink>
						</li>
					);
				})}
			</ol>
		</aside>
	);
}
