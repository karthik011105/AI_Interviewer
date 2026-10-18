import React from "react";

/**
 * Catches render-time exceptions so one broken component cannot blank the app.
 *
 * React unmounts the entire tree when a render throws and nothing catches it.
 * In a single-page app that means a white screen with no explanation — and this
 * application had no boundary anywhere across ~13,600 lines of frontend. The
 * worst moment for that is mid-interview: a candidate answering questions loses
 * the whole page, with no indication of whether their answers were saved.
 *
 * This deliberately does NOT try to recover the failed subtree. Re-rendering a
 * component that just threw usually throws again, and a boundary that loops is
 * worse than one that stops. It reports, it preserves the information the user
 * needs in order to act, and it offers the two escapes that actually work:
 * reload, or go back to the start.
 *
 * `onError` lets a caller forward the exception somewhere durable. Nothing is
 * wired to it yet; it exists so that when error tracking is switched on in
 * phase 2.8, the frontend has a hook rather than needing a refactor.
 */
export default class ErrorBoundary extends React.Component {
	constructor(props) {
		super(props);
		this.state = { error: null };
	}

	static getDerivedStateFromError(error) {
		return { error };
	}

	componentDidCatch(error, errorInfo) {
		// Always log, even in production. A boundary that swallows the
		// exception silently converts a visible crash into an invisible one,
		// which is harder to diagnose, not easier.
		// eslint-disable-next-line no-console
		console.error("Unhandled render error:", error, errorInfo?.componentStack);
		this.props.onError?.(error, errorInfo);
	}

	render() {
		const { error } = this.state;
		if (!error) {
			return this.props.children;
		}

		const { label } = this.props;

		return (
			<div
				role="alert"
				style={{
					margin: "2rem auto",
					maxWidth: "36rem",
					padding: "1.5rem",
					background: "var(--surface)",
					border: "1px solid var(--border)",
					borderRadius: "var(--radius-lg)",
					boxShadow: "var(--shadow)",
					color: "var(--text)",
				}}
			>
				<h2 style={{ margin: "0 0 0.5rem", fontSize: "1.125rem" }}>
					Something broke on this page
				</h2>
				<p style={{ margin: "0 0 1rem", color: "var(--muted)", lineHeight: 1.5 }}>
					{label
						? `The ${label} failed to display. `
						: "This part of the app failed to display. "}
					Your saved progress is stored on the server, so reloading should bring
					you back to where you were.
				</p>

				<div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
					<button
						type="button"
						onClick={() => window.location.reload()}
						style={{
							padding: "0.5rem 0.9rem",
							borderRadius: "var(--radius-sm)",
							border: "1px solid transparent",
							background: "var(--accent)",
							color: "var(--accent-ink)",
							cursor: "pointer",
							font: "inherit",
						}}
					>
						Reload the page
					</button>
					<button
						type="button"
						onClick={() => {
							window.location.href = "/";
						}}
						style={{
							padding: "0.5rem 0.9rem",
							borderRadius: "var(--radius-sm)",
							border: "1px solid var(--border-strong)",
							background: "var(--surface)",
							color: "var(--text)",
							cursor: "pointer",
							font: "inherit",
						}}
					>
						Back to start
					</button>
				</div>

				{/*
				  The message is shown, not hidden behind "contact support". It is
				  what a user pastes into a bug report, and it is already in the
				  browser console regardless — hiding it helps nobody.
				*/}
				<details style={{ marginTop: "1rem" }}>
					<summary style={{ cursor: "pointer", color: "var(--muted)" }}>
						Technical details
					</summary>
					<pre
						style={{
							marginTop: "0.5rem",
							padding: "0.75rem",
							overflowX: "auto",
							background: "var(--surface-sunken)",
							border: "1px solid var(--border)",
							borderRadius: "var(--radius-sm)",
							fontSize: "0.8125rem",
							whiteSpace: "pre-wrap",
							wordBreak: "break-word",
						}}
					>
						{String(error?.message || error)}
					</pre>
				</details>
			</div>
		);
	}
}
