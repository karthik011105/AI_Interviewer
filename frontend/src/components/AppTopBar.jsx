import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { LogOut, Moon, Sun } from "lucide-react";

import BrandLogo from "./BrandLogo";

const SHORT_LABELS = {
	upload: "Resume",
	assessment: "Assessment",
	technical: "Technical",
	dsa: "Coding",
	project_discussion: "Projects",
	hr: "HR",
	report: "Report",
};

/**
 * The signed-in app's only chrome: logo, a compact step tracker, the theme
 * toggle and an account menu. Replaces the old sidebar + "current stage"
 * toolbar, which repeated the same status three times over.
 */
export default function AppTopBar({ theme, onToggleTheme, user, onSignOut }) {
	const [menuOpen, setMenuOpen] = useState(false);
	const menuRef = useRef(null);
	const email = String(user?.email || "");
	const initial = (email.trim()[0] || "?").toUpperCase();
	// A calm, consistent tint per person (the same email always gets the same
	// hue) instead of a glowing gradient - the way Notion or Linear do it.
	const hue = [...email.toLowerCase()].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 360, 7);

	useEffect(() => {
		if (!menuOpen) return undefined;
		function onDown(event) {
			if (!menuRef.current?.contains(event.target)) setMenuOpen(false);
		}
		function onKey(event) {
			if (event.key === "Escape") setMenuOpen(false);
		}
		document.addEventListener("mousedown", onDown);
		document.addEventListener("keydown", onKey);
		return () => {
			document.removeEventListener("mousedown", onDown);
			document.removeEventListener("keydown", onKey);
		};
	}, [menuOpen]);

	return (
		<header className="pp-topbar">
			<NavLink to="/" className="pp-topbar__brand" aria-label="PrepForge home">
				<BrandLogo size={34} />
			</NavLink>


			<div className="pp-topbar__actions">
				<button
					type="button"
					className="pp-icon-btn"
					onClick={onToggleTheme}
					aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
				>
					{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
				</button>
				<div className="pp-account" ref={menuRef}>
					<button
						type="button"
						className="pp-avatar"
						style={{ "--avatar-hue": hue }}
						onClick={() => setMenuOpen((open) => !open)}
						aria-haspopup="menu"
						aria-expanded={menuOpen}
						aria-label="Account"
					>
						{initial}
					</button>
					{menuOpen ? (
						<div className="pp-account__menu" role="menu">
							<p className="pp-account__email" title={email}>{email}</p>
							<button type="button" role="menuitem" className="pp-account__item" onClick={onSignOut}>
								<LogOut size={16} /> Sign out
							</button>
						</div>
					) : null}
				</div>
			</div>
		</header>
	);
}
