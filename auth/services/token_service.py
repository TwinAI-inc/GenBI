"""
JWT + password-reset token utilities.

All env vars are read lazily inside functions — never at module import time.
"""

import os
import secrets
import hashlib
from datetime import datetime, timezone, timedelta
import jwt

JWT_ALGORITHM = 'HS256'
JWT_EXPIRY_HOURS = 24


def _get_jwt_secret():
    """Read JWT secret on every call so .env changes are picked up.
    Falls back to a dev-only value ONLY when FLASK_ENV != production.
    Production will have already failed fast in config.py if JWT_SECRET_KEY is missing.
    """
    secret = os.environ.get('JWT_SECRET_KEY', '')
    if not secret:
        if os.environ.get('FLASK_ENV', 'development').lower() == 'production':
            raise RuntimeError('JWT_SECRET_KEY is required in production')
        return 'dev-only-jwt-secret-never-use-in-production'
    return secret


def _get_reset_expiry_minutes():
    """Read reset token expiry lazily."""
    return int(os.environ.get('RESET_TOKEN_EXPIRY_MINUTES', '30'))


# ── JWT helpers ──────────────────────────────────────────────────────────────

def create_access_token(user_id: str) -> str:
    payload = {
        'sub': user_id,
        'iat': datetime.now(timezone.utc),
        'exp': datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Return payload dict or None if invalid/expired."""
    try:
        return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── Password-reset token helpers ─────────────────────────────────────────────

def generate_reset_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Store the hash; send the raw token."""
    raw = secrets.token_urlsafe(48)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def reset_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=_get_reset_expiry_minutes())
