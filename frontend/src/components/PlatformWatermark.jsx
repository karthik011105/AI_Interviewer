import { useId } from "react";

/**
 * The "P" mark with an arrow cut through it, rising to the upper right. Drawn
 * as SVG so it stays crisp at watermark size. The arrow is a mask, so it is a
 * genuine see-through cut rather than a white shape painted on top.
 */
export function PArrowMark({ size = 120, className = "" }) {
	const id = useId().replace(/:/g, "");
	return (
		<svg className={className} width={size} height={size * 1.15} viewBox="0 0 100 115" aria-hidden="true">
			<defs>
				<linearGradient id={`p-${id}`} x1="0.2" y1="0" x2="0.5" y2="1">
					<stop offset="0" stopColor="#2a1f6e" />
					<stop offset="0.55" stopColor="#4c2fc4" />
					<stop offset="1" stopColor="#8b5cf6" />
				</linearGradient>
				<mask id={`cut-${id}`}>
					<rect width="100" height="115" fill="#fff" />
					{/* Arrow shaft + head, cut out of the P */}
					<path
						d="M24 86 L56 46 L48.5 46 L48.5 38.5 L68 38.5 L68 58 L60.5 58 L60.5 50.5 L29.5 90.5 Z"
						fill="#000"
					/>
				</mask>
			</defs>
			<path
				d="M14 6 H56 C76 6 90 20 90 39 C90 58 76 72 56 72 H40 V109 L14 96 Z"
				fill={`url(#p-${id})`}
				mask={`url(#cut-${id})`}
			/>
		</svg>
	);
}

/** Large, faint mark centred behind the signed-in app. */
export default function PlatformWatermark() {
	return (
		<div className="pp-watermark" aria-hidden="true">
			<PArrowMark size={460} />
		</div>
	);
}
