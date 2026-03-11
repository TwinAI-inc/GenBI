"""
Billing API endpoints + page routes.
"""

import os
import re

from flask import request, jsonify, send_from_directory

from extensions import db
from . import billing_bp, billing_pages_bp
from auth.routes import auth_required
from .services.subscription_service import (
    list_active_plans,
    get_active_subscription,
    create_checkout,
    switch_plan,
    cancel_subscription,
    resume_subscription,
    get_portal_url,
    process_webhook,
    verify_checkout_session,
)
from .services.entitlement_service import (
    get_all_usage_summary, get_user_plan,
    can_consume, record_usage, ALL_FEATURES,
)


# ═════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@billing_bp.route('/plans', methods=['GET'])
def plans():
    """List all active plans with entitlements."""
    all_plans = list_active_plans()
    return jsonify({'plans': [p.to_dict() for p in all_plans]})


@billing_bp.route('/subscription', methods=['GET'])
@auth_required
def subscription():
    """Get current user's subscription + usage summary."""
    user = request.current_user
    sub = get_active_subscription(user.id)
    plan_info = get_user_plan(user.id)
    usage = get_all_usage_summary(user.id)

    return jsonify({
        'subscription': sub.to_dict() if sub else None,
        'plan': plan_info,
        'usage': usage,
    })


@billing_bp.route('/switch-plan', methods=['POST'])
@auth_required
def switch_plan_endpoint():
    """
    Unified plan-switch endpoint.

    Body: { "plan_code": "free|pro|business" }

    Returns:
      - { redirect_url } if user must be sent to Stripe Checkout / Portal
      - { subscription, message } if change was applied immediately (mock / cancel)
    """
    user = request.current_user
    data = request.get_json(silent=True) or {}
    plan_code = data.get('plan_code', '').strip()

    if not plan_code:
        return jsonify({'error': 'plan_code is required.'}), 422
    if not re.match(r'^[a-z0-9_-]{1,50}$', plan_code):
        return jsonify({'error': 'Invalid plan code format.'}), 422

    result, error = switch_plan(user.id, user.email, plan_code)
    if error:
        return jsonify({'error': error}), 400

    return jsonify(result)


@billing_bp.route('/checkout', methods=['POST'])
@auth_required
def checkout():
    """Start upgrade flow to a paid plan (legacy — delegates to switch_plan)."""
    user = request.current_user
    data = request.get_json(silent=True) or {}
    plan_code = data.get('plan_code', '').strip()

    if not plan_code:
        return jsonify({'error': 'plan_code is required.'}), 422
    if not re.match(r'^[a-z0-9_-]{1,50}$', plan_code):
        return jsonify({'error': 'Invalid plan code format.'}), 422

    result, error = create_checkout(user.id, user.email, plan_code)
    if error:
        return jsonify({'error': error}), 400

    return jsonify(result)


@billing_bp.route('/checkout-status', methods=['GET'])
@auth_required
def checkout_status():
    """Verify a Stripe Checkout session after redirect back."""
    session_id = request.args.get('session_id', '').strip()
    if not session_id:
        return jsonify({'error': 'session_id is required.'}), 422

    result, error = verify_checkout_session(request.current_user.id, session_id)
    if error:
        return jsonify({'error': error}), 400
    return jsonify(result)


@billing_bp.route('/cancel', methods=['POST'])
@auth_required
def cancel():
    """Cancel subscription at period end."""
    user = request.current_user
    result, error = cancel_subscription(user.id)
    if error:
        return jsonify({'error': error}), 400
    return jsonify({'subscription': result})


@billing_bp.route('/resume', methods=['POST'])
@auth_required
def resume():
    """Resume a subscription that was set to cancel."""
    user = request.current_user
    result, error = resume_subscription(user.id)
    if error:
        return jsonify({'error': error}), 400
    return jsonify({'subscription': result})


@billing_bp.route('/portal', methods=['GET'])
@auth_required
def portal():
    """Get Stripe customer portal URL."""
    user = request.current_user
    result, error = get_portal_url(user.id)
    if error:
        return jsonify({'error': error}), 400
    return jsonify(result)


@billing_bp.route('/usage', methods=['GET'])
@auth_required
def usage():
    """Get detailed usage for current period."""
    user = request.current_user
    summary = get_all_usage_summary(user.id)
    return jsonify({'usage': summary})


@billing_bp.route('/consume', methods=['POST'])
@auth_required
def consume():
    """Check quota and record one usage unit for a feature.

    Body: { "feature_key": "export" }
    Returns 200 with { allowed, current_usage, limit_value } on success,
    or 402 with { error, upgrade_required } when quota exceeded.
    """
    user = request.current_user
    data = request.get_json(silent=True) or {}
    feature_key = data.get('feature_key', '').strip()

    if feature_key not in ALL_FEATURES:
        return jsonify({'error': 'Invalid feature key.'}), 422

    check = can_consume(user.id, feature_key, 1)
    if not check['allowed']:
        plan_info = get_user_plan(user.id)
        return jsonify({
            'error': check['reason'],
            'feature': feature_key,
            'current_usage': check['current_usage'],
            'limit_value': check['limit_value'],
            'current_plan': plan_info['plan_code'],
            'upgrade_required': True,
        }), 402

    record_usage(user.id, feature_key, 1)
    return jsonify({
        'allowed': True,
        'current_usage': check['current_usage'] + 1,
        'limit_value': check['limit_value'],
    })


@billing_bp.route('/webhook', methods=['POST'])
@billing_bp.route('/stripe/webhook', methods=['POST'])
def webhook():
    """Stripe webhook endpoint (no auth — verified by signature)."""
    try:
        payload = request.get_data(as_text=True)
        sig = request.headers.get('Stripe-Signature', '')

        success, message = process_webhook(payload, sig)
        if not success:
            return jsonify({'error': message}), 400
        return jsonify({'message': message})
    except Exception:
        return jsonify({'error': 'Webhook processing error.'}), 500


# ═════════════════════════════════════════════════════════════════════════════
# PAGE ROUTES
# ═════════════════════════════════════════════════════════════════════════════

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))


@billing_pages_bp.route('/pricing')
def pricing_page():
    """Pricing page is served via the SPA (index.html)."""
    return send_from_directory(ROOT_DIR, 'index.html')


@billing_pages_bp.route('/billing/success')
def billing_success():
    """Post-Checkout success page. SPA detects ?billing=success and polls."""
    return send_from_directory(ROOT_DIR, 'index.html')


@billing_pages_bp.route('/billing/cancel')
def billing_cancel():
    """Checkout was canceled. SPA detects ?billing=canceled."""
    return send_from_directory(ROOT_DIR, 'index.html')
