"""create initial schema — all base tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-02-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ── users ─────────────────────────────────────────────────────────────
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('auth_provider', sa.String(20), nullable=False, server_default='email'),
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('google_id', sa.String(255), nullable=True),
        sa.Column('avatar_url', sa.String(500), nullable=True),
        sa.Column('otp_hash', sa.String(255), nullable=True),
        sa.Column('otp_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('otp_attempts', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    op.create_unique_constraint('uq_users_google_id', 'users', ['google_id'])

    # ── password_reset_tokens ─────────────────────────────────────────────
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_password_reset_tokens_token_hash', 'password_reset_tokens', ['token_hash'])

    # ── plans ─────────────────────────────────────────────────────────────
    op.create_table(
        'plans',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('code', sa.String(50), nullable=False, unique=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('price_cents', sa.Integer, nullable=False, server_default='0'),
        sa.Column('currency', sa.String(3), nullable=False, server_default='USD'),
        sa.Column('interval', sa.String(20), nullable=False, server_default='month'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.text('true')),
        sa.Column('sort_order', sa.Integer, nullable=False, server_default='0'),
        sa.Column('stripe_price_id', sa.String(255)),
        sa.Column('created_at', sa.DateTime(timezone=True)),
    )

    # ── plan_entitlements ─────────────────────────────────────────────────
    op.create_table(
        'plan_entitlements',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('plan_id', sa.Integer, sa.ForeignKey('plans.id', ondelete='CASCADE'), nullable=False),
        sa.Column('feature_key', sa.String(100), nullable=False),
        sa.Column('limit_value', sa.Integer, nullable=True),
        sa.Column('is_enabled', sa.Boolean, nullable=False, server_default=sa.text('true')),
        sa.UniqueConstraint('plan_id', 'feature_key', name='uq_plan_feature'),
    )

    # ── subscriptions ─────────────────────────────────────────────────────
    op.create_table(
        'subscriptions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_id', sa.Integer, sa.ForeignKey('plans.id'), nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='active'),
        sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('cancel_at_period_end', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('provider', sa.String(30), nullable=False, server_default='mock'),
        sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('provider_customer_id', sa.String(255)),
        sa.Column('provider_subscription_id', sa.String(255)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        'idx_one_active_sub_per_user',
        'subscriptions',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'past_due')"),
    )

    # ── usage_events ──────────────────────────────────────────────────────
    op.create_table(
        'usage_events',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('feature_key', sa.String(100), nullable=False),
        sa.Column('amount', sa.Integer, nullable=False, server_default='1'),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_key', sa.String(7), nullable=False),
    )
    op.create_index('idx_usage_user_feature_period', 'usage_events',
                    ['user_id', 'feature_key', 'period_key'])

    # ── webhook_events ────────────────────────────────────────────────────
    op.create_table(
        'webhook_events',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
    )

    # ── PL/pgSQL functions ────────────────────────────────────────────────
    op.execute("""
CREATE OR REPLACE FUNCTION get_user_plan(p_user_id VARCHAR)
RETURNS TABLE(plan_id INTEGER, plan_code VARCHAR, plan_name VARCHAR)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN QUERY
    SELECT p.id, p.code::VARCHAR, p.name::VARCHAR
    FROM subscriptions s
    JOIN plans p ON p.id = s.plan_id
    WHERE s.user_id = p_user_id
      AND s.status IN ('active', 'past_due')
    ORDER BY p.sort_order DESC
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN QUERY
        SELECT p.id, p.code::VARCHAR, p.name::VARCHAR
        FROM plans p WHERE p.code = 'free' LIMIT 1;
    END IF;
END;
$$;
""")

    op.execute("""
CREATE OR REPLACE FUNCTION get_entitlement(p_user_id VARCHAR, p_feature_key VARCHAR)
RETURNS TABLE(is_enabled BOOLEAN, limit_value INTEGER)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_plan_id INTEGER;
BEGIN
    SELECT (get_user_plan(p_user_id)).plan_id INTO v_plan_id;

    RETURN QUERY
    SELECT pe.is_enabled, pe.limit_value
    FROM plan_entitlements pe
    WHERE pe.plan_id = v_plan_id AND pe.feature_key = p_feature_key;

    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, 0;
    END IF;
END;
$$;
""")

    op.execute("""
CREATE OR REPLACE FUNCTION get_usage_for_period(
    p_user_id VARCHAR,
    p_feature_key VARCHAR,
    p_period_key VARCHAR
)
RETURNS INTEGER
LANGUAGE plpgsql STABLE AS $$
DECLARE
    total INTEGER;
BEGIN
    SELECT COALESCE(SUM(amount), 0) INTO total
    FROM usage_events
    WHERE user_id = p_user_id
      AND feature_key = p_feature_key
      AND period_key = p_period_key;
    RETURN total;
END;
$$;
""")

    op.execute("""
CREATE OR REPLACE FUNCTION can_consume(
    p_user_id VARCHAR,
    p_feature_key VARCHAR,
    p_amount INTEGER DEFAULT 1
)
RETURNS TABLE(allowed BOOLEAN, reason TEXT, current_usage INTEGER, limit_val INTEGER)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_enabled BOOLEAN;
    v_limit INTEGER;
    v_current INTEGER;
    v_period VARCHAR(7);
