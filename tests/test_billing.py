"""
Billing tests — validates plan limits, payment-gated upgrades, and entitlements.
Run: python3 -m pytest tests/test_billing.py -v
"""

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DATABASE_URL', 'postgresql://localhost/genbi_auth')
os.environ.setdefault('JWT_SECRET_KEY', 'test-secret')
os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret')
os.environ.setdefault('EMAIL_PROVIDER', 'console')
# Ensure no Stripe key in test env (mock mode should be blocked)
os.environ.pop('STRIPE_SECRET_KEY', None)

from server import create_app
from extensions import db


@pytest.fixture(scope='module')
def app():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        yield app


@pytest.fixture(scope='module')
def client(app):
    return app.test_client()


@pytest.fixture(scope='module')
def test_user(app):
    """Create a test user for billing tests."""
    from auth.models import User
    import bcrypt as _bcrypt

    with app.app_context():
        email = f'billing-test-{uuid.uuid4().hex[:8]}@test.com'
        user = User(
            id=str(uuid.uuid4()),
            name='Billing Tester',
            email=email,
            password_hash=_bcrypt.hashpw(b'TestPass123', _bcrypt.gensalt()).decode(),
        )
        db.session.add(user)
        db.session.commit()

        # Get JWT token
        from auth.services.token_service import create_access_token
        token = create_access_token(user.id)
        return {'user': user, 'token': token, 'email': email}


class TestPlansAPI:
    def test_list_plans(self, client):
        resp = client.get('/api/billing/plans')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'plans' in data
        plans = data['plans']
        assert len(plans) >= 3
        codes = [p['code'] for p in plans]
        assert 'free' in codes
        assert 'pro' in codes
        assert 'business' in codes

    def test_plans_have_entitlements(self, client):
        resp = client.get('/api/billing/plans')
        plans = resp.get_json()['plans']
        for plan in plans:
            assert 'entitlements' in plan
            assert 'ai_queries' in plan['entitlements']
            assert 'document_uploads' in plan['entitlements']

    def test_free_plan_has_limits(self, client):
        resp = client.get('/api/billing/plans')
        plans = resp.get_json()['plans']
        free = next(p for p in plans if p['code'] == 'free')
        assert free['price_cents'] == 0
        assert free['entitlements']['ai_queries']['limit_value'] == 50
        assert free['entitlements']['document_uploads']['limit_value'] == 5
        assert free['entitlements']['export']['is_enabled'] is False

    def test_pro_plan_pricing(self, client):
        resp = client.get('/api/billing/plans')
        plans = resp.get_json()['plans']
        pro = next(p for p in plans if p['code'] == 'pro')
        assert pro['price_cents'] == 1900
        assert pro['entitlements']['ai_queries']['limit_value'] == 400
        assert pro['entitlements']['custom_charts']['limit_value'] == 50
        assert pro['entitlements']['export']['is_enabled'] is True
        assert pro['entitlements']['export']['limit_value'] == 100
        assert pro['entitlements']['saved_projects']['limit_value'] == 25

    def test_business_plan_limits(self, client):
        resp = client.get('/api/billing/plans')
        plans = resp.get_json()['plans']
        biz = next(p for p in plans if p['code'] == 'business')
        assert biz['price_cents'] == 5900
        assert biz['entitlements']['ai_queries']['limit_value'] == 1500
        assert biz['entitlements']['priority_support']['is_enabled'] is True


