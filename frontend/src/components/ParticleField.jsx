import { useEffect, useRef } from "react";

const PALETTES = {
	dark: {
		dots: ["255,255,255", "167,139,250", "103,232,249"],
		spotlight: "124,92,255",
		spotlightAlpha: 0.22,
		link: "167,139,250",
	},
	light: {
		dots: ["109,79,240", "124,92,255", "14,165,233", "236,72,153"],
		spotlight: "124,92,255",
		spotlightAlpha: 0.2,
		link: "109,79,240",
		alphaBoost: 0.25,
	},
};

const CURSOR_RADIUS = 170;

/**
 * Floating bubbles plus a soft light that follows the cursor. Bubbles near the
 * cursor brighten, drift toward it and get faint links to it.
 *
 * `contained` fills the parent instead of the viewport (used by the sign-in
 * panel). Pointer-events are off, so it never blocks clicks; it stops when the
 * tab is hidden and renders a still frame under prefers-reduced-motion.
 */
export default function ParticleField({ theme = "light", contained = false, density = 1 }) {
	const canvasRef = useRef(null);

	useEffect(() => {
		const canvas = canvasRef.current;
		if (!canvas) return undefined;
		const ctx = canvas.getContext("2d");
		const palette = PALETTES[theme === "dark" ? "dark" : "light"];
		const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

		let width = 0;
		let height = 0;
		let particles = [];
		let frame = 0;
		const pointer = { x: -9999, y: -9999, active: false };
		const glow = { x: -9999, y: -9999 };

		function resize() {
			const rect = contained ? canvas.parentElement.getBoundingClientRect() : { width: window.innerWidth, height: window.innerHeight };
			const dpr = Math.min(window.devicePixelRatio || 1, 2);
			width = rect.width;
			height = rect.height;
			canvas.width = Math.round(width * dpr);
			canvas.height = Math.round(height * dpr);
			canvas.style.width = `${width}px`;
			canvas.style.height = `${height}px`;
			ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

			const count = Math.min(130, Math.round(((width * height) / 15000) * density));
			particles = Array.from({ length: count }, () => ({
				x: Math.random() * width,
				y: Math.random() * height,
				r: 0.8 + Math.random() * 2.4,
				vx: (Math.random() - 0.5) * 0.18,
				vy: -0.08 - Math.random() * 0.22,
				a: 0.25 + Math.random() * 0.45 + (palette.alphaBoost || 0),
				color: palette.dots[Math.floor(Math.random() * palette.dots.length)],
				phase: Math.random() * Math.PI * 2,
			}));
		}

		function toLocal(event) {
			if (!contained) return { x: event.clientX, y: event.clientY };
			const rect = canvas.getBoundingClientRect();
			return { x: event.clientX - rect.left, y: event.clientY - rect.top };
		}

		function onMove(event) {
			const p = toLocal(event);
			pointer.x = p.x;
			pointer.y = p.y;
			pointer.active = p.x >= 0 && p.y >= 0 && p.x <= width && p.y <= height;
			if (reduceMotion) draw(0);
		}

		function onLeave() {
			pointer.active = false;
		}

		function draw(time) {
			ctx.clearRect(0, 0, width, height);

			// The light around the cursor eases after it rather than snapping.
			if (pointer.active) {
				glow.x += (pointer.x - glow.x) * 0.12;
				glow.y += (pointer.y - glow.y) * 0.12;
				if (glow.x < -1000) {
					glow.x = pointer.x;
					glow.y = pointer.y;
				}
				const light = ctx.createRadialGradient(glow.x, glow.y, 0, glow.x, glow.y, 320);
				light.addColorStop(0, `rgba(${palette.spotlight},${palette.spotlightAlpha})`);
				light.addColorStop(1, `rgba(${palette.spotlight},0)`);
				ctx.fillStyle = light;
				ctx.fillRect(0, 0, width, height);
			}

			for (const p of particles) {
				if (!reduceMotion) {
					p.x += p.vx + Math.sin(time / 2400 + p.phase) * 0.08;
					p.y += p.vy;
					if (p.y < -10) {
						p.y = height + 10;
						p.x = Math.random() * width;
					}
					if (p.x < -10) p.x = width + 10;
					if (p.x > width + 10) p.x = -10;
				}

				let alpha = p.a;
				let radius = p.r;
				if (pointer.active) {
					const dx = pointer.x - p.x;
					const dy = pointer.y - p.y;
					const dist = Math.hypot(dx, dy);
					if (dist < CURSOR_RADIUS) {
						const pull = 1 - dist / CURSOR_RADIUS;
						if (!reduceMotion) {
							p.x += dx * 0.004 * pull;
							p.y += dy * 0.004 * pull;
						}
						alpha = Math.min(1, alpha + pull * 0.6);
						radius += pull * 1.2;
						ctx.strokeStyle = `rgba(${palette.link},${pull * 0.35})`;
						ctx.lineWidth = 0.7;
						ctx.beginPath();
						ctx.moveTo(p.x, p.y);
						ctx.lineTo(pointer.x, pointer.y);
						ctx.stroke();
					}
				}

				ctx.beginPath();
				ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
				ctx.fillStyle = `rgba(${p.color},${alpha})`;
				ctx.fill();
			}
		}

		function loop(time) {
			draw(time);
			frame = requestAnimationFrame(loop);
		}

		function onVisibility() {
			cancelAnimationFrame(frame);
			if (!document.hidden && !reduceMotion) frame = requestAnimationFrame(loop);
		}

		resize();
		const observer = new ResizeObserver(resize);
		observer.observe(contained ? canvas.parentElement : document.documentElement);
		window.addEventListener("pointermove", onMove, { passive: true });
		document.addEventListener("pointerleave", onLeave);
		document.addEventListener("visibilitychange", onVisibility);
		if (reduceMotion) draw(0);
		else frame = requestAnimationFrame(loop);

		return () => {
			cancelAnimationFrame(frame);
			observer.disconnect();
			window.removeEventListener("pointermove", onMove);
			document.removeEventListener("pointerleave", onLeave);
			document.removeEventListener("visibilitychange", onVisibility);
		};
	}, [theme, contained, density]);

	return <canvas ref={canvasRef} className={`particle-field${contained ? " particle-field--contained" : ""}`} aria-hidden="true" />;
}
