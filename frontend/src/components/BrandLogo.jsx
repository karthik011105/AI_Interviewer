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
			{/* Lower sweep: the motion trail */}
			<path d="M6 41 C 10 33, 16 28.5, 24 27.5 C 17 30.5, 11.5 35, 8.5 42.5 Z" fill={`url(#trail-${id})`} />
			{/* Body sweeping up into the reaching arm */}
			<path
				d="M9 45 C 12 33, 19 25.5, 28 24 C 34 23, 38.5 19.5, 41 13.5 C 40.5 21.5, 36 28.5, 28.5 31 C 21 33.5, 14.5 38, 9 45 Z"
				fill={`url(#body-${id})`}
			/>
			{/* Head */}
			<circle cx="26" cy="15.5" r="6" fill={`url(#head-${id})`} />
			{/* Star */}
			<path d="M42 3.5 L43.4 7.6 L47.5 9 L43.4 10.4 L42 14.5 L40.6 10.4 L36.5 9 L40.6 7.6 Z" fill="#2f7cf6" />
		</svg>
	);
}

export default function BrandLogo({ size = 34, showName = true, caption = "", className = "" }) {
	return (
		<span className={`brand-logo ${className}`}>
			<BrandMark size={size} />
			{showName ? (
				<span className="brand-logo__text">
					<span className="brand-logo__name">PrepForge</span>
					{caption ? <span className="brand-logo__caption">{caption}</span> : null}
				</span>
			) : null}
		</span>
	);
}
