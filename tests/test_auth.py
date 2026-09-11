"""Authentication, authorization, and abuse-protection regression tests.

This covers the security-critical path that previously had no coverage at all:
password policy, signup/sign-in behaviour, token verification, session-ownership
enforcement, and the rate-limit / lockout primitives.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import MagicMock, patch

import bcrypt
import jwt
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from backend import config
from backend.api import routes_auth
from backend.api.auth import (
	AuthenticatedUser,
	authenticate_access_token,
	ensure_session_access,
	resolve_authenticated_user_id,
)
from backend.api.rate_limit import (
	FailureTracker,
	FixedWindowRateLimiter,
	RateLimitExceeded,
)

# At least 32 bytes: PyJWT emits InsecureKeyLengthWarning below that for HS256,
# which would otherwise add noise to every CI run.
_TEST_SECRET = "unit-test-signing-secret-padded-to-32-bytes-minimum"


def _make_request(host: str = "203.0.113.10") -> MagicMock:
	request = MagicMock()
	request.client.host = host
	return request


class _AuthTestBase(TestCase):
	"""Pins auth settings so tests do not depend on the developer's .env."""

	env_overrides: dict[str, str] = {}

	def setUp(self) -> None:
		overrides = {
			"AUTH_JWT_SECRET": _TEST_SECRET,
			"AUTH_JWT_ALGORITHM": "HS256",
			"AUTH_JWT_EXPIRY_HOURS": "24",
			"AUTH_PASSWORD_MIN_LENGTH": "8",
			"AUTH_PASSWORD_MAX_BYTES": "72",
			"AUTH_RATE_LIMIT_MAX_ATTEMPTS": "10",
			"AUTH_RATE_LIMIT_WINDOW_SECONDS": "60",
			"AUTH_LOGIN_MAX_FAILURES": "5",
			"AUTH_LOGIN_LOCKOUT_SECONDS": "300",
			**self.env_overrides,
		}
		self._env_patcher = patch.dict(os.environ, overrides, clear=False)
		self._env_patcher.start()
		config.reset_settings()
		routes_auth.reset_auth_throttles()

	def tearDown(self) -> None:
		self._env_patcher.stop()
		config.reset_settings()
		routes_auth.reset_auth_throttles()


class PasswordPolicyTests(_AuthTestBase):
	def test_rejects_password_below_minimum_length(self) -> None:
		with self.assertRaises(ValidationError) as context:
			routes_auth.SignupRequest(email="user@example.com", password="short1")

		self.assertIn("at least 8 characters", str(context.exception))

	def test_accepts_password_at_minimum_length(self) -> None:
		request = routes_auth.SignupRequest(email="user@example.com", password="exactly8")
		self.assertEqual(request.password, "exactly8")

	def test_rejects_password_exceeding_bcrypt_byte_limit(self) -> None:
		with self.assertRaises(ValidationError) as context:
			routes_auth.SignupRequest(email="user@example.com", password="a" * 73)

		self.assertIn("72 bytes", str(context.exception))

	def test_rejects_multibyte_password_over_byte_limit_despite_short_length(self) -> None:
		"""A 30-character emoji password is 120 bytes and bcrypt would raise on it.

		The policy measures encoded bytes precisely so this fails validation with
		a 422 instead of blowing up inside bcrypt as a 500.
		"""
		emoji_password = "\U0001F600" * 30
		self.assertLess(len(emoji_password), 72)
		self.assertGreater(len(emoji_password.encode("utf-8")), 72)

		with self.assertRaises(ValidationError):
			routes_auth.SignupRequest(email="user@example.com", password=emoji_password)

	def test_login_does_not_apply_the_signup_policy(self) -> None:
		"""Sign-in must accept short passwords so pre-policy accounts still work."""
		request = routes_auth.LoginRequest(email="user@example.com", password="old")
		self.assertEqual(request.password, "old")


