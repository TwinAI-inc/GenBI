"""
Subscription lifecycle management with Stripe + mock fallback.

Switch-plan flow:
  Free → Paid    : Stripe Checkout (new subscription)
  Paid → Paid    : Stripe Billing Portal (plan change + proration)
  Paid → Free    : Cancel at period end
"""

import os
import uuid
from datetime import datetime, timezone, timedelta

from extensions import db
from billing.models import Plan, Subscription, WebhookEvent


# ── Stripe helpers ────────────────────────────────────────────────────────

def _is_stripe_configured():
    return bool(os.environ.get('STRIPE_SECRET_KEY'))


def _get_stripe():
    """Lazy-import stripe so it's not required when in mock mode."""
    import stripe
    stripe.api_key = os.environ['STRIPE_SECRET_KEY']
    return stripe


def _base_url():
    return os.environ.get('APP_BASE_URL', 'http://localhost:8000')


# ── Plan queries ──────────────────────────────────────────────────────────

def list_active_plans():
    return Plan.query.filter_by(is_active=True).order_by(Plan.sort_order).all()


def get_plan_by_code(code):
    return Plan.query.filter_by(code=code, is_active=True).first()


def get_plan_by_stripe_price(stripe_price_id):
    """Look up a plan by its Stripe price ID (for webhook handling)."""
    return Plan.query.filter_by(stripe_price_id=stripe_price_id, is_active=True).first()


# ── Subscription queries ─────────────────────────────────────────────────

def get_active_subscription(user_id):
    return Subscription.query.filter(
        Subscription.user_id == user_id,
        Subscription.status.in_(['active', 'past_due']),
    ).first()


def get_user_plan_code(user_id):
    sub = get_active_subscription(user_id)
    if sub and sub.plan:
        return sub.plan.code
    return 'free'


# ═════════════════════════════════════════════════════════════════════════
# UNIFIED SWITCH-PLAN  (the single entry point for all plan changes)
# ═════════════════════════════════════════════════════════════════════════

def switch_plan(user_id, user_email, target_plan_code):
    """
    Handle every plan transition:
      free  → paid : Stripe Checkout (or mock instant activation)
      paid  → paid : Stripe Billing Portal (or mock instant switch)
      paid  → free : Cancel subscription at period end

    Returns (result_dict, error_string).
    result_dict may contain:
      redirect_url  — Stripe Checkout / Portal URL (client must redirect)
      subscription  — updated subscription dict (mock mode)
      action        — 'checkout' | 'portal' | 'cancel' | 'mock_switch' | 'mock_upgrade'
      message       — human-readable status
    """
    target_plan = get_plan_by_code(target_plan_code)
    if not target_plan:
        return None, 'Plan not found.'

    existing = get_active_subscription(user_id)
    current_plan_code = existing.plan.code if existing else 'free'

    # ── Same plan ─────────────────────────────────────────────────────
    if target_plan_code == current_plan_code:
        return None, 'You are already on this plan.'

    # ── PAID → FREE (cancel at period end) ────────────────────────────
    if target_plan_code == 'free':
        if not existing or current_plan_code == 'free':
            return {'action': 'noop', 'message': 'You are already on the free plan.'}, None
        return _downgrade_to_free(existing)

    # ── FREE → PAID (new subscription via Checkout) ───────────────────
    if current_plan_code == 'free' or not existing:
        if _is_stripe_configured():
            return _stripe_checkout(user_id, user_email, target_plan)
        elif existing:
            # User has an active free-plan subscription row — switch it in place
            return _mock_switch(existing, target_plan)
        else:
            return _mock_activate(user_id, target_plan)

    # ── PAID → DIFFERENT PAID (upgrade/downgrade via Portal or update) ─
    if existing.plan_id == target_plan.id:
        return None, 'You are already on this plan.'

    if _is_stripe_configured() and existing.provider == 'stripe':
        # Has a Stripe subscription → send to Billing Portal
        if existing.provider_customer_id:
            return _stripe_portal(existing)
        # Has a Stripe sub but no customer ID (shouldn't happen)
        return _stripe_update_subscription(existing, target_plan)
    else:
        # Mock mode → instant switch
        return _mock_switch(existing, target_plan)


