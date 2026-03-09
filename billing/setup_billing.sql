-- ═══════════════════════════════════════════════════════════════════════════
-- GenBI Billing — Schema + PL/pgSQL Functions + Seed Data
-- Run: psql genbi_auth < billing/setup_billing.sql
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Tables ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plans (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    price_cents INTEGER NOT NULL DEFAULT 0,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    interval VARCHAR(20) NOT NULL DEFAULT 'month',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    stripe_price_id VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plan_entitlements (
    id SERIAL PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    feature_key VARCHAR(100) NOT NULL,
    limit_value INTEGER,  -- NULL = unlimited
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(plan_id, feature_key)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    current_period_start TIMESTAMPTZ NOT NULL,
    current_period_end TIMESTAMPTZ NOT NULL,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    canceled_at TIMESTAMPTZ,
    provider VARCHAR(30) NOT NULL DEFAULT 'mock',
    provider_customer_id VARCHAR(255),
    provider_subscription_id VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One active subscription per user
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_sub_per_user
    ON subscriptions(user_id) WHERE status IN ('active', 'past_due');

CREATE TABLE IF NOT EXISTS usage_events (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feature_key VARCHAR(100) NOT NULL,
    amount INTEGER NOT NULL DEFAULT 1,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period_key VARCHAR(7) NOT NULL  -- YYYY-MM
);

CREATE INDEX IF NOT EXISTS idx_usage_user_feature_period
    ON usage_events(user_id, feature_key, period_key);

CREATE TABLE IF NOT EXISTS webhook_events (
    id VARCHAR(255) PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ═══════════════════════════════════════════════════════════════════════════
-- PL/pgSQL FUNCTIONS
-- ═══════════════════════════════════════════════════════════════════════════

-- get_user_plan: returns the plan for a user (defaults to 'free')
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

-- get_entitlement: check a specific feature for a user
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

-- get_usage_for_period: total usage for a user+feature in a period
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

-- can_consume: check if user can use a feature (quota check)
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

    -- NULL limit = unlimited
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

-- record_usage: insert a usage event
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


-- ═══════════════════════════════════════════════════════════════════════════
-- SEED DATA — Plans + Entitlements
-- ═══════════════════════════════════════════════════════════════════════════

-- Upsert plans
INSERT INTO plans (code, name, description, price_cents, currency, interval, is_active, sort_order)
VALUES
    ('free', 'Free', 'Get started with basic analytics', 0, 'USD', 'month', TRUE, 0),
    ('pro', 'Pro', 'For power users who need more', 1900, 'USD', 'month', TRUE, 1),
    ('business', 'Business', 'For teams and advanced workflows', 5900, 'USD', 'month', TRUE, 2)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    price_cents = EXCLUDED.price_cents,
    is_active = EXCLUDED.is_active,
    sort_order = EXCLUDED.sort_order;

-- Seed entitlements for Free plan
INSERT INTO plan_entitlements (plan_id, feature_key, limit_value, is_enabled)
SELECT p.id, e.feature_key, e.limit_value, e.is_enabled
FROM plans p,
(VALUES
    ('document_uploads', 5::INTEGER, TRUE),
    ('ai_queries', 25::INTEGER, TRUE),
    ('custom_charts', 5::INTEGER, TRUE),
    ('premium_themes', NULL::INTEGER, FALSE),
    ('export', NULL::INTEGER, FALSE),
    ('priority_support', NULL::INTEGER, FALSE),
    ('saved_projects', 1::INTEGER, TRUE)
) AS e(feature_key, limit_value, is_enabled)
WHERE p.code = 'free'
ON CONFLICT (plan_id, feature_key) DO UPDATE SET
    limit_value = EXCLUDED.limit_value,
    is_enabled = EXCLUDED.is_enabled;

-- Seed entitlements for Pro plan
INSERT INTO plan_entitlements (plan_id, feature_key, limit_value, is_enabled)
SELECT p.id, e.feature_key, e.limit_value, e.is_enabled
FROM plans p,
(VALUES
    ('document_uploads', 50::INTEGER, TRUE),
    ('ai_queries', 400::INTEGER, TRUE),
    ('custom_charts', 50::INTEGER, TRUE),
    ('premium_themes', NULL::INTEGER, TRUE),
    ('export', 100::INTEGER, TRUE),
    ('priority_support', NULL::INTEGER, FALSE),
    ('saved_projects', 25::INTEGER, TRUE)
) AS e(feature_key, limit_value, is_enabled)
WHERE p.code = 'pro'
ON CONFLICT (plan_id, feature_key) DO UPDATE SET
    limit_value = EXCLUDED.limit_value,
    is_enabled = EXCLUDED.is_enabled;

-- Seed entitlements for Business plan (NULL limit = unlimited)
INSERT INTO plan_entitlements (plan_id, feature_key, limit_value, is_enabled)
SELECT p.id, e.feature_key, e.limit_value, e.is_enabled
FROM plans p,
(VALUES
    ('document_uploads', 500::INTEGER, TRUE),
    ('ai_queries', 1500::INTEGER, TRUE),
    ('custom_charts', NULL::INTEGER, TRUE),
    ('premium_themes', NULL::INTEGER, TRUE),
    ('export', NULL::INTEGER, TRUE),
    ('priority_support', NULL::INTEGER, TRUE),
    ('saved_projects', NULL::INTEGER, TRUE)
) AS e(feature_key, limit_value, is_enabled)
WHERE p.code = 'business'
ON CONFLICT (plan_id, feature_key) DO UPDATE SET
    limit_value = EXCLUDED.limit_value,
    is_enabled = EXCLUDED.is_enabled;

-- Verify
SELECT p.code, pe.feature_key, pe.limit_value, pe.is_enabled
FROM plans p
JOIN plan_entitlements pe ON pe.plan_id = p.id
ORDER BY p.sort_order, pe.feature_key;
