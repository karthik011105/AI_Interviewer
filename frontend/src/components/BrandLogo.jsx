import { useId } from "react";

/**
 * PrepForge mark: a figure (head + sweeping body) reaching up toward a star,
 * in a deep-to-light blue sweep. `useId` keeps gradient ids unique when the
 * logo appears more than once on a page.
 */
export function BrandMark({ size = 34 }) {
	const id = useId().replace(/:/g, "");
	return (
		<svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
			<defs>
				<linearGradient id={`body-${id}`} x1="0.1" y1="1" x2="0.9" y2="0">
					<stop offset="0" stopColor="#1d4ed8" />
					<stop offset="0.6" stopColor="#2f7cf6" />
					<stop offset="1" stopColor="#5fb3ff" />
				</linearGradient>
				<linearGradient id={`trail-${id}`} x1="0" y1="1" x2="1" y2="0">
					<stop offset="0" stopColor="#3b82f6" stopOpacity="0.35" />
					<stop offset="1" stopColor="#7cc4ff" stopOpacity="0.9" />
				</linearGradient>
				<radialGradient id={`head-${id}`} cx="0.35" cy="0.35" r="0.75">
					<stop offset="0" stopColor="#5aa9ff" />
					<stop offset="1" stopColor="#1d5fe0" />
				</radialGradient>
			</defs>
			{/* Lower sweep: a separate, lighter crescent under the body */}
			<path d="M4 37 C 8 30.5, 15 27.5, 23.5 28 C 15.5 30.5, 9.5 34.5, 6.5 41 C 5.5 40, 4.5 38.5, 4 37 Z" fill={`url(#trail-${id})`} />
			{/* Body: a thick swoosh from the lower left rising to the raised arm */}
			<path
				d="M8 46 C 10 34, 17.5 26, 28.5 23.5 C 34.5 22, 38.5 18.5, 41.5 12.5 C 42 22, 37 30.5, 28 33.5 C 20 36.5, 13 40.5, 8 46 Z"
				fill={`url(#body-${id})`}
			/>
			{/* Head */}
			<circle cx="26" cy="14" r="7" fill={`url(#head-${id})`} />
			{/* Star */}
			<path d="M42.5 2.5 L44 6.8 L48 8.2 L44 9.6 L42.5 14 L41 9.6 L37 8.2 L41 6.8 Z" fill="#2f7cf6" />
		</svg>
	);
}

export default function BrandLogo({ size = 34, showName = true, caption = "", className = "" }) {
	return (
		<span className={`brand-logo ${className}`}>
			<BrandMark size={size} />
			{showName ? (
				<span className="brand-logo__text">
					<span className="brand-logo__name">Prep<span className="brand-logo__forge">Forge</span></span>
					{caption ? <span className="brand-logo__caption">{caption}</span> : null}
				</span>
			) : null}
		</span>
	);
}
