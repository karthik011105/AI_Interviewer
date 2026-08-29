"""Custom auth routes backed by MongoDB and self-issued JWTs."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import NoReturn

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from pymongo.errors import DuplicateKeyError

from backend.api.auth import (
	AuthenticatedUser,
	get_optional_current_user,
	require_current_user,
	serialize_authenticated_user,
)
from backend.api.rate_limit import (
	FailureTracker,
	FixedWindowRateLimiter,
	RateLimitExceeded,
)
from backend.config import get_settings
from backend.database.mongo_client import get_repository

router = APIRouter(prefix="/auth", tags=["auth"])
_LOGGER = logging.getLogger(__name__)

# Used to equalise response timing when no account exists for the submitted
# address, so that sign-in latency does not reveal whether an account is
# present. Computed once at import; the value itself is never a valid match.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"invalid-placeholder-password", bcrypt.gensalt())

_INVALID_CREDENTIALS_DETAIL = "Invalid email or password."

_throttle_lock = threading.Lock()
_ip_rate_limiter: FixedWindowRateLimiter | None = None
_login_failure_tracker: FailureTracker | None = None


def _get_ip_rate_limiter() -> FixedWindowRateLimiter:
	global _ip_rate_limiter
	with _throttle_lock:
		if _ip_rate_limiter is None:
			auth_settings = get_settings().auth
			_ip_rate_limiter = FixedWindowRateLimiter(
				max_events=auth_settings.rate_limit_max_attempts,
				window_seconds=auth_settings.rate_limit_window_seconds,
			)
		return _ip_rate_limiter


def _get_login_failure_tracker() -> FailureTracker:
	global _login_failure_tracker
	with _throttle_lock:
		if _login_failure_tracker is None:
			auth_settings = get_settings().auth
			_login_failure_tracker = FailureTracker(
				max_failures=auth_settings.login_max_failures,
				lockout_seconds=auth_settings.login_lockout_seconds,
			)
		return _login_failure_tracker


def reset_auth_throttles() -> None:
	"""Drop the cached throttles.

	Tests use this to get a clean slate and to pick up overridden settings.
	"""

	global _ip_rate_limiter, _login_failure_tracker
	with _throttle_lock:
		_ip_rate_limiter = None
		_login_failure_tracker = None


def _normalize_email(email: str) -> str:
	"""Normalise an address to a single canonical form.

	Addresses are stored and looked up lowercased so that ``User@example.com``
	and ``user@example.com`` cannot become two accounts. Local-parts are
	technically case-sensitive per RFC 5321, but essentially every mail provider
	treats them case-insensitively, and allowing both to register is a far more
	likely source of account confusion than the theoretical alternative.
	"""

	return str(email or "").strip().casefold()


def _client_key(request: Request) -> str:
	"""Identify the caller for rate-limiting purposes.

	This deliberately uses the direct peer address and does NOT read
	``X-Forwarded-For``, because that header is attacker-controlled unless a
	trusted proxy overwrites it — honouring it blindly would let anyone bypass
	the limit by inventing a new value per request. When deploying behind a
	proxy, run uvicorn with ``--proxy-headers`` and an explicit
	``--forwarded-allow-ips`` so the peer address is rewritten safely upstream.
	"""

	client = request.client
	if client is None or not client.host:
		return "unknown"
	return str(client.host)


def _raise_rate_limited(exc: RateLimitExceeded) -> NoReturn:
	raise HTTPException(
		status_code=status.HTTP_429_TOO_MANY_REQUESTS,
		detail=str(exc),
		headers={"Retry-After": str(exc.retry_after_seconds)},
	)


def _enforce_ip_rate_limit(request: Request, scope: str) -> None:
	try:
		_get_ip_rate_limiter().check(f"{scope}:{_client_key(request)}")
	except RateLimitExceeded as exc:
		_raise_rate_limited(exc)


class SignupRequest(BaseModel):
	email: EmailStr
	password: str

	@field_validator("password")
	@classmethod
	def _validate_password_policy(cls, value: str) -> str:
		auth_settings = get_settings().auth
		if len(value) < auth_settings.password_min_length:
			raise ValueError(
				f"Password must be at least {auth_settings.password_min_length} "
				"characters long."
			)
		encoded_length = len(value.encode("utf-8"))
		if encoded_length > auth_settings.password_max_bytes:
			raise ValueError(
				f"Password must be at most {auth_settings.password_max_bytes} bytes "
				"once UTF-8 encoded. Accented, emoji, and non-Latin characters each "
				"count as more than one byte."
			)
		return value


class LoginRequest(BaseModel):
	email: EmailStr
	# The signup policy is deliberately NOT applied here. Enforcing it would lock
	# out accounts created under an earlier policy, and would disclose the policy
	# to unauthenticated callers. The bound below is only an input-size guard —
	# it is well above the 72 bytes bcrypt will actually consider.
	password: str = Field(min_length=1, max_length=1024)


def _create_jwt(user_id: str, email: str) -> str:
	auth_settings = get_settings().auth
	now = datetime.now(timezone.utc)
	payload = {
		"sub": user_id,
		"email": email,
		"iat": now,
		"exp": now + timedelta(hours=auth_settings.jwt_expiry_hours),
	}
	return jwt.encode(
		payload,
		auth_settings.jwt_secret,
		algorithm=auth_settings.jwt_algorithm,
	)


def _verify_password(password: str, password_hash: object) -> bool:
	"""Compare a submitted password against a stored bcrypt hash.

	bcrypt raises on secrets longer than 72 bytes and on malformed hashes. Both
	are treated as a failed comparison rather than propagating as a 500, since
	either way the credential does not check out.
	"""

	if not isinstance(password_hash, str) or not password_hash:
		return False
	try:
		return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
	except (ValueError, TypeError):
		return False


@router.post("/signup")
def signup(request: SignupRequest, http_request: Request) -> dict[str, str]:
	_enforce_ip_rate_limit(http_request, "signup")

	repo = get_repository()
	email = _normalize_email(request.email)

	if repo.db.users.find_one({"email": email}):
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="User with this email already exists.",
		)

	hashed = bcrypt.hashpw(request.password.encode("utf-8"), bcrypt.gensalt())
	try:
		user = repo.insert_one(
			"users",
			{
				"email": email,
				"password_hash": hashed.decode("utf-8"),
				"created_at": datetime.now(timezone.utc).isoformat(),
			},
		)
	except DuplicateKeyError as exc:
		# The check above is not atomic: two concurrent signups for the same
		# address can both pass it, and the unique index on users.email then
		# rejects the loser. Report that as the same conflict rather than
		# letting it surface as an unhandled 500.
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="User with this email already exists.",
		) from exc

	token = _create_jwt(str(user["id"]), user["email"])
	return {"access_token": token}


@router.post("/login")
def login(request: LoginRequest, http_request: Request) -> dict[str, str]:
	_enforce_ip_rate_limit(http_request, "login")

	email = _normalize_email(request.email)
	failure_tracker = _get_login_failure_tracker()

	# Keyed on the submitted address rather than a resolved account, so attempts
	# against addresses with no account are throttled identically. Keying on a
	# found user would turn the lockout into a user-enumeration oracle.
	try:
		failure_tracker.check(email)
	except RateLimitExceeded as exc:
		_LOGGER.warning("Sign-in temporarily locked out after repeated failures.")
		_raise_rate_limited(exc)

	repo = get_repository()
	user = repo.db.users.find_one({"email": email})

	if user is None:
		# Spend comparable time on a dummy hash so that response latency does not
		# distinguish "no such account" from "wrong password".
		bcrypt.checkpw(request.password.encode("utf-8"), _DUMMY_PASSWORD_HASH)
		failure_tracker.record_failure(email)
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail=_INVALID_CREDENTIALS_DETAIL,
		)

	if not _verify_password(request.password, user.get("password_hash")):
		failure_tracker.record_failure(email)
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail=_INVALID_CREDENTIALS_DETAIL,
		)

	failure_tracker.reset(email)
	return {"access_token": _create_jwt(str(user["_id"]), user["email"])}


@router.get("/status")
def auth_status(
	current_user: AuthenticatedUser | None = Depends(get_optional_current_user),
) -> dict[str, object]:
	return {
		"authenticated": current_user is not None,
		"user": serialize_authenticated_user(current_user) if current_user else None,
	}


@router.get("/me")
def auth_me(
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, object]:
	return {
		"authenticated": True,
		"user": serialize_authenticated_user(current_user),
	}


__all__ = ["reset_auth_throttles", "router"]