class TestSubscriptionAPI:
    def test_subscription_requires_auth(self, client):
        resp = client.get('/api/billing/subscription')
        assert resp.status_code == 401

    def test_get_subscription_default_free(self, client, test_user):
        resp = client.get('/api/billing/subscription', headers={
            'Authorization': f'Bearer {test_user["token"]}'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['plan']['plan_code'] == 'free'

    def test_usage_endpoint(self, client, test_user):
        resp = client.get('/api/billing/usage', headers={
            'Authorization': f'Bearer {test_user["token"]}'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'usage' in data
        assert 'ai_queries' in data['usage']


class TestPaymentGatedUpgrades:
    """Upgrades MUST go through Stripe — no instant/mock activation."""

    def test_upgrade_blocked_without_stripe(self, client, test_user):
        """Without STRIPE_SECRET_KEY, upgrade returns error."""
        resp = client.post('/api/billing/checkout',
            headers={
                'Authorization': f'Bearer {test_user["token"]}',
                'Content-Type': 'application/json',
            },
            json={'plan_code': 'pro'},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'not configured' in data['error'].lower() or 'contact support' in data['error'].lower()

    def test_plan_stays_free_after_blocked_upgrade(self, client, test_user):
        """After a blocked upgrade attempt, user remains on free plan."""
        resp = client.get('/api/billing/subscription', headers={
            'Authorization': f'Bearer {test_user["token"]}'
        })
        data = resp.get_json()
        assert data['plan']['plan_code'] == 'free'

    def test_switch_plan_also_blocked(self, client, test_user):
        """switch-plan endpoint also requires Stripe for upgrades."""
        resp = client.post('/api/billing/switch-plan',
            headers={
                'Authorization': f'Bearer {test_user["token"]}',
                'Content-Type': 'application/json',
            },
            json={'plan_code': 'business'},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'not configured' in data['error'].lower() or 'contact support' in data['error'].lower()

    def test_downgrade_to_free_when_already_free(self, client, test_user):
        """User already on free plan — returns 'already on this plan'."""
        resp = client.post('/api/billing/switch-plan',
            headers={
                'Authorization': f'Bearer {test_user["token"]}',
                'Content-Type': 'application/json',
            },
            json={'plan_code': 'free'},
        )
        # Already on free → returns error (same plan)
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'already' in data['error'].lower()

    def test_no_mock_action_in_response(self, client, test_user):
        """Ensure mock_upgrade / mock_switch actions never appear."""
        resp = client.post('/api/billing/switch-plan',
            headers={
                'Authorization': f'Bearer {test_user["token"]}',
                'Content-Type': 'application/json',
            },
            json={'plan_code': 'pro'},
        )
        data = resp.get_json()
        action = data.get('action', '')
        assert 'mock' not in action


class TestEntitlementChecks:
    def test_free_plan_entitlement(self, app, test_user):
        """Test PL/pgSQL get_entitlement for free plan user."""
        with app.app_context():
            row = db.session.execute(
                db.text('SELECT is_enabled, limit_value FROM get_entitlement(:uid, :fk)'),
                {'uid': test_user['user'].id, 'fk': 'ai_queries'},
            ).fetchone()
            assert row is not None
            assert row[0] is True  # is_enabled
            # Free plan = 50/mo
            assert row[1] == 50

    def test_can_consume(self, app, test_user):
        """Test PL/pgSQL can_consume function."""
        with app.app_context():
            row = db.session.execute(
                db.text('SELECT allowed, reason FROM can_consume(:uid, :fk, :amt)'),
                {'uid': test_user['user'].id, 'fk': 'ai_queries', 'amt': 1},
            ).fetchone()
            assert row[0] is True
            assert row[1] == 'OK'

    def test_disabled_feature_blocked(self, app, test_user):
        """Free plan: export is disabled."""
        with app.app_context():
            row = db.session.execute(
                db.text('SELECT is_enabled FROM get_entitlement(:uid, :fk)'),
                {'uid': test_user['user'].id, 'fk': 'export'},
            ).fetchone()
            assert row[0] is False


class TestConsumeEndpoint:
    def test_consume_requires_auth(self, client):
        resp = client.post('/api/billing/consume',
            json={'feature_key': 'export'})
        assert resp.status_code == 401

    def test_consume_invalid_feature(self, client, test_user):
        resp = client.post('/api/billing/consume',
            headers={'Authorization': f'Bearer {test_user["token"]}',
                     'Content-Type': 'application/json'},
            json={'feature_key': 'nonexistent'})
        assert resp.status_code == 422

    def test_consume_blocked_on_free(self, client, test_user):
        """Free plan user cannot consume export (disabled)."""
        resp = client.post('/api/billing/consume',
            headers={'Authorization': f'Bearer {test_user["token"]}',
                     'Content-Type': 'application/json'},
            json={'feature_key': 'export'})
        assert resp.status_code == 402
        data = resp.get_json()
        assert data['upgrade_required'] is True

    def test_consume_ai_queries_allowed(self, client, test_user):
        """Free plan user can consume ai_queries (within limit)."""
        resp = client.post('/api/billing/consume',
            headers={'Authorization': f'Bearer {test_user["token"]}',
                     'Content-Type': 'application/json'},
            json={'feature_key': 'ai_queries'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['allowed'] is True


class TestWebhookIdempotency:
    def test_webhook_get_health_check(self, client):
        """GET on webhook URL returns 200 with ok:true (browser verification)."""
        resp = client.get('/api/billing/webhook')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True

    def test_webhook_get_stripe_path(self, client):
        """GET on /stripe/webhook alias also returns 200."""
        resp = client.get('/api/billing/stripe/webhook')
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True

    def test_webhook_no_stripe(self, client):
        """Without Stripe config, webhook POST should return 400."""
        resp = client.post('/api/billing/webhook', data='{}',
            content_type='application/json')
        assert resp.status_code == 400


class TestCheckoutStatus:
    def test_checkout_status_requires_auth(self, client):
        resp = client.get('/api/billing/checkout-status?session_id=test')
        assert resp.status_code == 401

    def test_checkout_status_requires_session_id(self, client, test_user):
        resp = client.get('/api/billing/checkout-status', headers={
            'Authorization': f'Bearer {test_user["token"]}'
        })
        assert resp.status_code == 422

    def test_checkout_status_no_stripe(self, client, test_user):
        """Without Stripe, checkout-status returns error."""
        resp = client.get('/api/billing/checkout-status?session_id=cs_test_123', headers={
            'Authorization': f'Bearer {test_user["token"]}'
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'not configured' in data['error'].lower()
