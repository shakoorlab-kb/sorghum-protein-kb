"""One account: the admin. A signed, HTTP-only session cookie after login."""
from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from .config import settings

COOKIE = "si_session"
_secret = settings.session_secret or secrets.token_hex(32)  # unset = sessions end on restart
_signer = TimestampSigner(_secret, salt="sorghum-intel-session")
_failures: dict[str, deque] = defaultdict(deque)


def check_password(username: str, password: str, ip: str) -> bool:
    window = _failures[ip]
    now = time.time()
    while window and now - window[0] > 300:
        window.popleft()
    if len(window) >= 8:
        raise HTTPException(429, "too many failed logins; wait a few minutes")
    ok = bool(settings.admin_password) and hmac.compare_digest(username, settings.admin_user) \
        and hmac.compare_digest(password, settings.admin_password)
    if not ok:
        window.append(now)
    return ok


def issue() -> str:
    return _signer.sign(settings.admin_user.encode()).decode()


def current_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        user = _signer.unsign(token, max_age=settings.session_days * 86400).decode()
    except (BadSignature, SignatureExpired):
        return None
    return user if user == settings.admin_user else None


def require_admin(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "login required")
    return user
