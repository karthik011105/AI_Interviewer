import { useId } from "react";

/**
 * Illustration: an AI interviewer on a monitor questioning a candidate across
 * a desk. Pure SVG; the robot blinks, its antenna pulses and the speech
 * bubbles float (all CSS, disabled under prefers-reduced-motion).
 */
export default function InterviewScene() {
	const id = useId().replace(/:/g, "");
	const g = (name) => `${name}-${id}`;

	return (
		<svg className="interview-scene" viewBox="0 0 560 400" role="img" aria-labelledby={g("title")}>
			<title id={g("title")}>An AI interviewer on a screen asking a candidate a question</title>
			<defs>
				<linearGradient id={g("head")} x1="0" y1="0" x2="1" y2="1">
					<stop offset="0" stopColor="#8b6cff" />
					<stop offset="1" stopColor="#22d3ee" />
				</linearGradient>
				<linearGradient id={g("screen")} x1="0" y1="0" x2="0" y2="1">
					<stop offset="0" stopColor="#1b1640" />
					<stop offset="1" stopColor="#0d0b22" />
				</linearGradient>
				<radialGradient id={g("eye")}>
					<stop offset="0" stopColor="#ffffff" />
					<stop offset="0.45" stopColor="#a5f3fc" />
					<stop offset="1" stopColor="#22d3ee" />
				</radialGradient>
				<radialGradient id={g("halo")}>
					<stop offset="0" stopColor="#8b6cff" stopOpacity="0.5" />
					<stop offset="1" stopColor="#8b6cff" stopOpacity="0" />
				</radialGradient>
				<linearGradient id={g("desk")} x1="0" y1="0" x2="0" y2="1">
					<stop offset="0" stopColor="#c9b8ff" />
					<stop offset="1" stopColor="#a48bff" />
				</linearGradient>
				<linearGradient id={g("hoodie")} x1="0" y1="0" x2="0" y2="1">
					<stop offset="0" stopColor="#ff9a76" />
					<stop offset="1" stopColor="#f0684a" />
				</linearGradient>
			</defs>

			{/* floor glow */}
			<ellipse cx="280" cy="350" rx="250" ry="34" fill="#8b6cff" opacity="0.12" />

			{/* ---------------- AI interviewer on a monitor ---------------- */}
			<circle cx="150" cy="150" r="130" fill={`url(#${g("halo")})`} className="scene-halo" />
			<rect x="58" y="62" width="190" height="150" rx="18" fill="#2a2450" />
			<rect x="66" y="70" width="174" height="134" rx="12" fill={`url(#${g("screen")})`} />
			{/* screen grid */}
			<g stroke="#8b6cff" strokeOpacity="0.12">
				<line x1="66" y1="110" x2="240" y2="110" />
				<line x1="66" y1="150" x2="240" y2="150" />
				<line x1="110" y1="70" x2="110" y2="204" />
				<line x1="196" y1="70" x2="196" y2="204" />
			</g>
			{/* antenna */}
			<line x1="153" y1="96" x2="153" y2="82" stroke="#c4b5fd" strokeWidth="3" strokeLinecap="round" />
			<circle cx="153" cy="79" r="5" fill="#67e8f9" className="scene-antenna" />
			{/* head */}
			<rect x="113" y="96" width="80" height="66" rx="22" fill={`url(#${g("head")})`} />
			<rect x="122" y="110" width="62" height="32" rx="14" fill="#0b0a1f" />
			<g className="scene-eyes">
				<ellipse cx="141" cy="126" rx="6" ry="7" fill={`url(#${g("eye")})`} />
				<ellipse cx="165" cy="126" rx="6" ry="7" fill={`url(#${g("eye")})`} />
			</g>
			<path d="M142 150 Q153 156 164 150" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" />
			{/* ears */}
			<rect x="106" y="116" width="8" height="22" rx="4" fill="#7c5cff" />
			<rect x="192" y="116" width="8" height="22" rx="4" fill="#22c4e0" />
			{/* shoulders */}
			<path d="M108 204 Q110 170 153 168 Q196 170 198 204 Z" fill="#5b4bd6" />
			<circle cx="153" cy="186" r="5" fill="#67e8f9" opacity="0.9" />
			{/* live badge */}
			<rect x="76" y="78" width="40" height="14" rx="7" fill="#e5484d" />
			<text x="96" y="88.5" textAnchor="middle" fontSize="8" fontWeight="700" fill="#fff" fontFamily="Work Sans, sans-serif">LIVE</text>
			{/* stand */}
			<rect x="140" y="212" width="26" height="40" rx="4" fill="#3b3470" />
			<rect x="112" y="248" width="82" height="10" rx="5" fill="#3b3470" />

			{/* ---------------- desk ---------------- */}
			<rect x="40" y="256" width="480" height="20" rx="10" fill={`url(#${g("desk")})`} />
			<rect x="70" y="276" width="12" height="70" rx="5" fill="#9b84f5" />
			<rect x="478" y="276" width="12" height="70" rx="5" fill="#9b84f5" />

			{/* ---------------- candidate ---------------- */}
			{/* chair back */}
			<rect x="410" y="150" width="92" height="120" rx="20" fill="#3d3a52" />
			{/* body */}
			<path d="M352 262 Q350 196 402 186 Q452 194 452 262 Z" fill={`url(#${g("hoodie")})`} />
			<path d="M392 188 Q402 206 412 188" fill="none" stroke="#c4492e" strokeWidth="3" strokeLinecap="round" />
			{/* neck + head */}
			<rect x="394" y="168" width="16" height="22" rx="6" fill="#d99a72" />
			<circle cx="402" cy="146" r="30" fill="#e9b08a" />
			{/* hair */}
			<path d="M372 142 Q370 110 402 110 Q436 110 433 140 Q424 126 404 128 Q384 128 372 142 Z" fill="#2b2233" />
			{/* face (looking left, toward the AI) */}
			<circle cx="388" cy="146" r="3" fill="#2b2233" />
			<circle cx="406" cy="146" r="3" fill="#2b2233" />
			<path d="M388 160 Q395 165 402 160" fill="none" stroke="#a45d3e" strokeWidth="2.4" strokeLinecap="round" />
			{/* headset */}
			<path d="M372 146 Q372 112 402 112 Q432 112 432 146" fill="none" stroke="#1f1b2e" strokeWidth="5" strokeLinecap="round" />
			<rect x="425" y="138" width="12" height="20" rx="5" fill="#1f1b2e" />
			<path d="M427 156 Q420 170 392 168" fill="none" stroke="#1f1b2e" strokeWidth="2.5" strokeLinecap="round" />
			<circle cx="390" cy="168" r="3.5" fill="#22d3ee" className="scene-mic" />
			{/* laptop */}
			<path d="M318 256 L332 214 H392 L382 256 Z" fill="#cbd5e1" />
			<path d="M322 252 L334 218 H387 L378 252 Z" fill="#1b1640" />
			<path d="M346 236 h22 M342 244 h18" stroke="#8b6cff" strokeWidth="3" strokeLinecap="round" />
			<rect x="300" y="254" width="96" height="6" rx="3" fill="#94a3b8" />
			{/* coffee */}
			<rect x="460" y="232" width="22" height="24" rx="5" fill="#fff" />
			<path d="M482 238 q9 4 0 12" fill="none" stroke="#fff" strokeWidth="3" />
			<path d="M466 226 q3 -6 0 -10 M474 226 q3 -6 0 -10" stroke="#cbd5e1" strokeWidth="2" strokeLinecap="round" fill="none" className="scene-steam" />

			{/* ---------------- speech ---------------- */}
			<g className="scene-bubble scene-bubble--ai">
				<rect x="214" y="24" width="196" height="54" rx="16" fill="#ffffff" />
				<path d="M232 76 l-6 14 l18 -12 z" fill="#ffffff" />
				<text x="228" y="46" fontSize="12.5" fontWeight="600" fill="#1b1640" fontFamily="Work Sans, sans-serif">Walk me through a project</text>
				<text x="228" y="64" fontSize="12.5" fontWeight="600" fill="#1b1640" fontFamily="Work Sans, sans-serif">you're proud of.</text>
			</g>
			<g className="scene-bubble scene-bubble--you">
				<rect x="300" y="92" width="76" height="32" rx="14" fill="#ffd25a" />
				<path d="M362 122 l8 12 l-2 -14 z" fill="#ffd25a" />
				<circle cx="322" cy="108" r="3.5" fill="#1b1640" className="scene-dot" />
				<circle cx="338" cy="108" r="3.5" fill="#1b1640" className="scene-dot" />
				<circle cx="354" cy="108" r="3.5" fill="#1b1640" className="scene-dot" />
			</g>

			{/* sparkles */}
			<path d="M262 120 l3 7 7 3 -7 3 -3 7 -3 -7 -7 -3 7 -3 z" fill="#67e8f9" className="scene-sparkle" />
			<path d="M40 40 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2 z" fill="#c4b5fd" className="scene-sparkle scene-sparkle--late" />
			<path d="M520 70 l2.4 5.6 5.6 2.4 -5.6 2.4 -2.4 5.6 -2.4 -5.6 -5.6 -2.4 5.6 -2.4 z" fill="#ffd25a" className="scene-sparkle" />
		</svg>
	);
}