BEGIN
    SELECT e.is_enabled, e.limit_value INTO v_enabled, v_limit
    FROM get_entitlement(p_user_id, p_feature_key) e;

    IF NOT v_enabled THEN
        RETURN QUERY SELECT FALSE, 'Feature not available on your plan'::TEXT, 0, 0;
        RETURN;
    END IF;

    IF v_limit IS NULL THEN
        RETURN QUERY SELECT TRUE, 'OK'::TEXT, 0, NULL::INTEGER;
        RETURN;
    END IF;

    v_period := to_char(NOW(), 'YYYY-MM');
    v_current := get_usage_for_period(p_user_id, p_feature_key, v_period);

    IF v_current + p_amount > v_limit THEN
        RETURN QUERY SELECT FALSE,
            format('Monthly limit reached (%s/%s)', v_current, v_limit)::TEXT,
            v_current, v_limit;
    ELSE
        RETURN QUERY SELECT TRUE, 'OK'::TEXT, v_current, v_limit;
    END IF;
END;
$$;
""")

    op.execute("""
CREATE OR REPLACE FUNCTION record_usage(
    p_user_id VARCHAR,
    p_feature_key VARCHAR,
    p_amount INTEGER DEFAULT 1
)
RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    v_id INTEGER;
BEGIN
    INSERT INTO usage_events(user_id, feature_key, amount, period_key)
    VALUES (p_user_id, p_feature_key, p_amount, to_char(NOW(), 'YYYY-MM'))
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;
""")

    # ── Seed data — Plans + Entitlements ──────────────────────────────────
    op.execute("""
INSERT INTO plans (code, name, description, price_cents, currency, interval, is_active, sort_order)
VALUES
    ('free', 'Free', 'Get started with basic analytics', 0, 'USD', 'month', TRUE, 0),
    ('pro', 'Pro', 'For power users who need more', 1200, 'USD', 'month', TRUE, 1),
    ('business', 'Business', 'For teams and advanced workflows', 2900, 'USD', 'month', TRUE, 2)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    price_cents = EXCLUDED.price_cents,
    is_active = EXCLUDED.is_active,
    sort_order = EXCLUDED.sort_order;
""")

    op.execute("""
INSERT INTO plan_entitlements (plan_id, feature_key, limit_value, is_enabled)
SELECT p.id, e.feature_key, e.limit_value, e.is_enabled
FROM plans p,
(VALUES
    ('document_uploads', 3::INTEGER, TRUE),
    ('ai_queries', 100::INTEGER, TRUE),
    ('custom_charts', 2::INTEGER, TRUE),
    ('premium_themes', NULL::INTEGER, FALSE),
    ('export', NULL::INTEGER, FALSE),
    ('priority_support', NULL::INTEGER, FALSE)
) AS e(feature_key, limit_value, is_enabled)
WHERE p.code = 'free'
ON CONFLICT (plan_id, feature_key) DO UPDATE SET
    limit_value = EXCLUDED.limit_value,
    is_enabled = EXCLUDED.is_enabled;
""")

    op.execute("""
INSERT INTO plan_entitlements (plan_id, feature_key, limit_value, is_enabled)
SELECT p.id, e.feature_key, e.limit_value, e.is_enabled
FROM plans p,
(VALUES
    ('document_uploads', 25::INTEGER, TRUE),
    ('ai_queries', 100::INTEGER, TRUE),
    ('custom_charts', 20::INTEGER, TRUE),
    ('premium_themes', NULL::INTEGER, TRUE),
    ('export', NULL::INTEGER, TRUE),
    ('priority_support', NULL::INTEGER, TRUE)
) AS e(feature_key, limit_value, is_enabled)
WHERE p.code = 'pro'
ON CONFLICT (plan_id, feature_key) DO UPDATE SET
    limit_value = EXCLUDED.limit_value,
    is_enabled = EXCLUDED.is_enabled;
""")

    op.execute("""
INSERT INTO plan_entitlements (plan_id, feature_key, limit_value, is_enabled)
SELECT p.id, e.feature_key, e.limit_value, e.is_enabled
FROM plans p,
(VALUES
    ('document_uploads', NULL::INTEGER, TRUE),
    ('ai_queries', NULL::INTEGER, TRUE),
    ('custom_charts', NULL::INTEGER, TRUE),
    ('premium_themes', NULL::INTEGER, TRUE),
    ('export', NULL::INTEGER, TRUE),
    ('priority_support', NULL::INTEGER, TRUE)
) AS e(feature_key, limit_value, is_enabled)
WHERE p.code = 'business'
ON CONFLICT (plan_id, feature_key) DO UPDATE SET
    limit_value = EXCLUDED.limit_value,
    is_enabled = EXCLUDED.is_enabled;
""")


def downgrade():
    op.drop_table('webhook_events')
    op.drop_table('usage_events')
    op.drop_table('subscriptions')
    op.drop_table('plan_entitlements')
    op.drop_table('plans')
    op.drop_table('password_reset_tokens')
    op.drop_table('users')
