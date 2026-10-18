"""Phase 2.13: the interview socket is opened with a single-use ticket.

Browsers cannot set headers on a WebSocket handshake, so the socket used to be
opened with ``?access_token=<24-hour JWT>`` — and URLs are written to proxy and
access logs, including the platform ingress. One log line was a day of account
access. The client now trades its access token for a ticket that is good for
one connection within a minute, and is refused everywhere else.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import MagicMock, patch

import jwt
from fastapi import HTTPException

from backend import config
from backend.api import routes_auth
from backend.api.auth import (
	AuthenticatedUser,
	authenticate_access_token,
	authenticate_websocket_ticket,
	issue_websocket_ticket,
)
from backend.api.interview_runtime import WebSocketInterviewError, _authenticate_websocket_user
from backend.database.db_errors import DatabaseDependencyError, DuplicateRecordError

_SECRET = "unit-test-signing-secret-padded-to-32-bytes-minimum"
_USER = AuthenticatedUser(user_id="user-1", email="u@example.com", raw_user={})
_TEST_URI = os.environ.get("MONGO_TEST_URI") or "mongodb://localhost:27017"


def _repo(first_use: bool = True) -> MagicMock:
	repo = MagicMock()
	repo.consume_token_once.return_value = first_use
	repo.is_token_revoked.return_value = False
	return repo


class _Base(TestCase):
	def setUp(self) -> None:
		self._env = patch.dict(
			os.environ,
			{"AUTH_JWT_SECRET": _SECRET, "AUTH_JWT_ALGORITHM": "HS256"},
			clear=False,
		)
		self._env.start()
		config.reset_settings()

	def tearDown(self) -> None:
		self._env.stop()
		config.reset_settings()

	def _access_token(self) -> str:
		now = datetime.now(timezone.utc)
		return jwt.encode(
			{"sub": "user-1", "exp": now + timedelta(hours=24), "jti": uuid.uuid4().hex},
			_SECRET,
			algorithm="HS256",
		)

	def _consume(self, ticket: str, repo: MagicMock) -> AuthenticatedUser:
		with patch("backend.database.mongo_client.get_repository", return_value=repo):
			return authenticate_websocket_ticket(ticket)


class TicketTests(_Base):
	def test_a_fresh_ticket_authenticates_its_user_once(self) -> None:
		ticket = issue_websocket_ticket(_USER)
		repo = _repo()

		user = self._consume(ticket, repo)

		self.assertEqual(user.user_id, "user-1")
		repo.consume_token_once.assert_called_once()

	def test_a_spent_ticket_is_refused(self) -> None:
		"""The property that makes a logged URL worthless."""

		with self.assertRaises(HTTPException) as ctx:
			self._consume(issue_websocket_ticket(_USER), _repo(first_use=False))
		self.assertEqual(ctx.exception.status_code, 401)
		self.assertIn("already been used", ctx.exception.detail)

	def test_losing_the_unique_index_race_is_also_a_spent_ticket(self) -> None:
		repo = _repo()
		repo.consume_token_once.side_effect = DuplicateRecordError("E11000")

		with self.assertRaises(HTTPException) as ctx:
			self._consume(issue_websocket_ticket(_USER), repo)
		self.assertEqual(ctx.exception.status_code, 401)

	def test_a_database_failure_fails_closed(self) -> None:
		"""Unlike revocation checks, single use is the whole guarantee here."""

		repo = _repo()
		repo.consume_token_once.side_effect = DatabaseDependencyError("down")

		with self.assertRaises(HTTPException) as ctx:
			self._consume(issue_websocket_ticket(_USER), repo)
		self.assertEqual(ctx.exception.status_code, 503)

	def test_a_ticket_expires_within_a_minute(self) -> None:
		ticket = issue_websocket_ticket(_USER)
		exp = jwt.decode(ticket, _SECRET, algorithms=["HS256"])["exp"]
		self.assertLessEqual(exp - datetime.now(timezone.utc).timestamp(), 61)

		stale = jwt.encode(
			{
				"sub": "user-1",
				"typ": "ws_ticket",
				"exp": datetime.now(timezone.utc) - timedelta(seconds=1),
				"jti": uuid.uuid4().hex,
			},
			_SECRET,
			algorithm="HS256",
		)
		with self.assertRaises(HTTPException) as ctx:
			self._consume(stale, _repo())
		self.assertIn("expired", ctx.exception.detail)

	def test_a_ticket_is_not_an_access_token(self) -> None:
		"""Same signing key, so without an explicit check a ticket would work on
		every HTTP route for its lifetime."""

		ticket = issue_websocket_ticket(_USER)
		with patch("backend.api.auth._is_token_revoked", return_value=False):
			with self.assertRaises(HTTPException) as ctx:
				authenticate_access_token(ticket)
		self.assertEqual(ctx.exception.status_code, 401)

	def test_an_access_token_is_not_a_ticket(self) -> None:
		with self.assertRaises(HTTPException) as ctx:
			self._consume(self._access_token(), _repo())
		self.assertEqual(ctx.exception.status_code, 401)

	def test_the_endpoint_issues_a_ticket_for_the_caller(self) -> None:
		body = routes_auth.auth_ws_ticket(current_user=_USER)

		self.assertEqual(body["expires_in"], 60)
		self.assertEqual(self._consume(body["ticket"], _repo()).user_id, "user-1")


class HandshakeTests(_Base):
	def _socket(self, query: dict[str, str], headers: dict[str, str] | None = None):
		return SimpleNamespace(query_params=query, headers=headers or {})

	def test_the_access_token_is_no_longer_accepted_in_the_url(self) -> None:
		with self.assertRaises(WebSocketInterviewError):
			_authenticate_websocket_user(self._socket({"access_token": self._access_token()}))

	def test_a_ticket_in_the_url_is_accepted(self) -> None:
		ticket = issue_websocket_ticket(_USER)
		with patch("backend.database.mongo_client.get_repository", return_value=_repo()):
			user = _authenticate_websocket_user(self._socket({"ticket": ticket}))
		self.assertEqual(user.user_id, "user-1")

	def test_a_bearer_header_still_works_for_non_browser_clients(self) -> None:
		headers = {"authorization": f"Bearer {self._access_token()}"}
		with patch("backend.api.auth._is_token_revoked", return_value=False):
			user = _authenticate_websocket_user(self._socket({}, headers))
		self.assertEqual(user.user_id, "user-1")


def _mongo_available() -> bool:
	try:
		import pymongo

		client = pymongo.MongoClient(_TEST_URI, serverSelectionTimeoutMS=1500)
		client.server_info()
		client.close()
		return True
	except Exception:
		return False


@skipUnless(_mongo_available(), f"no MongoDB reachable at {_TEST_URI}")
class ConsumeOnceAgainstMongoTests(TestCase):
	def test_only_the_first_consume_succeeds(self) -> None:
		from backend.config import MongoSettings
		from backend.database.mongo_client import MongoRepository

		db_name = f"interview_sim_ticket_{uuid.uuid4().hex[:12]}"
		repo = MongoRepository(MongoSettings(uri=_TEST_URI, database_name=db_name))
		try:
			expires = datetime.now(timezone.utc) + timedelta(seconds=60)
			jti = uuid.uuid4().hex
			self.assertTrue(repo.consume_token_once(jti=jti, user_id="u", expires_at=expires))
			self.assertFalse(repo.consume_token_once(jti=jti, user_id="u", expires_at=expires))
			self.assertTrue(repo.is_token_revoked(jti=jti))
		finally:
			repo.client.drop_database(db_name)
			repo.client.close()
