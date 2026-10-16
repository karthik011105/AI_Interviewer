/**
 * How well the resume covers the chosen role's skills (Jobscan-style match
 * rate), blended with overall profile fit when the backend's pretrained models
 * are installed. Shown only after a role is picked - before that there is
 * nothing to match against.
 */
export default function RoleMatchCard({ match }) {
	if (!match) return null;
	const pct = Math.round(match.match_percent ?? 0);
	const tone = pct >= 60 ? "ok" : pct >= 35 ? "mid" : "low";

	return (
		<section className="pp-card rm" aria-label={`Match for ${match.role_title}`}>
			<header className="rm__head">
				<div>
					<h2>Match for {match.role_title}</h2>
					<p>{match.tip}</p>
				</div>
				<p className={`rm__pct rm__pct--${tone}`}>
					{pct}
					<span>%</span>
				</p>
			</header>

			{match.profile_similarity != null ? (
				<p className="rm__facts">
					Skills covered {Math.round(match.skill_coverage)}% · Profile fit {Math.round(match.profile_similarity)}%
				</p>
			) : null}

			{match.matched?.length ? (
				<div className="rm__group">
					<h3>On your resume</h3>
					<div className="rm__chips">
						{match.matched.map((skill) => (
							<span key={skill} className="rm__chip rm__chip--have">{skill}</span>
						))}
					</div>
				</div>
			) : null}

			{match.missing?.length ? (
				<div className="rm__group">
					<h3>Not found yet</h3>
					<div className="rm__chips">
						{match.missing.map((skill) => (
							<span key={skill} className="rm__chip">{skill}</span>
						))}
					</div>
				</div>
			) : null}

			{match.other_skills?.length ? (
				<div className="rm__group">
					<h3>Other skills on your resume</h3>
					<div className="rm__chips">
						{match.other_skills.map((skill) => (
							<span key={skill} className="rm__chip rm__chip--plain">{skill}</span>
						))}
					</div>
				</div>
			) : null}
		</section>
	);
}
