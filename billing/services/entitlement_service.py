"""
Entitlement checks and enforcement decorators.

Calls PL/pgSQL functions for correctness; falls back to ORM if functions
haven't been created yet (e.g., during initial migration).
"""

from datetime import datetime, timezone
from functools import wraps

from flask import request, jsonify

from extensions import db


# ── Feature key constants ──────────────────────────────────────────────────

FEATURE_DOCUMENT_UPLOADS = 'document_uploads'
FEATURE_AI_QUERIES = 'ai_queries'
FEATURE_CUSTOM_CHARTS = 'custom_charts'
FEATURE_PREMIUM_THEMES = 'premium_themes'
FEATURE_EXPORT = 'export'
FEATURE_PRIORITY_SUPPORT = 'priority_support'
FEATURE_SAVED_PROJECTS = 'saved_projects'

ALL_FEATURES = [
    FEATURE_DOCUMENT_UPLOADS,
    FEATURE_AI_QUERIES,
    FEATURE_CUSTOM_CHARTS,
    FEATURE_PREMIUM_THEMES,
    FEATURE_EXPORT,
    FEATURE_PRIORITY_SUPPORT,
    FEATURE_SAVED_PROJECTS,
]


def _current_period_key():
    return datetime.now(timezone.utc).strftime('%Y-%m')


def get_user_plan(user_id):
    """Return (plan_id, plan_code, plan_name) for a user."""
    row = db.session.execute(
        db.text('SELECT plan_id, plan_code, plan_name FROM get_user_plan(:uid)'),
        {'uid': user_id},
    ).fetchone()
    if row:
        return {'plan_id': row[0], 'plan_code': row[1], 'plan_name': row[2]}
    return {'plan_id': None, 'plan_code': 'free', 'plan_name': 'Free'}


def get_entitlement(user_id, feature_key):
    """Return (is_enabled, limit_value) for a user+feature."""
    row = db.session.execute(
        db.text('SELECT is_enabled, limit_value FROM get_entitlement(:uid, :fk)'),
        {'uid': user_id, 'fk': feature_key},
    ).fetchone()
    if row:
        return {'is_enabled': row[0], 'limit_value': row[1]}
    return {'is_enabled': False, 'limit_value': 0}


def get_usage(user_id, feature_key, period_key=None):
    """Return total usage for the current period."""
    pk = period_key or _current_period_key()
    row = db.session.execute(
        db.text('SELECT get_usage_for_period(:uid, :fk, :pk)'),
        {'uid': user_id, 'fk': feature_key, 'pk': pk},
    ).fetchone()
    return row[0] if row else 0


def can_consume(user_id, feature_key, amount=1):
    """Check if user can consume amount of feature. Returns dict."""
    row = db.session.execute(
        db.text('SELECT allowed, reason, current_usage, limit_val FROM can_consume(:uid, :fk, :amt)'),
        {'uid': user_id, 'fk': feature_key, 'amt': amount},
    ).fetchone()
    if row:
        return {
            'allowed': row[0],
            'reason': row[1],
            'current_usage': row[2],
            'limit_value': row[3],
        }
    return {'allowed': False, 'reason': 'Unable to check entitlement', 'current_usage': 0, 'limit_value': 0}


def record_usage(user_id, feature_key, amount=1):
    """Record usage event. Returns event id."""
    row = db.session.execute(
        db.text('SELECT record_usage(:uid, :fk, :amt)'),
        {'uid': user_id, 'fk': feature_key, 'amt': amount},
    ).fetchone()
    db.session.commit()
    return row[0] if row else None


def get_all_usage_summary(user_id):
    """Return usage summary for all features for the current period."""
    period = _current_period_key()
    summary = {}
    for fk in ALL_FEATURES:
        ent = get_entitlement(user_id, fk)
        usage = get_usage(user_id, fk, period)
        summary[fk] = {
            'is_enabled': ent['is_enabled'],
            'limit_value': ent['limit_value'],
            'current_usage': usage,
        }
    return summary


# ── Decorators ─────────────────────────────────────────────────────────────

def requires_entitlement(feature_key):
    """Decorator: block access if feature is not enabled on user's plan."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = getattr(request, 'current_user', None)
            if not user:
                return jsonify({'error': 'Authentication required.'}), 401

            ent = get_entitlement(user.id, feature_key)
            if not ent['is_enabled']:
                plan = get_user_plan(user.id)
                return jsonify({
                    'error': f'This feature requires a higher plan.',
                    'feature': feature_key,
                    'current_plan': plan['plan_code'],
                    'upgrade_required': True,
                }), 403

            return f(*args, **kwargs)
        return decorated
    return decorator


def requires_quota(feature_key, amount=1):
    """Decorator: check quota before proceeding. Records usage after success."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = getattr(request, 'current_user', None)
            if not user:
                return jsonify({'error': 'Authentication required.'}), 401

            check = can_consume(user.id, feature_key, amount)
            if not check['allowed']:
                plan = get_user_plan(user.id)
                return jsonify({
                    'error': check['reason'],
                    'feature': feature_key,
                    'current_usage': check['current_usage'],
                    'limit_value': check['limit_value'],
                    'current_plan': plan['plan_code'],
                    'upgrade_required': True,
                }), 402

            # Run the actual endpoint
            response = f(*args, **kwargs)

            # Record usage only on success (2xx)
            status_code = response[1] if isinstance(response, tuple) else response.status_code
            if 200 <= status_code < 300:
                record_usage(user.id, feature_key, amount)

            return response
        return decorated
    return decorator
