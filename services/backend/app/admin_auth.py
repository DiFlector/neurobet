"""Server-side authentication for administrative review actions."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request

COOKIE_NAME = "neurobet_admin_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


def _credentials() -> tuple[str, str, str]:
    username = os.getenv("ADMIN_USERNAME", "").strip()
    password_hash = os.getenv("ADMIN_PASSWORD_SHA256", "").strip().lower()
    secret = os.getenv("ADMIN_SESSION_SECRET", "")
    if not username or len(password_hash) != 64 or not secret:
        raise HTTPException(status_code=503, detail="Admin authentication is not configured")
    return username, password_hash, secret


def authenticate(username: str, password: str) -> bool:
    configured_user, expected_hash, _ = _credentials()
    actual_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(username.strip(), configured_user) and hmac.compare_digest(actual_hash, expected_hash)


def _signature(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_session(username: str) -> str:
    _, _, secret = _credentials()
    body = json.dumps({"u": username, "exp": int(time.time()) + SESSION_TTL_SECONDS,
                       "n": secrets.token_urlsafe(12)}, separators=(",", ":")).encode("utf-8")
    payload = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    return f"{payload}.{_signature(payload, secret)}"


def session_username(token: str | None) -> str | None:
    if not token:
        return None
    try:
        configured_user, _, secret = _credentials()
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _signature(payload, secret)):
            return None
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        data = json.loads(raw)
        if data.get("u") != configured_user or int(data.get("exp", 0)) <= int(time.time()):
            return None
        return configured_user
    except (HTTPException, ValueError, TypeError, json.JSONDecodeError):
        return None


def require_admin(request: Request) -> str:
    # Fail closed if the deployment has not provisioned the server-side secret.
    _credentials()
    username = session_username(request.cookies.get(COOKIE_NAME))
    if not username:
        raise HTTPException(status_code=401, detail="Admin session required")
    return username


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=os.getenv("ADMIN_COOKIE_SECURE", "true").strip().lower() not in {"0", "false", "no"},
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=os.getenv("ADMIN_COOKIE_SECURE", "true").strip().lower() not in {"0", "false", "no"},
        samesite="strict",
    )
