"""
Auth API endpoints + page routes.
"""

import logging
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from functools import wraps
from html import escape as html_escape

from flask import request, jsonify, send_from_directory
import bcrypt as _bcrypt
import requests as _requests

logger = logging.getLogger(__name__)

# Precomputed dummy hash for constant-time login checks (prevents timing attacks)
_DUMMY_HASH = _bcrypt.hashpw(b'timing-safe-dummy', _bcrypt.gensalt(rounds=12)).decode()


def _hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(rounds=12)).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return _bcrypt.checkpw(password.encode(), hashed.encode())


def _sanitize_name(name: str) -> str:
    """Strip HTML/script tags from user-supplied names."""
    cleaned = re.sub(r'<[^>]*>', '', name).strip()
    return cleaned[:120] if cleaned else 'User'


from extensions import db
from . import auth_bp, pages_bp
from .models import User, PasswordResetToken
from .schemas import validate_signup, validate_login, validate_reset_password, validate_otp, validate_set_password
from .services.token_service import (
    create_access_token, decode_access_token,
    generate_reset_token, hash_reset_token, reset_token_expiry,
)
from .services.email_provider import get_email_provider
from .services.otp_service import generate_otp, hash_otp, verify_otp, mask_email


# ── Rate limiting (applied in server.py via flask-limiter) ───────────────────
# The actual limiter instance is created in server.py and applied to these
# endpoints there, so this module stays decoupled from the limiter config.


# ── Auth decorator ───────────────────────────────────────────────────────────

