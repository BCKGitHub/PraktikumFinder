"""Authentication, CSRF, rate limiting and security headers."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import get_settings
from .models import AdminUser, utcnow

# argon2-cffi defaults to Argon2id with OWASP-recommended parameters.
_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def new_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    return bool(secret) and len(code) == 6 and pyotp.TOTP(secret).verify(code, valid_window=1)


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="PraktikumFinder")


# --------------------------------------------------------------------------
# Admin session helpers
# --------------------------------------------------------------------------

ADMIN_SESSION_MAX_AGE = timedelta(hours=8)


def _admin_key(admin: AdminUser) -> str:
    """Ties a session to this exact account: a new account that reuses the id (fresh database) or a
    password change makes older session cookies invalid."""
    raw = f"{admin.id}:{admin.email}:{admin.password_hash}".encode()
    return hashlib.sha256(raw).hexdigest()


def login_admin_session(request: Request, admin: AdminUser, mfa_ok: bool) -> None:
    # Rotate the CSRF token on privilege change.
    request.session.clear()
    request.session["admin_id"] = admin.id
    request.session["admin_key"] = _admin_key(admin)
    request.session["mfa_ok"] = mfa_ok
    request.session["admin_since"] = int(time.time())
    request.session["csrf"] = secrets.token_urlsafe(32)


def current_admin(request: Request, db: Session, require_mfa: bool = True) -> AdminUser | None:
    admin_id = request.session.get("admin_id")
    since = request.session.get("admin_since", 0)
    if not admin_id or time.time() - since > ADMIN_SESSION_MAX_AGE.total_seconds():
        return None
    if require_mfa and not request.session.get("mfa_ok"):
        return None
    admin = db.get(AdminUser, admin_id)
    if admin is None or not admin.active:
        return None
    if not secrets.compare_digest(str(request.session.get("admin_key", "")), _admin_key(admin)):
        return None
    if require_mfa and not admin.totp_enabled:
        return None
    return admin


class AdminRequired(Exception):
    """Raised when an admin route is accessed without a valid session."""


def require_admin(request: Request, db: Session) -> AdminUser:
    admin = current_admin(request, db)
    if admin is None:
        raise AdminRequired()
    return admin


def register_failed_login(db: Session, admin: AdminUser) -> None:
    admin.failed_logins += 1
    if admin.failed_logins >= MAX_FAILED_LOGINS:
        admin.locked_until = utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        admin.failed_logins = 0
    db.commit()


def is_locked(admin: AdminUser) -> bool:
    return admin.locked_until is not None and admin.locked_until > utcnow()


# --------------------------------------------------------------------------
# CSRF (synchronizer token stored in the signed session cookie)
# --------------------------------------------------------------------------


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request) -> None:
    form = await request.form()
    sent = form.get("csrf_token") or request.headers.get("x-csrf-token", "")
    expected = request.session.get("csrf")
    if not expected or not isinstance(sent, str) or not secrets.compare_digest(sent, expected):
        raise HTTPException(status_code=400, detail="Ungültiges oder abgelaufenes Formular. Bitte lade die Seite neu.")


# --------------------------------------------------------------------------
# Rate limiting (in-memory sliding window; fine for a single-process MVP)
# --------------------------------------------------------------------------


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record a hit; return False when the limit is exceeded."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()

# bucket -> (limit, window seconds)
RATE_LIMITS = {
    "login": (10, 300),
    "submit": (5, 3600),
    "report": (10, 3600),
    "search": (120, 60),
    "docx": (30, 300),
    "event": (300, 60),
}


def client_ip(request: Request) -> str:
    # Behind the bundled reverse proxy (Caddy) the real IP is in X-Forwarded-For.
    fwd = request.headers.get("x-forwarded-for")
    if fwd and get_settings().is_production:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request, bucket: str) -> None:
    limit, window = RATE_LIMITS[bucket]
    if not rate_limiter.hit(f"{bucket}:{client_ip(request)}", limit, window):
        raise HTTPException(status_code=429, detail="Zu viele Anfragen. Bitte warte kurz und versuche es erneut.")


# --------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; object-src 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next) -> Response:
        response = await call_next(request)
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=(), interest-cohort=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if get_settings().secure_cookies:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/admin"):
            h["Cache-Control"] = "no-store"
        return response
