import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { describeApiError } from "./authClient.js";

// Real 422 bodies captured from POST /auth/signup.
describe("describeApiError", () => {
	it("explains a too-short password instead of showing [object Object]", () => {
		const body = { detail: [{ loc: ["body", "password"], msg: "Value error, Password must be at least 8 characters long." }] };
		assert.equal(describeApiError(body, "Signup failed."), "Password must be at least 8 characters long.");
	});

	it("explains an invalid email", () => {
		const body = { detail: [{ loc: ["body", "email"], msg: "value is not a valid email address: The part after the @-sign is not valid. It should have a period." }] };
		assert.equal(describeApiError(body, "Signup failed."), "Email: The part after the @-sign is not valid. It should have a period.");
	});

	it("passes a plain string detail through", () => {
		assert.equal(describeApiError({ detail: "User with this email already exists." }, "x"), "User with this email already exists.");
	});

	it("falls back when there is no detail", () => {
		assert.equal(describeApiError({}, "Signup failed."), "Signup failed.");
		assert.equal(describeApiError(null, "Signup failed."), "Signup failed.");
	});
});