def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Authentication required.'}), 401
        token = auth_header[7:]
        payload = decode_access_token(token)
        if not payload:
            return jsonify({'error': 'Invalid or expired token.'}), 401
        user = db.session.get(User, payload['sub'])
        if not user:
            return jsonify({'error': 'User not found.'}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


# ── OTP helper ───────────────────────────────────────────────────────────────

def _generate_and_send_otp(user):
    """Generate OTP, store hash on user, send via email provider."""
    otp = generate_otp()
    user.otp_hash = hash_otp(otp)
    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    user.otp_attempts = 0
    db.session.flush()

    provider = get_email_provider()
    provider.send_otp(user.email, otp, user.name)


# ── Free plan auto-assign helper ─────────────────────────────────────────────

def _assign_free_plan(user_id):
    """Create a free-plan Subscription for a new user."""
    from billing.models import Plan, Subscription
    free_plan = Plan.query.filter_by(code='free', is_active=True).first()
    if not free_plan:
        return
    now = datetime.now(timezone.utc)
    sub = Subscription(
        id=str(uuid.uuid4()),
        user_id=user_id,
        plan_id=free_plan.id,
        status='active',
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        provider='mock',
    )
    db.session.add(sub)
    db.session.flush()


# ═════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@auth_bp.route('/signup', methods=['POST'])
def signup():
    data = request.get_json(silent=True) or {}
    errors = validate_signup(data)
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 422

    email = data['email'].strip().lower()
    existing = User.query.filter_by(email=email).first()
    if existing:
        return jsonify({'error': 'An account with this email already exists.'}), 409

    user = User(
        name=_sanitize_name(data['name']),
        email=email,
        password_hash=_hash_password(data['password']),
        auth_provider='email',
        email_verified=False,
    )
    db.session.add(user)
    db.session.flush()

    # Generate & send OTP
    _generate_and_send_otp(user)

    # Auto-assign free billing plan
    _assign_free_plan(user.id)

    db.session.commit()

    token = create_access_token(user.id)
    return jsonify({
        'token': token,
        'user': user.to_dict(),
        'email_verification_required': True,
    }), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    errors = validate_login(data)
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 422

    email = data['email'].strip().lower()
    user = User.query.filter_by(email=email).first()

    # Google-only accounts have no password
    if user and user.auth_provider == 'google' and not user.has_password:
        return jsonify({'error': 'This account uses Google Sign-In. Please log in with Google.'}), 400

    # Constant-time check: always run bcrypt even if user doesn't exist (prevents timing attacks)
    if user and user.password_hash:
        pw_ok = _verify_password(data['password'], user.password_hash)
    else:
        _bcrypt.checkpw(data['password'].encode(), _DUMMY_HASH.encode())
        pw_ok = False

    if not pw_ok:
        logger.warning('Failed login attempt for %s', mask_email(email))
        return jsonify({'error': 'Invalid email or password.'}), 401

    token = create_access_token(user.id)

    # If email not verified, re-send OTP
    if not user.email_verified:
        _generate_and_send_otp(user)
        db.session.commit()
        return jsonify({
            'token': token,
            'user': user.to_dict(),
            'email_verification_required': True,
        })

    return jsonify({'token': token, 'user': user.to_dict()})


@auth_bp.route('/me', methods=['GET'])
@auth_required
def me():
    return jsonify({'user': request.current_user.to_dict()})


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """Client-side JWT auth — just confirm logout. Token is cleared on client."""
    return jsonify({'message': 'Logged out successfully.'})


# ── OTP endpoints ────────────────────────────────────────────────────────────

@auth_bp.route('/send-otp', methods=['POST'])
@auth_required
def send_otp():
    user = request.current_user
    _generate_and_send_otp(user)
    db.session.commit()
    return jsonify({'message': 'Verification code sent.'})


@auth_bp.route('/verify-otp', methods=['POST'])
@auth_required
def verify_otp_endpoint():
    data = request.get_json(silent=True) or {}
    errors = validate_otp(data)
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 422

    user = request.current_user

    if user.otp_attempts >= 5:
        logger.warning('OTP lockout for user %s (5 failed attempts)', user.id)
        return jsonify({'error': 'Too many attempts. Please request a new code.'}), 429

    if not user.otp_expires_at or user.otp_expires_at < datetime.now(timezone.utc):
        return jsonify({'error': 'Code expired. Please request a new one.'}), 400

    user.otp_attempts += 1

    if not user.otp_hash or not verify_otp(data['otp'].strip(), user.otp_hash):
        logger.warning('Failed OTP attempt %d for user %s', user.otp_attempts, user.id)
        db.session.commit()
        return jsonify({'error': 'Invalid verification code.'}), 400

    # Success
    user.email_verified = True
    user.otp_hash = None
    user.otp_expires_at = None
    user.otp_attempts = 0
    db.session.commit()

    return jsonify({'verified': True, 'user': user.to_dict()})


# ── Google OAuth endpoint ────────────────────────────────────────────────────

@auth_bp.route('/google', methods=['POST'])
def google_auth():
    data = request.get_json(silent=True) or {}
    code = data.get('code')
    redirect_uri = data.get('redirect_uri')

    if not code or not redirect_uri:
        return jsonify({'error': 'Authorization code and redirect_uri are required.'}), 400

    # Validate redirect_uri against server-side whitelist (comma-separated env var)
    raw_uris = os.environ.get('GOOGLE_OAUTH_REDIRECT_URI', '')
    allowed_uris = {u.strip() for u in raw_uris.split(',') if u.strip()} if raw_uris else set()
    # Always allow the current host as fallback
    allowed_uris.add(request.host_url.rstrip('/') + '/auth/google/callback')
    if redirect_uri not in allowed_uris:
        logger.warning('OAuth redirect_uri mismatch: got %s', redirect_uri)
        return jsonify({'error': 'Invalid redirect URI.'}), 400

    client_id = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', '')
    client_secret = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        return jsonify({'error': 'Google OAuth is not configured.'}), 500

    # Exchange authorization code for tokens
    token_resp = _requests.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }, timeout=10)

    if not token_resp.ok:
        return jsonify({'error': 'Failed to exchange authorization code.'}), 400

    tokens = token_resp.json()
    id_token_str = tokens.get('id_token')
    if not id_token_str:
        return jsonify({'error': 'No ID token received from Google.'}), 400

    # Decode ID token (verify with Google's tokeninfo endpoint)
    verify_resp = _requests.get(
        f'https://oauth2.googleapis.com/tokeninfo?id_token={id_token_str}',
        timeout=10,
    )
    if not verify_resp.ok:
        return jsonify({'error': 'Invalid Google ID token.'}), 400

    id_info = verify_resp.json()
    google_sub = id_info.get('sub')
    google_email = (id_info.get('email') or '').strip().lower()
    google_name = _sanitize_name(id_info.get('name') or google_email.split('@')[0])
    google_picture = id_info.get('picture')

    if not google_sub:
        return jsonify({'error': 'Invalid Google ID token.'}), 400
    if not google_email or '@' not in google_email:
        return jsonify({'error': 'Could not retrieve email from Google.'}), 400

    # Check for existing user by google_id
    user = User.query.filter_by(google_id=google_sub).first()
    if not user:
        # Check by email
        user = User.query.filter_by(email=google_email).first()

    if user:
        # Existing user — link Google if needed
        if not user.google_id:
            user.google_id = google_sub
        if google_picture:
            user.avatar_url = google_picture
        if not user.email_verified:
            user.email_verified = True
    else:
        # New user via Google
        user = User(
            name=google_name,
            email=google_email,
            auth_provider='google',
            email_verified=True,
            google_id=google_sub,
            avatar_url=google_picture,
            password_hash=None,
        )
        db.session.add(user)
        db.session.flush()
        _assign_free_plan(user.id)

    db.session.commit()

    token = create_access_token(user.id)
    return jsonify({'token': token, 'user': user.to_dict()})


