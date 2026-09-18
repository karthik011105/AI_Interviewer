import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { resolveInterviewSocketUrl } from "./useInterviewSocket.js";

/**
 * This function decides the WebSocket scheme, and getting it wrong breaks the
 * deployed app specifically — never local development.
 *
 * A page served over HTTPS cannot open a ws:// socket: browsers block it as
 * mixed content. Local development runs on plain HTTP, where ws:// is correct
 * and the bug is invisible. The planned deployment puts the frontend on
 * Cloudflare Pages and the API on Hugging Face Spaces, both HTTPS, so this is
 * exactly the path that only fails in production.
 *
 * It is also now shared: ScriptedInterview.jsx used to carry a byte-identical
 * copy and imports this one instead, so a change here reaches both callers.
 */
describe("resolveInterviewSocketUrl", () => {
	it("upgrades to wss when the API is served over https", () => {
		const url = resolveInterviewSocketUrl(
			"https://api.example.com",
			"session-1",
			"hr",
			"token-abc",
		);

		assert.ok(url.startsWith("wss://"), `expected wss, got ${url}`);
	});

	it("uses ws for a plain http API", () => {
		const url = resolveInterviewSocketUrl(
			"http://127.0.0.1:8000",
			"session-1",
			"hr",
			"token-abc",
		);

		assert.ok(url.startsWith("ws://"), `expected ws, got ${url}`);
	});

	it("builds the interview path for the session and round", () => {
		const url = new URL(
			resolveInterviewSocketUrl(
				"https://api.example.com",
				"session-1",
				"technical",
				"token-abc",
			),
		);

		assert.equal(url.pathname, "/interview/ws/session-1/technical");
	});

	it("preserves a base path when the API is mounted under a prefix", () => {
		const url = new URL(
			resolveInterviewSocketUrl(
				"https://example.com/api",
				"session-1",
				"hr",
				"token-abc",
			),
		);

		assert.equal(url.pathname, "/api/interview/ws/session-1/hr");
	});

	it("percent-encodes the session id and round", () => {
		// A session id is server-generated, but the round comes from app state
		// and must not be able to inject extra path segments.
		const url = new URL(
			resolveInterviewSocketUrl(
				"https://api.example.com",
				"a/b",
				"c d",
				"token-abc",
			),
		);

		assert.equal(url.pathname, "/interview/ws/a%2Fb/c%20d");
	});

	it("discards any query or fragment already on the base url", () => {
		// Otherwise a stray ?foo= in VITE_API_BASE_URL would survive into the
		// socket URL alongside the token.
		const url = new URL(
			resolveInterviewSocketUrl(
				"https://api.example.com/?foo=bar#frag",
				"session-1",
				"hr",
				"token-abc",
			),
		);

		assert.equal(url.searchParams.get("foo"), null);
		assert.equal(url.hash, "");
		assert.equal(url.searchParams.get("access_token"), "token-abc");
	});

	it("falls back to the local default when no base url is given", () => {
		const url = resolveInterviewSocketUrl("", "session-1", "hr", "token-abc");

		assert.ok(url.startsWith("ws://127.0.0.1:8000/"), url);
	});
});
