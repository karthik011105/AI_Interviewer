const TIER_ORDER = ["strong", "familiar", "mentioned", "absent", "general"];

const TIER_COPY = {
	strong: "Depth checks",
	familiar: "Edge cases",
	mentioned: "Beyond the name",
	absent: "Fundamentals",
	general: "General",
};

export default function CoverageRail({ coverage }) {
	const { tierProgress = {}, remainingTargets = [], turnsUsed = 0, maxTurns = 0 } = coverage || {};
	const tiers = TIER_ORDER.filter((tier) => tierProgress[tier]);

	if (tiers.length === 0 && !maxTurns) return null;

	return (
		<aside className="coverage-rail glass-panel">
			<div className="panel-head panel-head--tight">
				<span className="section-kicker">Coverage</span>
				<h3>What is still to come</h3>
			</div>

			{maxTurns ? (
				<p className="coverage-rail__turns">
					Turn <strong>{Math.min(turnsUsed + 1, maxTurns)}</strong> of up to{" "}
					<strong>{maxTurns}</strong>
				</p>
			) : null}

			<ul className="coverage-rail__tiers">
				{tiers.map((tier) => {
					const { covered = 0, target = 0 } = tierProgress[tier] || {};
					const pct = target ? Math.round((covered / target) * 100) : 0;
					return (
						<li key={tier} className="coverage-rail__tier">
							<span className="coverage-rail__tier-head">
								<span>{TIER_COPY[tier] || tier}</span>
								<span className="coverage-rail__count">
									{covered}/{target}
								</span>
							</span>
							<span className={`coverage-rail__bar coverage-rail__bar--${tier}`}>
								<span style={{ width: `${pct}%` }} />
							</span>
						</li>
					);
				})}
			</ul>

			{remainingTargets.length > 0 ? (
				<div className="coverage-rail__remaining">
					<span className="coverage-rail__remaining-label">Still to cover</span>
					<div className="coverage-rail__chips">
						{remainingTargets.map((target) => (
							<span
								key={`${target.focus_skill}-${target.question_tier}`}
								className={`coverage-rail__chip coverage-rail__chip--${target.question_tier}`}
							>
								{target.focus_skill}
							</span>
						))}
					</div>
				</div>
			) : (
				<p className="coverage-rail__done">Every planned skill has been covered.</p>
			)}
		</aside>
	);
}
