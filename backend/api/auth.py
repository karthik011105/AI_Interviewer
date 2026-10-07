"""Authentication helpers for FastAPI routes.

Tokens are self-issued HS256 JWTs signed with ``AUTH_JWT_SECRET`` (see
``backend/api/routes_auth.py`` for signup/login issuance). This module handles
verification, the bearer-token dependencies, and session-ownership checks.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

from backend.config import get_settings
from backend.database.db_errors import DatabaseClientError, DuplicateRecordError

_LOGGER = logging.getLogger(__name__)
_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
	user_id: str
	email: str | None
	raw_user: Mapping[str, Any]


def _coerce_mapping(value: Any) -> dict[str, Any]:
	if value is None:
		return {}
	if isinstance(value, Mapping):
		return dict(value)
	if hasattr(value, "model_dump"):
		payload = value.model_dump()
		if isinstance(payload, Mapping):
			return dict(payload)
	if hasattr(value, "dict"):
		payload = value.dict()
		if isinstance(payload, Mapping):
			return dict(payload)
	result: dict[str, Any] = {}
	for field in ("id", "email", "aud", "role", "app_metadata", "user_metadata"):
		if hasattr(value, field):
			result[field] = getattr(value, field)
	return result


def _extract_user_payload(response: Any) -> dict[str, Any]:
	if isinstance(response, Mapping) and isinstance(response.get("user"), Mapping):
		return dict(response["user"])

	user = getattr(response, "user", None)
	return _coerce_mapping(user)


def serialize_authenticated_user(current_user: AuthenticatedUser) -> dict[str, Any]:
	return {
		"id": current_user.user_id,
		"email": current_user.email,
		"app_metadata": dict(current_user.raw_user.get("app_metadata") or {}),
		"user_metadata": dict(current_user.raw_user.get("user_metadata") or {}),
	}


def _is_token_revoked(token_id: str) -> bool:
	"""Return True when this token id appears in the revocation denylist.

	Imported lazily to keep this module importable without a database — several
	helpers here (and their tests) do pure token work with no Mongo available.

	A database failure is treated as "not revoked" rather than failing the
	request. That is a deliberate availability trade-off: the alternative locks
	every authenticated user out of the application whenever MongoDB blips. The
	exposure window is bounded by the token's own expiry, and every route that
	touches data needs the database anyway, so it will fail there instead.
	"""

	try:
		from backend.database.mongo_client import get_repository

		return get_repository().is_token_revoked(jti=token_id)
	except Exception:
		_LOGGER.exception(
			"Could not check the token revocation list; allowing the request."
		)
		return False


def authenticate_access_token(access_token: str) -> AuthenticatedUser:
	trimmed = str(access_token or "").strip()
	if not trimmed:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Sign in is required for this operation.",
		)

	try:
		auth_settings = get_settings().auth
		payload = jwt.decode(
			trimmed,
			auth_settings.jwt_secret,
			algorithms=[auth_settings.jwt_algorithm],
		)
		
		user_id = str(payload.get("sub") or "").strip()
		if not user_id:
			raise ValueError("Invalid JWT payload: missing sub")

		# A WebSocket ticket is signed with the same key, so without this check
		# it would work as a (short-lived) bearer token on every HTTP route.
		if payload.get("typ") == WS_TICKET_TYPE:
			raise jwt.InvalidTokenError("A WebSocket ticket is not an access token.")

		# Reject tokens that have been explicitly revoked (logout). Tokens issued
		# before revocation existed carry no jti; those are still accepted, since
		# refusing them would sign out every existing session on deploy. They
		# simply cannot be revoked individually until reissued at next sign-in.
		token_id = str(payload.get("jti") or "").strip()
		if token_id and _is_token_revoked(token_id):
			raise HTTPException(
				status_code=status.HTTP_401_UNAUTHORIZED,
				detail="This session has been signed out.",
			)

		email = str(payload.get("email") or "").strip() or None
		
		# Build a payload that matches what the rest of the code expects
		user_payload = {
			"id": user_id,
			"email": email,
			"app_metadata": payload.get("app_metadata") or {},
			"user_metadata": payload.get("user_metadata") or {},
			"aud": payload.get("aud"),
			"role": payload.get("role")
		}
		
		return AuthenticatedUser(user_id=user_id, email=email, raw_user=user_payload)
		
	except HTTPException:
		# Already a considered auth failure (e.g. a revoked token). Let it through
		# rather than letting the catch-all below flatten it into a generic
		# message that hides why the token was refused.
		raise
	except jwt.ExpiredSignatureError as exc:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Access token has expired.",
		) from exc
	except jwt.InvalidTokenError as exc:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid access token.",
		) from exc
	except Exception as exc:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid or expired access token.",
		) from exc


def get_optional_current_user(
	credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedUser | None:
	if credentials is None:
		return None
	if credentials.scheme.casefold() != "bearer":
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Authorization must use a bearer token.",
		)

	return authenticate_access_token(credentials.credentials)


def require_current_user(
	current_user: AuthenticatedUser | None = Depends(get_optional_current_user),
) -> AuthenticatedUser:
	if current_user is None:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Sign in is required for this operation.",
		)
	return current_user


def resolve_authenticated_user_id(
	requested_user_id: str | None,
	current_user: AuthenticatedUser | None,
) -> str | None:
	trimmed_user_id = str(requested_user_id or "").strip() or None
	if current_user is None:
		return trimmed_user_id
	if trimmed_user_id is not None and trimmed_user_id != current_user.user_id:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="The supplied user_id does not match the authenticated user.",
		)
	return current_user.user_id


def ensure_session_access(
	parent_session: Mapping[str, Any],
	current_user: AuthenticatedUser | None,
) -> Mapping[str, Any]:
	session_user_id = str(parent_session.get("user_id") or "").strip()
	if not session_user_id:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail=(
				"This interview session has no owner. Sign in and create a new session "
				"so resume, assessment, and report data are securely tied to your account."
			),
		)
	if current_user is None:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Sign in is required to access this saved interview session.",
		)
	if current_user.user_id != session_user_id:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="This interview session belongs to a different user.",
		)
	return parent_session


# WebSocket tickets
# -----------------
# Browsers cannot set headers on a WebSocket handshake, so the socket has to be
# authenticated through its URL — and URLs are written to proxy and access logs,
# including the platform ingress in front of the deployment. Putting the 24-hour
# access token there meant a single log line was a day of account access.
#
# Instead the client trades its access token (sent as a header, over HTTP) for a
# ticket that is good for one connection within a minute. A ticket recovered
# from a log has already been spent, and it is refused everywhere except the
# socket handshake.
WS_TICKET_TYPE = "ws_ticket"
WS_TICKET_TTL_SECONDS = 60


def issue_websocket_ticket(user: AuthenticatedUser) -> str:
	auth_settings = get_settings().auth
	now = datetime.now(timezone.utc)
	return jwt.encode(
		{
			"sub": user.user_id,
			"email": user.email,
			"typ": WS_TICKET_TYPE,
			"iat": now,
			"exp": now + timedelta(seconds=WS_TICKET_TTL_SECONDS),
			"jti": uuid.uuid4().hex,
		},
		auth_settings.jwt_secret,
		algorithm=auth_settings.jwt_algorithm,
	)


def authenticate_websocket_ticket(ticket: str) -> AuthenticatedUser:
	"""Validate a WebSocket ticket and spend it.

	Unlike access-token revocation, a database failure here fails *closed*: the
	single-use guarantee is the whole point, and the interview cannot run
	without the database anyway.
	"""

	trimmed = str(ticket or "").strip()
	if not trimmed:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Sign in is required for this operation.",
		)
	auth_settings = get_settings().auth
	try:
		payload = jwt.decode(
			trimmed,
			auth_settings.jwt_secret,
			algorithms=[auth_settings.jwt_algorithm],
			options={"require": ["exp", "jti", "sub"]},
		)
	except jwt.ExpiredSignatureError as exc:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="The connection ticket has expired. Please reconnect.",
		) from exc
	except jwt.InvalidTokenError as exc:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid connection ticket.",
		) from exc

	if payload.get("typ") != WS_TICKET_TYPE:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid connection ticket.",
		)

	user_id = str(payload["sub"]).strip()
	try:
		from backend.database.mongo_client import get_repository

		first_use = get_repository().consume_token_once(
			jti=str(payload["jti"]),
			user_id=user_id,
			expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc),
		)
	except DuplicateRecordError:
		first_use = False
	except DatabaseClientError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Could not verify the connection ticket. Please try again.",
		) from exc
	if not first_use:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="This connection ticket has already been used.",
		)

	email = str(payload.get("email") or "").strip() or None
	return AuthenticatedUser(
		user_id=user_id,
		email=email,
		raw_user={"id": user_id, "email": email, "app_metadata": {}, "user_metadata": {}},
	)


__all__ = [
	"AuthenticatedUser",
	"WS_TICKET_TTL_SECONDS",
	"authenticate_access_token",
	"authenticate_websocket_ticket",
	"issue_websocket_ticket",
	"ensure_session_access",
	"get_optional_current_user",
	"require_current_user",
	"resolve_authenticated_user_id",
	"serialize_authenticated_user",
]