class SignupTests(_AuthTestBase):
	def test_signup_creates_user_and_returns_token(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None
		repo.insert_one.return_value = {"id": "user-1", "email": "user@example.com"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			result = routes_auth.signup(
				routes_auth.SignupRequest(email="user@example.com", password="password123"),
				_make_request(),
			)

		payload = jwt.decode(result["access_token"], _TEST_SECRET, algorithms=["HS256"])
		self.assertEqual(payload["sub"], "user-1")
		self.assertEqual(payload["email"], "user@example.com")

	def test_signup_stores_a_bcrypt_hash_never_the_raw_password(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None
		repo.insert_one.return_value = {"id": "user-1", "email": "user@example.com"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			routes_auth.signup(
				routes_auth.SignupRequest(email="user@example.com", password="password123"),
				_make_request(),
			)

		_, stored = repo.insert_one.call_args.args
		self.assertNotIn("password", stored)
		self.assertNotEqual(stored["password_hash"], "password123")
		self.assertTrue(stored["password_hash"].startswith("$2b$"))
		self.assertTrue(
			bcrypt.checkpw(b"password123", stored["password_hash"].encode("utf-8"))
		)

	def test_signup_normalizes_email_case(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None
		repo.insert_one.return_value = {"id": "user-1", "email": "user@example.com"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			routes_auth.signup(
				routes_auth.SignupRequest(email="User@Example.COM", password="password123"),
				_make_request(),
			)

		repo.db.users.find_one.assert_called_once_with({"email": "user@example.com"})
		_, stored = repo.insert_one.call_args.args
		self.assertEqual(stored["email"], "user@example.com")

	def test_signup_rejects_an_existing_email(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = {"_id": "user-1"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			with self.assertRaises(HTTPException) as context:
				routes_auth.signup(
					routes_auth.SignupRequest(email="user@example.com", password="password123"),
					_make_request(),
				)

		self.assertEqual(context.exception.status_code, 409)
		repo.insert_one.assert_not_called()

	def test_signup_race_surfaces_duplicate_key_as_409_not_500(self) -> None:
		"""Two concurrent signups can both pass the existence check.

		The loser hits the unique index on users.email. That must be reported as
		the same 409 conflict rather than escaping as an unhandled 500.
		"""
		repo = MagicMock()
		repo.db.users.find_one.return_value = None
		repo.insert_one.side_effect = DuplicateKeyError("E11000 duplicate key error")

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			with self.assertRaises(HTTPException) as context:
				routes_auth.signup(
					routes_auth.SignupRequest(email="user@example.com", password="password123"),
					_make_request(),
				)

		self.assertEqual(context.exception.status_code, 409)
		self.assertIn("already exists", context.exception.detail)


class LoginTests(_AuthTestBase):
	def _repo_with_user(self, password: str = "password123") -> MagicMock:
		hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
		repo = MagicMock()
		repo.db.users.find_one.return_value = {
			"_id": "user-1",
			"email": "user@example.com",
			"password_hash": hashed,
		}
		return repo

	def test_login_succeeds_with_correct_password(self) -> None:
		with patch("backend.api.routes_auth.get_repository", return_value=self._repo_with_user()):
			result = routes_auth.login(
				routes_auth.LoginRequest(email="user@example.com", password="password123"),
				_make_request(),
			)

		payload = jwt.decode(result["access_token"], _TEST_SECRET, algorithms=["HS256"])
		self.assertEqual(payload["sub"], "user-1")

	def test_login_rejects_wrong_password(self) -> None:
		with patch("backend.api.routes_auth.get_repository", return_value=self._repo_with_user()):
			with self.assertRaises(HTTPException) as context:
				routes_auth.login(
					routes_auth.LoginRequest(email="user@example.com", password="wrong-password"),
					_make_request(),
				)

		self.assertEqual(context.exception.status_code, 401)

	def test_unknown_email_and_wrong_password_are_indistinguishable(self) -> None:
		"""Neither status code nor detail may reveal whether an account exists."""
		repo_missing = MagicMock()
		repo_missing.db.users.find_one.return_value = None

		with patch("backend.api.routes_auth.get_repository", return_value=repo_missing):
			with self.assertRaises(HTTPException) as missing:
				routes_auth.login(
					routes_auth.LoginRequest(email="nobody@example.com", password="password123"),
					_make_request(),
				)

		routes_auth.reset_auth_throttles()

		with patch("backend.api.routes_auth.get_repository", return_value=self._repo_with_user()):
			with self.assertRaises(HTTPException) as wrong:
				routes_auth.login(
					routes_auth.LoginRequest(email="user@example.com", password="wrong-password"),
					_make_request(),
				)

		self.assertEqual(missing.exception.status_code, wrong.exception.status_code)
		self.assertEqual(missing.exception.detail, wrong.exception.detail)

	def test_login_survives_a_corrupt_stored_hash(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = {
			"_id": "user-1",
			"email": "user@example.com",
			"password_hash": "not-a-bcrypt-hash",
		}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			with self.assertRaises(HTTPException) as context:
				routes_auth.login(
					routes_auth.LoginRequest(email="user@example.com", password="password123"),
					_make_request(),
				)

		self.assertEqual(context.exception.status_code, 401)

	def test_login_survives_an_oversized_password_without_500(self) -> None:
		"""bcrypt raises above 72 bytes; sign-in must treat that as a bad credential."""
		with patch("backend.api.routes_auth.get_repository", return_value=self._repo_with_user()):
			with self.assertRaises(HTTPException) as context:
				routes_auth.login(
					routes_auth.LoginRequest(email="user@example.com", password="a" * 200),
					_make_request(),
				)

		self.assertEqual(context.exception.status_code, 401)

	def test_login_is_case_insensitive_on_email(self) -> None:
		repo = self._repo_with_user()

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			routes_auth.login(
				routes_auth.LoginRequest(email="USER@example.com", password="password123"),
				_make_request(),
			)

		repo.db.users.find_one.assert_called_once_with({"email": "user@example.com"})


class LoginLockoutTests(_AuthTestBase):
	env_overrides = {"AUTH_LOGIN_MAX_FAILURES": "3", "AUTH_RATE_LIMIT_MAX_ATTEMPTS": "100"}

	def test_repeated_failures_lock_the_account_out_with_retry_after(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			for _ in range(3):
				with self.assertRaises(HTTPException) as context:
					routes_auth.login(
						routes_auth.LoginRequest(email="user@example.com", password="password123"),
						_make_request(),
					)
				self.assertEqual(context.exception.status_code, 401)

			with self.assertRaises(HTTPException) as locked:
				routes_auth.login(
					routes_auth.LoginRequest(email="user@example.com", password="password123"),
					_make_request(),
				)

		self.assertEqual(locked.exception.status_code, 429)
		self.assertIn("Retry-After", locked.exception.headers)

	def test_lockout_follows_the_account_not_the_client_address(self) -> None:
		"""Rotating source IPs must not reset an account's failure streak."""
		repo = MagicMock()
		repo.db.users.find_one.return_value = None

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			for index in range(3):
				with self.assertRaises(HTTPException):
					routes_auth.login(
						routes_auth.LoginRequest(email="user@example.com", password="password123"),
						_make_request(host=f"198.51.100.{index}"),
					)

			with self.assertRaises(HTTPException) as locked:
				routes_auth.login(
					routes_auth.LoginRequest(email="user@example.com", password="password123"),
					_make_request(host="198.51.100.99"),
				)

		self.assertEqual(locked.exception.status_code, 429)

	def test_successful_login_clears_the_failure_streak(self) -> None:
		hashed = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode("utf-8")
		repo = MagicMock()
		repo.db.users.find_one.return_value = {
			"_id": "user-1",
			"email": "user@example.com",
			"password_hash": hashed,
		}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			for _ in range(2):
				with self.assertRaises(HTTPException):
					routes_auth.login(
						routes_auth.LoginRequest(email="user@example.com", password="wrong"),
						_make_request(),
					)

			routes_auth.login(
				routes_auth.LoginRequest(email="user@example.com", password="password123"),
				_make_request(),
			)

			# The streak is cleared, so the next two failures must not lock out.
			for _ in range(2):
				with self.assertRaises(HTTPException) as context:
					routes_auth.login(
						routes_auth.LoginRequest(email="user@example.com", password="wrong"),
						_make_request(),
					)
				self.assertEqual(context.exception.status_code, 401)


class IpRateLimitTests(_AuthTestBase):
	env_overrides = {"AUTH_RATE_LIMIT_MAX_ATTEMPTS": "3"}

	def test_signup_is_rate_limited_per_client_address(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None
		repo.insert_one.return_value = {"id": "user-1", "email": "user@example.com"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			for _ in range(3):
				routes_auth.signup(
					routes_auth.SignupRequest(email="user@example.com", password="password123"),
					_make_request(),
				)

			with self.assertRaises(HTTPException) as context:
				routes_auth.signup(
					routes_auth.SignupRequest(email="user@example.com", password="password123"),
					_make_request(),
				)

		self.assertEqual(context.exception.status_code, 429)

	def test_rate_limit_is_scoped_per_address(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None
		repo.insert_one.return_value = {"id": "user-1", "email": "user@example.com"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			for _ in range(3):
				routes_auth.signup(
					routes_auth.SignupRequest(email="user@example.com", password="password123"),
					_make_request(host="203.0.113.1"),
				)

			# A different caller still has its own untouched budget.
			routes_auth.signup(
				routes_auth.SignupRequest(email="other@example.com", password="password123"),
				_make_request(host="203.0.113.2"),
			)


class RateLimiterPrimitiveTests(TestCase):
	"""Direct tests of the throttling primitives, using an injected clock."""

	def test_allows_up_to_the_limit_then_blocks(self) -> None:
		now = [1000.0]
		limiter = FixedWindowRateLimiter(
			max_events=3, window_seconds=60, clock=lambda: now[0]
		)

		for _ in range(3):
			limiter.check("key")

		with self.assertRaises(RateLimitExceeded):
			limiter.check("key")

	def test_window_slides_so_old_events_stop_counting(self) -> None:
		now = [1000.0]
		limiter = FixedWindowRateLimiter(
			max_events=2, window_seconds=60, clock=lambda: now[0]
		)

		limiter.check("key")
		limiter.check("key")
		with self.assertRaises(RateLimitExceeded):
			limiter.check("key")

		now[0] += 61
		limiter.check("key")

	def test_blocked_attempts_do_not_extend_the_lockout(self) -> None:
		now = [1000.0]
		limiter = FixedWindowRateLimiter(
			max_events=1, window_seconds=60, clock=lambda: now[0]
		)

		limiter.check("key")
		now[0] += 30
		for _ in range(5):
			with self.assertRaises(RateLimitExceeded):
				limiter.check("key")

		# Only the original event counted, so the window clears on schedule.
		now[0] += 31
		limiter.check("key")

	def test_retry_after_is_always_a_positive_integer(self) -> None:
		exc = RateLimitExceeded(0.2)
		self.assertEqual(exc.retry_after_seconds, 1)

	def test_keys_are_tracked_independently(self) -> None:
		now = [1000.0]
		limiter = FixedWindowRateLimiter(
			max_events=1, window_seconds=60, clock=lambda: now[0]
		)

		limiter.check("a")
		limiter.check("b")

	def test_failure_tracker_locks_out_then_expires(self) -> None:
		now = [1000.0]
		tracker = FailureTracker(
			max_failures=2, lockout_seconds=300, clock=lambda: now[0]
		)

		tracker.check("account")
		tracker.record_failure("account")
		tracker.check("account")
		tracker.record_failure("account")

		with self.assertRaises(RateLimitExceeded):
			tracker.check("account")

		now[0] += 301
		tracker.check("account")

	def test_failure_tracker_reset_clears_the_streak(self) -> None:
		tracker = FailureTracker(max_failures=2, lockout_seconds=300)

		tracker.record_failure("account")
		tracker.record_failure("account")
		tracker.reset("account")

		tracker.check("account")

	def test_rejects_nonsensical_configuration(self) -> None:
		with self.assertRaises(ValueError):
			FixedWindowRateLimiter(max_events=0, window_seconds=60)
		with self.assertRaises(ValueError):
			FixedWindowRateLimiter(max_events=1, window_seconds=0)
		with self.assertRaises(ValueError):
			FailureTracker(max_failures=0, lockout_seconds=60)


class TokenVerificationTests(_AuthTestBase):
	def _token(self, **overrides: object) -> str:
		now = datetime.now(timezone.utc)
		payload: dict[str, object] = {
			"sub": "user-1",
			"email": "user@example.com",
			"iat": now,
			"exp": now + timedelta(hours=1),
		}
		payload.update(overrides)
		return jwt.encode(payload, _TEST_SECRET, algorithm="HS256")

	def test_accepts_a_valid_token(self) -> None:
		user = authenticate_access_token(self._token())

		self.assertEqual(user.user_id, "user-1")
		self.assertEqual(user.email, "user@example.com")

	def test_rejects_an_expired_token(self) -> None:
		expired = datetime.now(timezone.utc) - timedelta(hours=1)
		with self.assertRaises(HTTPException) as context:
			authenticate_access_token(self._token(exp=expired))

		self.assertEqual(context.exception.status_code, 401)

	def test_rejects_a_token_signed_with_another_secret(self) -> None:
		now = datetime.now(timezone.utc)
		forged = jwt.encode(
			{"sub": "user-1", "iat": now, "exp": now + timedelta(hours=1)},
			"attacker-controlled-secret",
			algorithm="HS256",
		)

		with self.assertRaises(HTTPException) as context:
			authenticate_access_token(forged)

		self.assertEqual(context.exception.status_code, 401)

	def test_rejects_a_token_without_a_subject(self) -> None:
		with self.assertRaises(HTTPException) as context:
			authenticate_access_token(self._token(sub=""))

		self.assertEqual(context.exception.status_code, 401)

	def test_rejects_blank_and_malformed_tokens(self) -> None:
		for candidate in ("", "   ", "not-a-jwt", "a.b.c"):
			with self.assertRaises(HTTPException) as context:
				authenticate_access_token(candidate)
			self.assertEqual(context.exception.status_code, 401)

	def test_rejects_an_unsigned_none_algorithm_token(self) -> None:
		"""The classic 'alg: none' downgrade must not authenticate anyone."""
		now = datetime.now(timezone.utc)
		unsigned = jwt.encode(
			{"sub": "user-1", "iat": now, "exp": now + timedelta(hours=1)},
			key="",
			algorithm="none",
		)

		with self.assertRaises(HTTPException) as context:
			authenticate_access_token(unsigned)

		self.assertEqual(context.exception.status_code, 401)


class SessionOwnershipTests(TestCase):
	def setUp(self) -> None:
		self.user = AuthenticatedUser(
			user_id="user-1",
			email="user@example.com",
			raw_user={"id": "user-1"},
		)

	def test_owner_may_access_their_session(self) -> None:
		session = {"user_id": "user-1"}
		self.assertIs(ensure_session_access(session, self.user), session)

	def test_another_user_is_forbidden(self) -> None:
		with self.assertRaises(HTTPException) as context:
			ensure_session_access({"user_id": "user-2"}, self.user)

		self.assertEqual(context.exception.status_code, 403)

	def test_anonymous_caller_is_unauthorized(self) -> None:
		with self.assertRaises(HTTPException) as context:
			ensure_session_access({"user_id": "user-1"}, None)

		self.assertEqual(context.exception.status_code, 401)

	def test_ownerless_session_is_a_conflict(self) -> None:
		for session in ({}, {"user_id": ""}, {"user_id": "   "}):
			with self.assertRaises(HTTPException) as context:
				ensure_session_access(session, self.user)
			self.assertEqual(context.exception.status_code, 409)

	def test_resolve_user_id_rejects_a_mismatched_request(self) -> None:
		with self.assertRaises(HTTPException) as context:
			resolve_authenticated_user_id("user-2", self.user)

		self.assertEqual(context.exception.status_code, 403)

	def test_resolve_user_id_prefers_the_authenticated_identity(self) -> None:
		self.assertEqual(resolve_authenticated_user_id(None, self.user), "user-1")
		self.assertEqual(resolve_authenticated_user_id("user-1", self.user), "user-1")


class AuthHttpLayerTests(_AuthTestBase):
	"""End-to-end checks through the real FastAPI stack.

	The tests above call the route functions directly, which does not exercise
	the framework wiring. These confirm that ``Request`` really is injected, that
	a policy violation surfaces as a 422, and that ``Retry-After`` actually
	reaches the client on a 429.
	"""

	def setUp(self) -> None:
		super().setUp()
		from fastapi import FastAPI
		from fastapi.testclient import TestClient

		app = FastAPI()
		app.include_router(routes_auth.router)
		self._client_context = TestClient(app)
		self.client = self._client_context.__enter__()

	def tearDown(self) -> None:
		self._client_context.__exit__(None, None, None)
		super().tearDown()

	def test_signup_over_http_returns_a_token(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None
		repo.insert_one.return_value = {"id": "user-1", "email": "user@example.com"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			response = self.client.post(
				"/auth/signup",
				json={"email": "user@example.com", "password": "password123"},
			)

		self.assertEqual(response.status_code, 200)
		self.assertIn("access_token", response.json())

	def test_weak_password_is_rejected_as_422_over_http(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			response = self.client.post(
				"/auth/signup",
				json={"email": "user@example.com", "password": "short"},
			)

		self.assertEqual(response.status_code, 422)
		repo.insert_one.assert_not_called()

	def test_oversized_password_is_rejected_as_422_not_500(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			response = self.client.post(
				"/auth/signup",
				json={"email": "user@example.com", "password": "\U0001F600" * 30},
			)

		self.assertEqual(response.status_code, 422)

	def test_rate_limited_response_carries_a_retry_after_header(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			last_response = None
			# The default budget is 10 per window; the 11th must be rejected.
			for _ in range(11):
				last_response = self.client.post(
					"/auth/login",
					json={"email": "user@example.com", "password": "password123"},
				)

		self.assertEqual(last_response.status_code, 429)
		self.assertIn("retry-after", {k.lower() for k in last_response.headers})
		self.assertGreaterEqual(int(last_response.headers["retry-after"]), 1)

	def test_protected_route_requires_a_bearer_token(self) -> None:
		self.assertEqual(self.client.get("/auth/me").status_code, 401)

	def test_protected_route_accepts_a_valid_bearer_token(self) -> None:
		now = datetime.now(timezone.utc)
		token = jwt.encode(
			{
				"sub": "user-1",
				"email": "user@example.com",
				"iat": now,
				"exp": now + timedelta(hours=1),
			},
			_TEST_SECRET,
			algorithm="HS256",
		)

		response = self.client.get(
			"/auth/me", headers={"Authorization": f"Bearer {token}"}
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["user"]["id"], "user-1")

	def test_status_route_reports_anonymous_without_a_token(self) -> None:
		response = self.client.get("/auth/status")

		self.assertEqual(response.status_code, 200)
		self.assertFalse(response.json()["authenticated"])


class TokenRevocationTests(_AuthTestBase):
	"""Signing out must actually invalidate the token server-side.

	Before revocation existed, logout only cleared the client's copy: a captured
	token stayed valid until it expired and could not be invalidated at all.
	"""

	def _token(self, **overrides: object) -> tuple[str, str]:
		"""Return (encoded token, its jti)."""
		import uuid as _uuid

		now = datetime.now(timezone.utc)
		token_id = _uuid.uuid4().hex
		payload: dict[str, object] = {
			"sub": "user-1",
			"email": "user@example.com",
			"iat": now,
			"exp": now + timedelta(hours=1),
			"jti": token_id,
		}
		payload.update(overrides)
		return jwt.encode(payload, _TEST_SECRET, algorithm="HS256"), token_id

	def test_issued_tokens_carry_a_unique_jti(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None
		repo.insert_one.return_value = {"id": "user-1", "email": "user@example.com"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			first = routes_auth.signup(
				routes_auth.SignupRequest(email="a@example.com", password="password123"),
				_make_request(),
			)["access_token"]
			second = routes_auth.signup(
				routes_auth.SignupRequest(email="b@example.com", password="password123"),
				_make_request(),
			)["access_token"]

		jti_a = jwt.decode(first, _TEST_SECRET, algorithms=["HS256"])["jti"]
		jti_b = jwt.decode(second, _TEST_SECRET, algorithms=["HS256"])["jti"]
		self.assertTrue(jti_a and jti_b)
		self.assertNotEqual(jti_a, jti_b, "each token needs its own id to be revocable")

	def test_a_revoked_token_is_rejected(self) -> None:
		token, _ = self._token()
		with patch("backend.api.auth._is_token_revoked", return_value=True):
			with self.assertRaises(HTTPException) as context:
				authenticate_access_token(token)

		self.assertEqual(context.exception.status_code, 401)
		self.assertIn("signed out", context.exception.detail)

	def test_a_live_token_is_still_accepted(self) -> None:
		token, _ = self._token()
		with patch("backend.api.auth._is_token_revoked", return_value=False):
			user = authenticate_access_token(token)

		self.assertEqual(user.user_id, "user-1")

	def test_logout_records_the_revocation(self) -> None:
		token, token_id = self._token()
		repo = MagicMock()
		credentials = MagicMock()
		credentials.credentials = token
		user = AuthenticatedUser(user_id="user-1", email="u@example.com", raw_user={})

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			result = routes_auth.logout(credentials, user)

		self.assertTrue(result["revoked"])
		repo.revoke_token.assert_called_once()
		kwargs = repo.revoke_token.call_args.kwargs
		self.assertEqual(kwargs["jti"], token_id)
		self.assertEqual(kwargs["user_id"], "user-1")
		# expires_at must be the token's own exp, so the TTL index can drop the
		# row exactly when the token would have died anyway.
		self.assertIsInstance(kwargs["expires_at"], datetime)

	def test_logout_surfaces_a_storage_failure(self) -> None:
		"""A failed revocation must not report success -- the user believes they are out."""
		token, _ = self._token()
		repo = MagicMock()
		repo.revoke_token.side_effect = RuntimeError("mongo down")
		credentials = MagicMock()
		credentials.credentials = token
		user = AuthenticatedUser(user_id="user-1", email="u@example.com", raw_user={})

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			with self.assertRaises(HTTPException) as context:
				routes_auth.logout(credentials, user)

		self.assertEqual(context.exception.status_code, 503)

	def test_tokens_without_a_jti_still_authenticate(self) -> None:
		"""Tokens issued before revocation existed must not be invalidated on deploy."""
		now = datetime.now(timezone.utc)
		legacy = jwt.encode(
			{"sub": "user-1", "email": "u@example.com", "iat": now, "exp": now + timedelta(hours=1)},
			_TEST_SECRET,
			algorithm="HS256",
		)

		user = authenticate_access_token(legacy)
		self.assertEqual(user.user_id, "user-1")

	def test_revocation_lookup_failure_does_not_lock_everyone_out(self) -> None:
		"""A database blip must not deny every authenticated request."""
		token, _ = self._token()
		with patch(
			"backend.database.mongo_client.get_repository",
			side_effect=RuntimeError("mongo down"),
		):
			user = authenticate_access_token(token)

		self.assertEqual(user.user_id, "user-1")


class ResetTokenHashingTests(TestCase):
	def test_is_deterministic(self) -> None:
		self.assertEqual(
			routes_auth._hash_reset_token("same-token"),
			routes_auth._hash_reset_token("same-token"),
		)

	def test_different_tokens_hash_differently(self) -> None:
		self.assertNotEqual(
			routes_auth._hash_reset_token("token-a"),
			routes_auth._hash_reset_token("token-b"),
		)

	def test_never_returns_the_raw_token(self) -> None:
		self.assertNotIn("super-secret-token", routes_auth._hash_reset_token("super-secret-token"))


class ForgotPasswordTests(_AuthTestBase):
	def test_an_existing_account_gets_a_token_and_an_email(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = {"_id": "user-1", "email": "user@example.com"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo), \
			 patch("backend.api.routes_auth.send_email") as mock_send:
			result = routes_auth.forgot_password(
				routes_auth.ForgotPasswordRequest(email="user@example.com"), _make_request()
			)

		repo.create_password_reset_token.assert_called_once()
		kwargs = repo.create_password_reset_token.call_args.kwargs
		self.assertEqual(kwargs["user_id"], "user-1")
		mock_send.assert_called_once()
		self.assertEqual(mock_send.call_args.kwargs["to"], "user@example.com")
		self.assertIn("detail", result)

	def test_an_unknown_address_gets_the_identical_response_and_no_token(self) -> None:
		repo = MagicMock()
		repo.db.users.find_one.return_value = None

		with patch("backend.api.routes_auth.get_repository", return_value=repo), \
			 patch("backend.api.routes_auth.send_email") as mock_send:
			result = routes_auth.forgot_password(
				routes_auth.ForgotPasswordRequest(email="nobody@example.com"), _make_request()
			)

		repo.create_password_reset_token.assert_not_called()
		mock_send.assert_not_called()
		self.assertEqual(result, {"detail": routes_auth._GENERIC_FORGOT_PASSWORD_DETAIL})

	def test_known_and_unknown_addresses_get_byte_identical_responses(self) -> None:
		"""The response body itself must carry no signal either way."""
		found_repo = MagicMock()
		found_repo.db.users.find_one.return_value = {"_id": "user-1", "email": "user@example.com"}
		missing_repo = MagicMock()
		missing_repo.db.users.find_one.return_value = None

		with patch("backend.api.routes_auth.get_repository", return_value=found_repo), \
			 patch("backend.api.routes_auth.send_email"):
			found_result = routes_auth.forgot_password(
				routes_auth.ForgotPasswordRequest(email="user@example.com"), _make_request("203.0.113.1")
			)
		with patch("backend.api.routes_auth.get_repository", return_value=missing_repo), \
			 patch("backend.api.routes_auth.send_email"):
			missing_result = routes_auth.forgot_password(
				routes_auth.ForgotPasswordRequest(email="nobody@example.com"), _make_request("203.0.113.2")
			)

		self.assertEqual(found_result, missing_result)

	def test_an_email_delivery_failure_is_swallowed_not_surfaced(self) -> None:
		"""Letting delivery failure reach the caller would disclose the address exists."""
		repo = MagicMock()
		repo.db.users.find_one.return_value = {"_id": "user-1", "email": "user@example.com"}

		with patch("backend.api.routes_auth.get_repository", return_value=repo), \
			 patch(
				 "backend.api.routes_auth.send_email",
				 side_effect=routes_auth.EmailDeliveryError("smtp down"),
			 ):
			result = routes_auth.forgot_password(
				routes_auth.ForgotPasswordRequest(email="user@example.com"), _make_request()
			)

		self.assertEqual(result, {"detail": routes_auth._GENERIC_FORGOT_PASSWORD_DETAIL})


class ResetPasswordTests(_AuthTestBase):
	def test_a_valid_token_updates_the_password(self) -> None:
		repo = MagicMock()
		repo.consume_password_reset_token.return_value = {
			"_id": "hash", "user_id": "user-1", "used_at": "now",
		}

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			result = routes_auth.reset_password(
				routes_auth.ResetPasswordRequest(token="raw-token", new_password="new-password-123")
			)

		repo.db.users.update_one.assert_called_once()
		args, _ = repo.db.users.update_one.call_args
		self.assertEqual(args[0], {"_id": "user-1"})
		new_hash = args[1]["$set"]["password_hash"]
		self.assertTrue(bcrypt.checkpw(b"new-password-123", new_hash.encode("utf-8")))
		self.assertIn("detail", result)

	def test_an_invalid_or_expired_or_reused_token_is_rejected(self) -> None:
		repo = MagicMock()
		repo.consume_password_reset_token.return_value = None

		with patch("backend.api.routes_auth.get_repository", return_value=repo):
			with self.assertRaises(HTTPException) as context:
				routes_auth.reset_password(
					routes_auth.ResetPasswordRequest(token="bad-token", new_password="new-password-123")
				)

		self.assertEqual(context.exception.status_code, 400)
		repo.db.users.update_one.assert_not_called()

	def test_the_new_password_is_still_subject_to_the_signup_policy(self) -> None:
		with self.assertRaises(ValidationError):
			routes_auth.ResetPasswordRequest(token="raw-token", new_password="short")