# ═════════════════════════════════════════════════════════════════════════
# STRIPE FLOWS
# ═════════════════════════════════════════════════════════════════════════

def _stripe_checkout(user_id, user_email, plan):
    """Create Stripe Checkout Session for a new subscription."""
    stripe = _get_stripe()
    base = _base_url()

    # Reuse existing Stripe customer if we have one from a previous subscription
    customer_id = None
    prev_sub = Subscription.query.filter(
        Subscription.user_id == user_id,
        Subscription.provider == 'stripe',
        Subscription.provider_customer_id.isnot(None),
    ).order_by(Subscription.created_at.desc()).first()
    if prev_sub:
        customer_id = prev_sub.provider_customer_id

    checkout_params = dict(
        mode='subscription',
        line_items=[{'price': plan.stripe_price_id, 'quantity': 1}],
        success_url=os.environ.get('STRIPE_SUCCESS_URL', f'{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}'),
        cancel_url=os.environ.get('STRIPE_CANCEL_URL', f'{base}/billing/cancel'),
        metadata={'user_id': user_id, 'plan_code': plan.code},
    )
    if customer_id:
        checkout_params['customer'] = customer_id
    else:
        checkout_params['customer_email'] = user_email

    session = stripe.checkout.Session.create(**checkout_params)
    return {
        'action': 'checkout',
        'redirect_url': session.url,
        'session_id': session.id,
    }, None


def _stripe_portal(existing_sub):
    """Send existing Stripe subscriber to Customer Portal (plan changes + proration)."""
    stripe = _get_stripe()
    base = _base_url()

    session = stripe.billing_portal.Session.create(
        customer=existing_sub.provider_customer_id,
        return_url=f'{base}/dashboard?billing=updated',
    )
    return {
        'action': 'portal',
        'redirect_url': session.url,
    }, None


def _stripe_update_subscription(existing_sub, target_plan):
    """
    Fallback: server-side subscription update with proration.
    Used when Billing Portal is unavailable.
    """
    stripe = _get_stripe()
    sub_obj = stripe.Subscription.retrieve(existing_sub.provider_subscription_id)

    stripe.Subscription.modify(
        existing_sub.provider_subscription_id,
        items=[{
            'id': sub_obj['items']['data'][0]['id'],
            'price': target_plan.stripe_price_id,
        }],
        proration_behavior='create_prorations',
    )
    # The webhook (customer.subscription.updated) will update the DB
    return {
        'action': 'updated',
        'message': f'Switching to {target_plan.name}. This may take a moment.',
    }, None


# ═════════════════════════════════════════════════════════════════════════
# MOCK FLOWS (no Stripe configured — instant activation for development)
# ═════════════════════════════════════════════════════════════════════════

def _mock_activate(user_id, plan):
    """Immediately activate a new subscription in mock mode."""
    now = datetime.now(timezone.utc)
    sub = Subscription(
        id=str(uuid.uuid4()),
        user_id=user_id,
        plan_id=plan.id,
        status='active',
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        provider='mock',
    )
    db.session.add(sub)
    db.session.commit()
    return {
        'action': 'mock_upgrade',
        'subscription': sub.to_dict(),
        'message': f'Upgraded to {plan.name}!',
    }, None


def _mock_switch(existing_sub, target_plan):
    """Instantly switch plan in mock mode (keeps period, changes plan)."""
    existing_sub.plan_id = target_plan.id
    existing_sub.updated_at = datetime.now(timezone.utc)
    existing_sub.cancel_at_period_end = False
    existing_sub.canceled_at = None
    db.session.commit()
    return {
        'action': 'mock_switch',
        'subscription': existing_sub.to_dict(),
        'message': f'Switched to {target_plan.name}!',
    }, None


# ═════════════════════════════════════════════════════════════════════════
# DOWNGRADE TO FREE (cancel at period end)
# ═════════════════════════════════════════════════════════════════════════

