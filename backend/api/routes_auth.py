"""Custom auth routes backed by MongoDB and self-issued JWTs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

from backend.api.auth import (
	AuthenticatedUser,
	get_optional_current_user,
	require_current_user,
	serialize_authenticated_user,
)
from backend.config import get_settings
from backend.database.mongo_client import get_repository

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequest(BaseModel):
	email: EmailStr
	password: str


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


@router.post("/signup")
def signup(request: AuthRequest) -> dict[str, str]:
	repo = get_repository()
	existing = repo.db.users.find_one({"email": str(request.email)})
	if existing:
		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="User with this email already exists.",
		)

	hashed = bcrypt.hashpw(request.password.encode("utf-8"), bcrypt.gensalt())
	user = repo.insert_one(
		"users",
		{
			"email": str(request.email),
			"password_hash": hashed.decode("utf-8"),
			"created_at": datetime.now(timezone.utc).isoformat(),
		}
	)

	token = _create_jwt(user["id"], user["email"])
	return {"access_token": token}


@router.post("/login")
def login(request: AuthRequest) -> dict[str, str]:
	repo = get_repository()
	user = repo.db.users.find_one({"email": str(request.email)})
	if not user:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid email or password.",
		)

	if not bcrypt.checkpw(
		request.password.encode("utf-8"),
		user["password_hash"].encode("utf-8")
	):
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid email or password.",
		)

	token = _create_jwt(str(user["_id"]), user["email"])
	return {"access_token": token}


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


__all__ = ["router"]