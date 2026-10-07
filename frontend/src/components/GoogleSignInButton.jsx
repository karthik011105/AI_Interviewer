import { useEffect, useRef, useState } from "react";

const GIS_SRC = "https://accounts.google.com/gsi/client";
let gisPromise = null;

// Google Identity Services, loaded once and only when a client ID exists.
function loadGis() {
	if (window.google?.accounts?.id) return Promise.resolve();
	if (!gisPromise) {
		gisPromise = new Promise((resolve, reject) => {
			const script = document.createElement("script");
			script.src = GIS_SRC;
			script.async = true;
			script.onload = () => resolve();
			script.onerror = () => {
				gisPromise = null;
				reject(new Error("Could not load Google sign-in."));
			};
			document.head.appendChild(script);
		});
	}
	return gisPromise;
}

function GoogleMark() {
	return (
		<svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
			<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
			<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
			<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
			<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
		</svg>
	);
}

/**
 * "Continue with Google". Renders Google's official button when the server
 * reports a client ID; until then, a look-alike that explains it is not set up
 * rather than a button that silently does nothing.
 */
export default function GoogleSignInButton({ clientId, mode = "signin", theme = "light", onCredential, onError, disabled }) {
	const slotRef = useRef(null);
	const [ready, setReady] = useState(false);
	const [note, setNote] = useState("");
	const handlers = useRef({ onCredential, onError });
	handlers.current = { onCredential, onError };

	useEffect(() => {
		if (!clientId) return undefined;
		let cancelled = false;
		loadGis()
			.then(() => {
				if (cancelled || !slotRef.current) return;
				window.google.accounts.id.initialize({
					client_id: clientId,
					callback: (response) => handlers.current.onCredential?.(response.credential),
					ux_mode: "popup",
				});
				slotRef.current.innerHTML = "";
				window.google.accounts.id.renderButton(slotRef.current, {
					type: "standard",
					theme: theme === "dark" ? "filled_black" : "outline",
					size: "large",
					shape: "pill",
					text: mode === "signup" ? "signup_with" : "continue_with",
					width: slotRef.current.clientWidth || 320,
				});
				setReady(true);
			})
			.catch((error) => handlers.current.onError?.(error.message));
		return () => {
			cancelled = true;
		};
	}, [clientId, mode, theme]);

	if (!clientId) {
		return (
			<div className="google-signin">
				<button
					type="button"
					className="google-signin__fallback"
					disabled={disabled}
					onClick={() => setNote("Google sign-in will be available once it is set up for this site. Use email for now.")}
				>
					<GoogleMark />
					<span>Continue with Google</span>
				</button>
				{note ? <p className="google-signin__note">{note}</p> : null}
			</div>
		);
	}

	return (
		<div className="google-signin">
			<div ref={slotRef} className="google-signin__slot" aria-busy={!ready} />
		</div>
	);
}