def _downgrade_to_free(existing_sub):
    """Cancel the subscription at period end so user keeps access until then."""
    now = datetime.now(timezone.utc)

    if _is_stripe_configured() and existing_sub.provider == 'stripe' and existing_sub.provider_subscription_id:
        stripe = _get_stripe()
        stripe.Subscription.modify(
            existing_sub.provider_subscription_id,
            cancel_at_period_end=True,
        )

    existing_sub.cancel_at_period_end = True
    existing_sub.canceled_at = now
    existing_sub.updated_at = now
    db.session.commit()

    end_date = existing_sub.current_period_end.strftime('%b %d, %Y') if existing_sub.current_period_end else 'end of period'
    return {
        'action': 'cancel',
        'message': f'Your plan will downgrade to Free on {end_date}.',
        'subscription': existing_sub.to_dict(),
    }, None


# ═════════════════════════════════════════════════════════════════════════
# LEGACY ENTRY POINTS (kept for backward compat with existing routes)
# ═════════════════════════════════════════════════════════════════════════

def create_checkout(user_id, user_email, plan_code):
    """Legacy: redirect to switch_plan."""
    return switch_plan(user_id, user_email, plan_code)


def cancel_subscription(user_id):
    sub = get_active_subscription(user_id)
    if not sub:
        return None, 'No active subscription to cancel.'
    if sub.plan.code == 'free':
        return None, 'Cannot cancel the free plan.'
    return _downgrade_to_free(sub)


def resume_subscription(user_id):
    sub = get_active_subscription(user_id)
    if not sub:
        return None, 'No active subscription.'
    if not sub.cancel_at_period_end:
        return None, 'Subscription is not set to cancel.'

    if _is_stripe_configured() and sub.provider == 'stripe' and sub.provider_subscription_id:
        stripe = _get_stripe()
        stripe.Subscription.modify(
            sub.provider_subscription_id,
            cancel_at_period_end=False,
        )

    sub.cancel_at_period_end = False
    sub.canceled_at = None
    sub.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return sub.to_dict(), None


def get_portal_url(user_id):
    sub = get_active_subscription(user_id)
    if not sub:
        return None, 'No active subscription.'

    if _is_stripe_configured() and sub.provider == 'stripe' and sub.provider_customer_id:
        stripe = _get_stripe()
        base = _base_url()
        session = stripe.billing_portal.Session.create(
            customer=sub.provider_customer_id,
            return_url=f'{base}/dashboard?billing=updated',
        )
        return {'portal_url': session.url}, None

    return {'portal_url': None, 'mock': True, 'message': 'Billing portal not available in mock mode.'}, None


# ═════════════════════════════════════════════════════════════════════════
# STRIPE WEBHOOK HANDLING
# ═════════════════════════════════════════════════════════════════════════

def process_webhook(payload, sig_header):
    if not _is_stripe_configured():
        return False, 'Stripe not configured.'

    stripe = _get_stripe()
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return False, 'Invalid webhook signature.'

    # Idempotency check
    if WebhookEvent.query.get(event['id']):
        return True, 'Already processed.'

    event_type = event['type']
    data = event['data']['object']

    if event_type == 'checkout.session.completed':
        _handle_checkout_completed(data)
    elif event_type == 'customer.subscription.updated':
        _handle_subscription_updated(data)
    elif event_type == 'customer.subscription.deleted':
        _handle_subscription_deleted(data)
    elif event_type == 'invoice.paid':
        _handle_invoice_paid(data)
    elif event_type == 'invoice.payment_failed':
        _handle_invoice_failed(data)

    # Mark as processed
    db.session.add(WebhookEvent(id=event['id'], event_type=event_type))
    db.session.commit()
    return True, 'OK'


