"""
Minimal billing tests.
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
        assert free['entitlements']['ai_queries']['limit_value'] == 25
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


class TestCheckoutAndCancel:
    def test_checkout_mock_mode(self, client, test_user):
        """In mock mode, checkout should immediately activate."""
        resp = client.post('/api/billing/checkout',
            headers={
                'Authorization': f'Bearer {test_user["token"]}',
                'Content-Type': 'application/json',
            },
            json={'plan_code': 'pro'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('action') == 'mock_upgrade'
        assert data['subscription']['plan']['code'] == 'pro'

    def test_subscription_now_pro(self, client, test_user):
        resp = client.get('/api/billing/subscription', headers={
            'Authorization': f'Bearer {test_user["token"]}'
        })
        data = resp.get_json()
        assert data['plan']['plan_code'] == 'pro'

    def test_cancel(self, client, test_user):
        resp = client.post('/api/billing/cancel', headers={
            'Authorization': f'Bearer {test_user["token"]}'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['subscription']['subscription']['cancel_at_period_end'] is True

    def test_resume(self, client, test_user):
        resp = client.post('/api/billing/resume', headers={
            'Authorization': f'Bearer {test_user["token"]}'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['subscription']['cancel_at_period_end'] is False

    def test_switch_to_business(self, client, test_user):
        resp = client.post('/api/billing/checkout',
            headers={
                'Authorization': f'Bearer {test_user["token"]}',
                'Content-Type': 'application/json',
            },
            json={'plan_code': 'business'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['subscription']['plan']['code'] == 'business'


class TestEntitlementChecks:
    def test_entitlement_via_db(self, app, test_user):
        """Test PL/pgSQL get_entitlement function directly."""
        with app.app_context():
            row = db.session.execute(
                db.text('SELECT is_enabled, limit_value FROM get_entitlement(:uid, :fk)'),
                {'uid': test_user['user'].id, 'fk': 'ai_queries'},
            ).fetchone()
            assert row is not None
            assert row[0] is True  # is_enabled
            # Business plan = 1500/mo
            assert row[1] == 1500

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
        """Business plan has all features enabled, so test with a fake feature."""
        with app.app_context():
            row = db.session.execute(
                db.text('SELECT is_enabled FROM get_entitlement(:uid, :fk)'),
                {'uid': test_user['user'].id, 'fk': 'nonexistent_feature'},
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

    def test_consume_allowed(self, client, test_user):
        """Business plan user can consume export (unlimited)."""
        resp = client.post('/api/billing/consume',
            headers={'Authorization': f'Bearer {test_user["token"]}',
                     'Content-Type': 'application/json'},
            json={'feature_key': 'export'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['allowed'] is True


class TestWebhookIdempotency:
    def test_webhook_no_stripe(self, client):
        """Without Stripe config, webhook should return 400."""
        resp = client.post('/api/billing/webhook', data='{}',
            content_type='application/json')
        assert resp.status_code == 400
