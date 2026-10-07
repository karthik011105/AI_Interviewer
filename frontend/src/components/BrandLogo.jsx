import { useId } from "react";

/**
 * PrepPilot mark: a paper plane climbing away on a dashed flight trail, with
 * a small AI spark, on a violet-to-cyan tile. `useId` keeps gradient ids unique
 * when the logo appears more than once on a page.
 */
export function BrandMark({ size = 34 }) {
	const id = useId().replace(/:/g, "");
	return (
		<svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
			<defs>
				<linearGradient id={`tile-${id}`} x1="0" y1="0" x2="1" y2="1">
					<stop offset="0" stopColor="#6d4ff0" />
					<stop offset="0.5" stopColor="#7c5cff" />
					<stop offset="1" stopColor="#06b6d4" />
				</linearGradient>
				<linearGradient id={`plane-${id}`} x1="0" y1="0" x2="1" y2="1">
					<stop offset="0" stopColor="#ffffff" />
					<stop offset="1" stopColor="#d6f7ff" />
				</linearGradient>
				<linearGradient id={`shine-${id}`} x1="0" y1="0" x2="0" y2="1">
					<stop offset="0" stopColor="#fff" stopOpacity="0.35" />
					<stop offset="1" stopColor="#fff" stopOpacity="0" />
				</linearGradient>
			</defs>
			<rect x="1" y="1" width="46" height="46" rx="13" fill={`url(#tile-${id})`} />
			<path d="M6 16 Q6 6 16 6 H32 Q42 6 42 16 V18 Q24 26 6 18 Z" fill={`url(#shine-${id})`} />
			{/* Flight trail curving up into the plane */}
			<path d="M7 40 Q12 31 21 31.5" fill="none" stroke="#a5f3fc" strokeWidth="2.6" strokeLinecap="round" strokeDasharray="0.1 4.6" />
			{/* Paper plane: upper wing, lower wing, inner fold */}
			<path d="M40 9 L8.5 22.5 L20 27 Z" fill={`url(#plane-${id})`} />
			<path d="M40 9 L20 27 L24.5 38.5 Z" fill="#e0e7ff" />
			<path d="M40 9 L20 27 L22.3 29.6 Z" fill="#a5b4fc" />
			{/* AI spark */}
			<path d="M37 30 l1.3 3 3 1.3 -3 1.3 -1.3 3 -1.3 -3 -3 -1.3 3 -1.3 z" fill="#fff" opacity="0.9" />
		</svg>
	);
}

export default function BrandLogo({ size = 34, showName = true, className = "" }) {
	return (
		<span className={`brand-logo ${className}`}>
			<BrandMark size={size} />
			{showName ? (
				<span className="brand-logo__name">
					Prep<span className="brand-logo__accent">Pilot</span>
				</span>
			) : null}
		</span>
	);
}