def _handle_checkout_completed(session_data):
    """New subscription created via Checkout."""
    user_id = session_data.get('metadata', {}).get('user_id')
    plan_code = session_data.get('metadata', {}).get('plan_code')
    if not user_id or not plan_code:
        return

    plan = get_plan_by_code(plan_code)
    if not plan:
        return

    stripe = _get_stripe()
    stripe_sub_id = session_data.get('subscription')
    customer_id = session_data.get('customer')

    # Fetch authoritative subscription details from Stripe
    stripe_sub = stripe.Subscription.retrieve(stripe_sub_id) if stripe_sub_id else None

    now = datetime.now(timezone.utc)
    period_start = now
    period_end = now + timedelta(days=30)
    if stripe_sub:
        period_start = datetime.fromtimestamp(stripe_sub['current_period_start'], tz=timezone.utc)
        period_end = datetime.fromtimestamp(stripe_sub['current_period_end'], tz=timezone.utc)

    # Expire any existing active subscription for this user
    existing = get_active_subscription(user_id)
    if existing:
        existing.status = 'expired'
        existing.updated_at = now
        db.session.flush()

    sub = Subscription(
        id=str(uuid.uuid4()),
        user_id=user_id,
        plan_id=plan.id,
        status='active',
        current_period_start=period_start,
        current_period_end=period_end,
        provider='stripe',
        provider_customer_id=customer_id,
        provider_subscription_id=stripe_sub_id,
    )
    db.session.add(sub)
    db.session.flush()


def _handle_subscription_updated(sub_data):
    """
    Handle subscription updates from Stripe. This covers:
    - Status changes (active, past_due, canceled)
    - Plan changes via Billing Portal (price/product swap)
    - cancel_at_period_end toggling
    - Period renewal
    """
    provider_sub_id = sub_data.get('id')
    sub = Subscription.query.filter_by(provider_subscription_id=provider_sub_id).first()
    if not sub:
        return

    now = datetime.now(timezone.utc)

    # ── Status mapping ────────────────────────────────────────────────
    status_map = {
        'active': 'active',
        'past_due': 'past_due',
        'canceled': 'expired',
        'unpaid': 'past_due',
        'incomplete': 'active',
        'incomplete_expired': 'expired',
        'trialing': 'active',
    }
    stripe_status = sub_data.get('status', 'active')
    sub.status = status_map.get(stripe_status, 'active')

    # ── Plan change detection (user switched via Billing Portal) ──────
    items = sub_data.get('items', {}).get('data', [])
    if items:
        new_price_id = items[0].get('price', {}).get('id')
        if new_price_id:
            new_plan = get_plan_by_stripe_price(new_price_id)
            if new_plan and new_plan.id != sub.plan_id:
                sub.plan_id = new_plan.id

    # ── Period + cancellation ─────────────────────────────────────────
    sub.cancel_at_period_end = sub_data.get('cancel_at_period_end', False)
    if sub.cancel_at_period_end and not sub.canceled_at:
        sub.canceled_at = now
    elif not sub.cancel_at_period_end:
        sub.canceled_at = None

    if sub_data.get('current_period_start'):
        sub.current_period_start = datetime.fromtimestamp(
            sub_data['current_period_start'], tz=timezone.utc
        )
    if sub_data.get('current_period_end'):
        sub.current_period_end = datetime.fromtimestamp(
            sub_data['current_period_end'], tz=timezone.utc
        )

    sub.updated_at = now
    db.session.flush()


def _handle_subscription_deleted(sub_data):
    """Subscription fully canceled/expired on Stripe."""
    provider_sub_id = sub_data.get('id')
    sub = Subscription.query.filter_by(provider_subscription_id=provider_sub_id).first()
    if sub:
        sub.status = 'expired'
        sub.canceled_at = sub.canceled_at or datetime.now(timezone.utc)
        sub.updated_at = datetime.now(timezone.utc)
        db.session.flush()


def _handle_invoice_paid(invoice_data):
    """Invoice successfully paid — ensure subscription is marked active."""
    stripe_sub_id = invoice_data.get('subscription')
    if not stripe_sub_id:
        return
    sub = Subscription.query.filter_by(provider_subscription_id=stripe_sub_id).first()
    if sub and sub.status != 'active':
        sub.status = 'active'
        sub.updated_at = datetime.now(timezone.utc)
        db.session.flush()


def _handle_invoice_failed(invoice_data):
    """Invoice payment failed — mark subscription as past_due."""
    stripe_sub_id = invoice_data.get('subscription')
    if not stripe_sub_id:
        return
    sub = Subscription.query.filter_by(provider_subscription_id=stripe_sub_id).first()
    if sub:
        sub.status = 'past_due'
        sub.updated_at = datetime.now(timezone.utc)
        db.session.flush()
