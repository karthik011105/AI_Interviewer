import { useId } from "react";

/*
 * Illustrated, "3D" feature icons: a glossy tile with depth, a soft highlight
 * and a raised glyph, rather than flat line icons. Gradient ids go through
 * useId so several icons can share a page.
 */

function Tile({ id, from, to, children }) {
	return (
		<svg viewBox="0 0 64 64" width="68" height="68" aria-hidden="true">
			<defs>
				<linearGradient id={`tile-${id}`} x1="0" y1="0" x2="0.9" y2="1">
					<stop offset="0" stopColor={from} />
					<stop offset="1" stopColor={to} />
				</linearGradient>
				<linearGradient id={`shine-${id}`} x1="0" y1="0" x2="0" y2="1">
					<stop offset="0" stopColor="#fff" stopOpacity="0.55" />
					<stop offset="1" stopColor="#fff" stopOpacity="0" />
				</linearGradient>
				<filter id={`drop-${id}`} x="-30%" y="-30%" width="160%" height="170%">
					<feDropShadow dx="0" dy="4" stdDeviation="3.5" floodColor={to} floodOpacity="0.45" />
				</filter>
				<filter id={`glyph-${id}`} x="-30%" y="-30%" width="160%" height="160%">
					<feDropShadow dx="0" dy="1.5" stdDeviation="1.2" floodColor="#000" floodOpacity="0.28" />
				</filter>
			</defs>
			<g filter={`url(#drop-${id})`}>
				<rect x="4" y="4" width="56" height="56" rx="16" fill={`url(#tile-${id})`} />
			</g>
			<path d="M10 20 Q10 8 22 8 H42 Q54 8 54 20 V24 Q32 34 10 24 Z" fill={`url(#shine-${id})`} />
			<rect x="4.5" y="4.5" width="55" height="55" rx="15.5" fill="none" stroke="#fff" strokeOpacity="0.35" />
			<g filter={`url(#glyph-${id})`}>{children}</g>
		</svg>
	);
}

export function ResumeIcon() {
	const id = useId().replace(/:/g, "");
	return (
		<Tile id={id} from="#ffd56b" to="#f59e0b">
			<path d="M21 14 H37 L45 22 V48 Q45 50 43 50 H21 Q19 50 19 48 V16 Q19 14 21 14 Z" fill="#fff" />
			<path d="M37 14 V20 Q37 22 39 22 H45 Z" fill="#fde7b0" />
			<circle cx="27" cy="27" r="3.4" fill="#f59e0b" />
			<rect x="33" y="24.5" width="8" height="2.4" rx="1.2" fill="#fbbf24" />
			<rect x="33" y="28.5" width="5" height="2" rx="1" fill="#fcd34d" />
			<rect x="24" y="35" width="17" height="2.4" rx="1.2" fill="#e5e7eb" />
			<rect x="24" y="39.5" width="14" height="2.4" rx="1.2" fill="#e5e7eb" />
			<rect x="24" y="44" width="10" height="2.4" rx="1.2" fill="#e5e7eb" />
			<path d="M47 34 l1.6 3.8 3.8 1.6 -3.8 1.6 -1.6 3.8 -1.6 -3.8 -3.8 -1.6 3.8 -1.6 z" fill="#fff" />
		</Tile>
	);
}

export function MicIcon() {
	const id = useId().replace(/:/g, "");
	return (
		<Tile id={id} from="#7cc4ff" to="#2563eb">
			<defs>
				<linearGradient id={`metal-${id}`} x1="0" y1="0" x2="1" y2="0">
					<stop offset="0" stopColor="#e2e8f0" />
					<stop offset="0.45" stopColor="#ffffff" />
					<stop offset="1" stopColor="#cbd5e1" />
				</linearGradient>
			</defs>
			<rect x="25" y="12" width="14" height="24" rx="7" fill={`url(#metal-${id})`} />
			<g stroke="#94a3b8" strokeWidth="1" opacity="0.7">
				<line x1="27" y1="18" x2="37" y2="18" />
				<line x1="26" y1="22" x2="38" y2="22" />
				<line x1="26" y1="26" x2="38" y2="26" />
				<line x1="27" y1="30" x2="37" y2="30" />
			</g>
			<path d="M20 30 Q20 42 32 42 Q44 42 44 30" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
			<rect x="30.5" y="42" width="3" height="6" fill="#fff" />
			<rect x="24" y="47.5" width="16" height="3.5" rx="1.75" fill="#fff" />
		</Tile>
	);
}

export function GrowthIcon() {
	const id = useId().replace(/:/g, "");
	return (
		<Tile id={id} from="#6ee7b7" to="#059669">
			<rect x="15" y="34" width="7" height="14" rx="2" fill="#ecfdf5" />
			<rect x="25" y="27" width="7" height="21" rx="2" fill="#ffffff" />
			<rect x="35" y="20" width="7" height="28" rx="2" fill="#ffffff" />
			<path d="M15 28 L25 21 L32 25 L46 13" fill="none" stroke="#fde047" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
			<path d="M40 12 H47 V19" fill="none" stroke="#fde047" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
			<circle cx="46" cy="42" r="7" fill="#fff" />
			<path d="M42.8 42 l2.2 2.2 4.2-4.4" fill="none" stroke="#059669" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
		</Tile>
	);
}
