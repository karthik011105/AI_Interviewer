"""Custom auth routes backed by MongoDB and self-issued JWTs."""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import NoReturn

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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
from backend.email_provider import EmailDeliveryError, send_email

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


def _validate_password_policy(value: str) -> str:
	"""Shared by SignupRequest and ResetPasswordRequest — a new password from
	either path must meet the same bar."""
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


class SignupRequest(BaseModel):
	email: EmailStr
	password: str

	@field_validator("password")
	@classmethod
	def _validate_password(cls, value: str) -> str:
		return _validate_password_policy(value)


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
		# Unique token id, so an individual token can be revoked on logout
		# without invalidating the user's other sessions.
		"jti": uuid.uuid4().hex,
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


class ForgotPasswordRequest(BaseModel):
	email: EmailStr


class ResetPasswordRequest(BaseModel):
	token: str = Field(min_length=1, max_length=512)
	new_password: str

	@field_validator("new_password")
	@classmethod
	def _validate_new_password(cls, value: str) -> str:
		return _validate_password_policy(value)


_PASSWORD_RESET_EMAIL_SUBJECT = "Reset your password"
_GENERIC_FORGOT_PASSWORD_DETAIL = (
	"If an account exists for that address, a password reset link has been sent."
)


def _hash_reset_token(raw_token: str) -> str:
	# SHA-256 of a 32-byte random token, not bcrypt: this is a lookup key for a
	# high-entropy single-use secret, not a low-entropy password an attacker
	# could feasibly brute-force offline — bcrypt's deliberate slowness buys
	# nothing here and would cost every legitimate reset a real delay.
	return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@router.post("/forgot-password")
def forgot_password(request: ForgotPasswordRequest, http_request: Request) -> dict[str, str]:
	_enforce_ip_rate_limit(http_request, "forgot_password")

	email = _normalize_email(request.email)
	repo = get_repository()
	user = repo.db.users.find_one({"email": email})

	# Identical response whether or not the account exists — the same
	# enumeration-resistance shape login already has. A response that varied
	# here would reopen exactly the oracle that hardening closed.
	if user is not None:
		auth_settings = get_settings().auth
		raw_token = secrets.token_urlsafe(32)
		expires_at = datetime.now(timezone.utc) + timedelta(
			minutes=auth_settings.password_reset_token_ttl_minutes
		)
		repo.create_password_reset_token(
			token_hash=_hash_reset_token(raw_token),
			user_id=str(user["_id"]),
			expires_at=expires_at,
		)
		reset_url = f"{auth_settings.password_reset_url_base}?token={raw_token}"
		try:
			send_email(
				to=email,
				subject=_PASSWORD_RESET_EMAIL_SUBJECT,
				body=(
					"A password reset was requested for this account.\n\n"
					f"Reset it here (expires in "
					f"{auth_settings.password_reset_token_ttl_minutes} minutes):\n"
					f"{reset_url}\n\n"
					"If you did not request this, no action is needed — the link "
					"expires on its own and your password is unchanged."
				),
			)
		except EmailDeliveryError:
			# Must not surface to the caller: doing so would disclose that the
			# address exists (the generic branch below never fails this way).
			_LOGGER.exception("Failed to send password reset email for user %s.", user["_id"])

	return {"detail": _GENERIC_FORGOT_PASSWORD_DETAIL}


@router.post("/reset-password")
def reset_password(request: ResetPasswordRequest) -> dict[str, str]:
	repo = get_repository()
	record = repo.consume_password_reset_token(
		token_hash=_hash_reset_token(request.token),
		now=datetime.now(timezone.utc),
	)
	if record is None:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="This password reset link is invalid, expired, or already used.",
		)

	hashed = bcrypt.hashpw(request.new_password.encode("utf-8"), bcrypt.gensalt())
	repo.db.users.update_one(
		{"_id": record["user_id"]}, {"$set": {"password_hash": hashed.decode("utf-8")}}
	)
	return {"detail": "Password updated. Sign in with your new password."}


@router.post("/logout")
def logout(
	credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
	current_user: AuthenticatedUser = Depends(require_current_user),
) -> dict[str, object]:
	"""Revoke the presented access token.

	Before this existed, signing out only cleared the client's copy of the token
	— the token itself stayed valid until it expired, so a stolen one could not
	be invalidated. Revocation is recorded server-side and checked on every
	subsequent request.

	Only the presented token is revoked, so signing out on one device leaves the
	user's other sessions alone.
	"""

	token = credentials.credentials if credentials else ""
	auth_settings = get_settings().auth
	try:
		payload = jwt.decode(
			token,
			auth_settings.jwt_secret,
			algorithms=[auth_settings.jwt_algorithm],
		)
	except jwt.InvalidTokenError:
		# require_current_user already validated this token, so reaching here
		# means something raced. Nothing to revoke.
		return {"revoked": False, "detail": "Token could not be read."}

	token_id = str(payload.get("jti") or "").strip()
	if not token_id:
		# Issued before revocation support existed. It stays valid until it
		# expires; there is no id to revoke it by.
		return {
			"revoked": False,
			"detail": "This token predates revocation support and will expire on its own.",
		}

	expires_at = payload.get("exp")
	try:
		expiry = (
			datetime.fromtimestamp(int(expires_at), tz=timezone.utc)
			if expires_at is not None
			else datetime.now(timezone.utc) + timedelta(hours=auth_settings.jwt_expiry_hours)
		)
		get_repository().revoke_token(
			jti=token_id,
			user_id=current_user.user_id,
			expires_at=expiry,
		)
	except Exception as exc:
		# Failing to record the revocation must be visible, not silently
		# swallowed — the caller believes they are signed out.
		_LOGGER.exception("Failed to revoke token for user %s.", current_user.user_id)
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Could not complete sign-out. Please try again.",
		) from exc

	return {"revoked": True}


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
