import { useId } from "react";

/**
 * The brand mark: a rounded tile in a violet-to-cyan sweep carrying a voice
 * waveform, with a small AI "spark" in the corner. `useId` keeps gradient ids
 * unique when the logo appears more than once on a page.
 */
export function BrandMark({ size = 34 }) {
	const id = useId().replace(/:/g, "");
	return (
		<svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
			<defs>
				<linearGradient id={`bg-${id}`} x1="0" y1="0" x2="1" y2="1">
					<stop offset="0" stopColor="#7c5cff" />
					<stop offset="0.55" stopColor="#5b6bff" />
					<stop offset="1" stopColor="#22d3ee" />
				</linearGradient>
				<linearGradient id={`spark-${id}`} x1="0" y1="0" x2="1" y2="1">
					<stop offset="0" stopColor="#ffffff" />
					<stop offset="1" stopColor="#c7f9ff" />
				</linearGradient>
			</defs>
			<rect x="1" y="1" width="38" height="38" rx="11" fill={`url(#bg-${id})`} />
			<rect x="1.5" y="1.5" width="37" height="37" rx="10.5" fill="none" stroke="rgba(255,255,255,0.28)" />
			{/* Voice waveform */}
			<g fill="#fff">
				<rect x="9" y="17" width="3" height="8" rx="1.5" opacity="0.75" />
				<rect x="14" y="13" width="3" height="16" rx="1.5" />
				<rect x="19" y="10" width="3" height="22" rx="1.5" />
				<rect x="24" y="15" width="3" height="12" rx="1.5" opacity="0.85" />
			</g>
			{/* AI spark */}
			<path
				d="M31 6.5l1.3 3.2 3.2 1.3-3.2 1.3L31 15.5l-1.3-3.2-3.2-1.3 3.2-1.3z"
				fill={`url(#spark-${id})`}
			/>
		</svg>
	);
}

export default function BrandLogo({ size = 34, showName = true, className = "" }) {
	return (
		<span className={`brand-logo ${className}`}>
			<BrandMark size={size} />
			{showName ? (
				<span className="brand-logo__name">Interview Simulator</span>
			) : null}
		</span>
	);
}
