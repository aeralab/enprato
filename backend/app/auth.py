from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Any

from fastapi import Depends, HTTPException, Request

from . import db

COOKIE_NAME = "enprato_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.urlsafe_b64decode(salt), int(rounds))
        return hmac.compare_digest(base64.urlsafe_b64decode(expected), actual)
    except (ValueError, TypeError):
        return False


def current_user(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(COOKIE_NAME, "")
    row = db.user_for_token(token) if token else None
    return dict(row) if row else None


def require_user(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "请先登录")
    return user


def auth_required() -> bool:
    return os.environ.get("ENPRATO_REQUIRE_AUTH", "").strip().lower() in {"1", "true", "yes"}


def require_user_or_local(request: Request) -> dict[str, Any]:
    """云端强制登录；本机默认可匿名，走 lan-local + license.json。"""
    user = current_user(request)
    if user:
        return dict(user)
    if auth_required():
        raise HTTPException(401, "请先登录")
    return {"id": "lan-local", "email": "", "status": "active"}


def cookie_secure() -> bool:
    return os.environ.get("ENPRATO_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"}
