import { AlertCircle, CheckCircle2 } from "lucide-react";

const FIX_BELOW = 55;

function verdictFor(score, fixCount) {
	const lead =
		score >= 80 ? "Strong resume." : score >= 60 ? "Good foundation." : score >= 40 ? "A fair start." : "Needs work before you apply.";
	if (!fixCount) return `${lead} Only small polish left.`;
	return `${lead} ${fixCount === 1 ? "One thing" : `${fixCount} things`} would lift it most.`;
}

/**
 * Resume score: the number, a one-line verdict, and a checklist ordered so
 * the weakest area comes first. Deliberately plain - no "AI powered" badge,
 * emoji, pass/fail pills or count-up animation.
 */
export default function ResumeScoreCard({ score }) {
	if (!score) return null;

	const { action_verbs, quantification, keyword_density, section_completeness } = score;
	const overall = Math.round(score.overall_score ?? 0);

	const items = [
		{ key: "numbers", label: "Numbers and impact", pct: quantification?.score, tip: quantification?.tip },
		{ key: "keywords", label: "Role keywords", pct: keyword_density?.score, tip: keyword_density?.tip },
		{ key: "verbs", label: "Action verbs", pct: action_verbs?.score, tip: action_verbs?.tip },
		{ key: "sections", label: "Sections", pct: section_completeness?.score, tip: section_completeness?.tip },
	]
		.map((item) => ({ ...item, pct: Math.round((item.pct ?? 0) * 100) }))
		.sort((a, b) => a.pct - b.pct);

	const fixCount = items.filter((item) => item.pct < FIX_BELOW).length;

	const facts = [
		keyword_density?.keywords_total != null
			? `${keyword_density.keywords_matched ?? 0} of ${keyword_density.keywords_total} role keywords`
			: null,
		action_verbs?.strong_count != null ? `${action_verbs.strong_count} strong verbs` : null,
		quantification?.quantified_bullets != null
			? `${quantification.quantified_bullets} ${quantification.quantified_bullets === 1 ? "bullet" : "bullets"} with numbers`
			: null,
	].filter(Boolean);

	return (
		<section className="rs pp-card" aria-label="Resume score">
			<header className="rs__head">
				<p className="rs__score">
					<span className="rs__num">{overall}</span>
					<span className="rs__of">/100</span>
				</p>
				<div>
					<h2>Resume score</h2>
					<p className="rs__verdict">{verdictFor(overall, fixCount)}</p>
				</div>
			</header>

			<ol className="rs__list">
				{items.map((item) => {
					const fix = item.pct < FIX_BELOW;
					return (
						<li key={item.key} className={`rs__item ${fix ? "rs__item--fix" : "rs__item--ok"}`}>
							<span className="rs__mark" aria-hidden="true">
								{fix ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}
							</span>
							<div className="rs__body">
								<div className="rs__row">
									<strong>{item.label}</strong>
									<span className="rs__pct">{item.pct}%</span>
								</div>
								<span className="rs__bar" aria-hidden="true">
									<i style={{ width: `${item.pct}%` }} />
								</span>
								{item.tip ? <p className="rs__tip">{item.tip}</p> : null}
							</div>
						</li>
					);
				})}
			</ol>

			{facts.length ? <p className="rs__facts">{facts.join(" · ")}</p> : null}
		</section>
	);
}