# ── Set password (for Google-only users) ─────────────────────────────────────

@auth_bp.route('/set-password', methods=['POST'])
@auth_required
def set_password():
    data = request.get_json(silent=True) or {}
    errors = validate_set_password(data)
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 422

    user = request.current_user
    user.password_hash = _hash_password(data['password'])
    db.session.commit()

    return jsonify({'message': 'Password set successfully.', 'user': user.to_dict()})


# ── Password reset (unchanged) ───────────────────────────────────────────────

@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """Always returns 200 to prevent account enumeration."""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    generic_msg = 'If an account with that email exists, we sent a reset link.'

    if not email:
        return jsonify({'message': generic_msg})

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'message': generic_msg})

    # Invalidate any existing unused tokens for this user
    PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).delete()

    raw_token, token_hash = generate_reset_token()
    reset_row = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=reset_token_expiry(),
    )
    db.session.add(reset_row)
    db.session.commit()

    base_url = os.environ.get('APP_BASE_URL', 'http://localhost:8000')
    reset_link = f'{base_url}/reset-password?token={raw_token}'

    provider = get_email_provider()
    provider.send_password_reset(user.email, reset_link, user.name)

    return jsonify({'message': generic_msg})


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json(silent=True) or {}
    errors = validate_reset_password(data)
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 422

    token_hash = hash_reset_token(data['token'].strip())
    reset_row = PasswordResetToken.query.filter_by(token_hash=token_hash).first()

    if not reset_row:
        return jsonify({'error': 'Invalid or expired reset link.'}), 400
    if reset_row.is_used:
        return jsonify({'error': 'This reset link has already been used.'}), 400
    if reset_row.is_expired:
        return jsonify({'error': 'This reset link has expired. Please request a new one.'}), 400

    user = db.session.get(User, reset_row.user_id)
    if not user:
        return jsonify({'error': 'User not found.'}), 400

    user.password_hash = _hash_password(data['new_password'])
    reset_row.used_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({'message': 'Password reset successfully. You can now log in.'})


# ═════════════════════════════════════════════════════════════════════════════
# PAGE ROUTES (serve static HTML auth pages)
# ═════════════════════════════════════════════════════════════════════════════

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')


@pages_bp.route('/login')
def login_page():
    return send_from_directory(TEMPLATES_DIR, 'login.html')


@pages_bp.route('/signup')
def signup_page():
    return send_from_directory(TEMPLATES_DIR, 'signup.html')


@pages_bp.route('/forgot-password')
def forgot_password_page():
    return send_from_directory(TEMPLATES_DIR, 'forgot_password.html')


@pages_bp.route('/reset-password')
def reset_password_page():
    return send_from_directory(TEMPLATES_DIR, 'reset_password.html')


@pages_bp.route('/verify-email')
def verify_email_page():
    return send_from_directory(TEMPLATES_DIR, 'verify_email.html')


@pages_bp.route('/auth/google/callback')
def google_callback_page():
    """Serves the login page which handles the Google OAuth callback via JS."""
    return send_from_directory(TEMPLATES_DIR, 'login.html')
