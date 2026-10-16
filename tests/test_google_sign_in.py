"""Sign in with Google: every check that makes the ID token trustworthy.

The token is signed here with a locally generated RSA key standing in for
Google's, so the real verification path (signature, audience, issuer, expiry,
email_verified) runs; only the key download is replaced.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from backend import config
from backend.api import routes_auth

_CLIENT_ID = "1234-test.apps.googleusercontent.com"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(**overrides: object) -> str:
	now = int(time.time())
	claims = {
		"iss": "https://accounts.google.com",
		"aud": _CLIENT_ID,
		"sub": "google-user-1",
		"email": "Person@Gmail.com",
		"email_verified": True,
		"name": "Person",
		"iat": now,
		"exp": now + 600,
	}
	claims.update(overrides)
	return jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": "k1"})


def _request() -> MagicMock:
	request = MagicMock()
	request.client.host = "203.0.113.5"
	request.headers = {}
	return request


class GoogleSignInTests(TestCase):
	def setUp(self) -> None:
		self._env = patch.dict(
			os.environ,
			{"GOOGLE_CLIENT_ID": _CLIENT_ID, "AUTH_JWT_SECRET": "unit-test-signing-secret-padded-to-32-bytes-minimum"},
			clear=False,
		)
		self._env.start()
		config.reset_settings()
		routes_auth.reset_auth_throttles()
		fake_keys = SimpleNamespace(get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=_KEY.public_key()))
		self._keys = patch.object(routes_auth, "_google_jwks_client", fake_keys)
		self._keys.start()
		self.repo = MagicMock()
		self.repo.db.users.find_one.return_value = None
		self.repo.insert_one.return_value = {"id": "new-user-id", "email": "person@gmail.com"}
		self._repo = patch.object(routes_auth, "get_repository", return_value=self.repo)
		self._repo.start()

	def tearDown(self) -> None:
		for patcher in (self._repo, self._keys, self._env):
			patcher.stop()
		config.reset_settings()
		routes_auth.reset_auth_throttles()

	def _sign_in(self, token: str) -> dict:
		return routes_auth.google_sign_in(routes_auth.GoogleSignInRequest(credential=token), _request())

	def _assert_refused(self, token: str, status: int = 401) -> None:
		with self.assertRaises(HTTPException) as ctx:
			self._sign_in(token)
		self.assertEqual(ctx.exception.status_code, status)
		self.repo.insert_one.assert_not_called()

	def test_a_first_sign_in_creates_a_passwordless_account(self) -> None:
		body = self._sign_in(_token())

		self.assertIn("access_token", body)
		saved = self.repo.insert_one.call_args.args[1]
		self.assertEqual(saved["email"], "person@gmail.com")
		self.assertIsNone(saved["password_hash"])
		self.assertEqual(saved["auth_provider"], "google")

	def test_an_existing_account_with_that_email_is_signed_in(self) -> None:
		self.repo.db.users.find_one.return_value = {"_id": "existing-id", "email": "person@gmail.com"}

		body = self._sign_in(_token())

		claims = jwt.decode(body["access_token"], options={"verify_signature": False})
		self.assertEqual(claims["sub"], "existing-id")
		self.repo.insert_one.assert_not_called()

	def test_a_token_issued_for_another_app_is_refused(self) -> None:
		self._assert_refused(_token(aud="someone-elses-app.apps.googleusercontent.com"))

	def test_an_unverified_email_is_refused(self) -> None:
		self._assert_refused(_token(email_verified=False))

	def test_a_non_google_issuer_is_refused(self) -> None:
		self._assert_refused(_token(iss="https://evil.example.com"))

	def test_an_expired_token_is_refused(self) -> None:
		self._assert_refused(_token(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600))

	def test_a_token_signed_by_another_key_is_refused(self) -> None:
		other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
		forged = jwt.encode(jwt.decode(_token(), options={"verify_signature": False}), other, algorithm="RS256")
		self._assert_refused(forged)

	def test_unconfigured_server_says_so(self) -> None:
		with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": ""}):
			self._assert_refused(_token(), status=503)
			self.assertEqual(routes_auth.auth_config(), {"google_client_id": None})

	def test_config_exposes_the_client_id_for_the_button(self) -> None:
		self.assertEqual(routes_auth.auth_config(), {"google_client_id": _CLIENT_ID})

	def test_a_google_only_account_cannot_be_password_logged_in(self) -> None:
		self.assertFalse(routes_auth._verify_password("anything", None))


class SignupNameTests(TestCase):
	def setUp(self) -> None:
		self._env = patch.dict(os.environ, {"AUTH_JWT_SECRET": "unit-test-signing-secret-padded-to-32-bytes-minimum"}, clear=False)
		self._env.start()
		config.reset_settings()
		routes_auth.reset_auth_throttles()

	def tearDown(self) -> None:
		self._env.stop()
		config.reset_settings()
		routes_auth.reset_auth_throttles()

	def _signup(self, **fields):
		repo = MagicMock()
		repo.db.users.find_one.return_value = None
		repo.insert_one.return_value = {"id": "u1", "email": "a@gmail.com"}
		with patch.object(routes_auth, "get_repository", return_value=repo):
			routes_auth.signup(routes_auth.SignupRequest(email="a@gmail.com", password="LongEnough123", **fields), _request())
		return repo.insert_one.call_args.args[1]

	def test_names_are_stored(self) -> None:
		saved = self._signup(first_name=" Ada ", last_name="Lovelace")
		self.assertEqual((saved["first_name"], saved["last_name"], saved["display_name"]), ("Ada", "Lovelace", "Ada Lovelace"))

	def test_names_are_optional(self) -> None:
		saved = self._signup()
		self.assertIsNone(saved["display_name"])
