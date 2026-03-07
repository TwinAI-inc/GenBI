"""Update pricing tiers and AI credit limits

New pricing model based on Azure AI Foundry cost:
- Free: $0/mo, 25 AI chats, 5 uploads, 5 charts, no export, 1 project
- Pro: $19/mo, 400 AI chats, 50 uploads, unlimited charts, export, unlimited projects
- Business: $59/mo, 1500 AI chats, 500 uploads (fair-use), unlimited charts, export, unlimited projects, priority support

Revision ID: d7a3b9e1f456
Revises: c4f8a1d2e567
Create Date: 2026-03-06 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7a3b9e1f456'
down_revision = 'c4f8a1d2e567'
branch_labels = None
depends_on = None


def upgrade():
    # ── Fix missing server_default on usage_events.occurred_at ────────────
    # The 0001 migration created the column WITHOUT a SQL-level DEFAULT,
    # so the PL/pgSQL record_usage() function fails on INSERT.
    op.alter_column('usage_events', 'occurred_at',
                    server_default=sa.text('NOW()'))

    # ── Update plan display fields ────────────────────────────────────────
    op.execute("""
UPDATE plans SET
    description = 'Get started with basic analytics',
    price_cents = 0,
    sort_order = 0
WHERE code = 'free';
""")
    op.execute("""
UPDATE plans SET
    description = 'For power users who need more',
    price_cents = 1900,
    sort_order = 1
WHERE code = 'pro';
""")
    op.execute("""
UPDATE plans SET
    description = 'For teams and advanced workflows',
    price_cents = 5900,
    sort_order = 2
WHERE code = 'business';
""")

    # ── Free plan entitlements ────────────────────────────────────────────
    # ai_queries: 25/mo (was 100)
    op.execute("""
UPDATE plan_entitlements SET limit_value = 25, is_enabled = TRUE
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
    # document_uploads: 5/mo (was 3)
    op.execute("""
UPDATE plan_entitlements SET limit_value = 5, is_enabled = TRUE
WHERE feature_key = 'document_uploads'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
    # custom_charts: 5 (was 10)
    op.execute("""
UPDATE plan_entitlements SET limit_value = 5, is_enabled = TRUE
WHERE feature_key = 'custom_charts'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
    # export: disabled (was enabled with 10)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = FALSE
WHERE feature_key = 'export'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
    # premium_themes: disabled (unchanged)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = FALSE
WHERE feature_key = 'premium_themes'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
    # priority_support: disabled (unchanged)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = FALSE
WHERE feature_key = 'priority_support'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
    # saved_projects: 1 (new feature key)
    op.execute("""
INSERT INTO plan_entitlements (plan_id, feature_key, limit_value, is_enabled)
SELECT p.id, 'saved_projects', 1, TRUE
FROM plans p WHERE p.code = 'free'
ON CONFLICT (plan_id, feature_key) DO UPDATE SET limit_value = 1, is_enabled = TRUE;
""")

    # ── Pro plan entitlements ─────────────────────────────────────────────
    # ai_queries: 400/mo (was 100)
    op.execute("""
UPDATE plan_entitlements SET limit_value = 400, is_enabled = TRUE
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    # document_uploads: 50/mo (was 25)
    op.execute("""
UPDATE plan_entitlements SET limit_value = 50, is_enabled = TRUE
WHERE feature_key = 'document_uploads'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    # custom_charts: unlimited (was 20)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = TRUE
WHERE feature_key = 'custom_charts'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    # export: enabled unlimited (unchanged)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = TRUE
WHERE feature_key = 'export'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    # premium_themes: enabled (unchanged)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = TRUE
WHERE feature_key = 'premium_themes'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    # priority_support: disabled on Pro (was enabled)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = FALSE
WHERE feature_key = 'priority_support'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    # saved_projects: unlimited (new feature key)
    op.execute("""
INSERT INTO plan_entitlements (plan_id, feature_key, limit_value, is_enabled)
SELECT p.id, 'saved_projects', NULL, TRUE
FROM plans p WHERE p.code = 'pro'
ON CONFLICT (plan_id, feature_key) DO UPDATE SET limit_value = NULL, is_enabled = TRUE;
""")

    # ── Business plan entitlements ────────────────────────────────────────
    # ai_queries: 1500/mo (was unlimited)
    op.execute("""
UPDATE plan_entitlements SET limit_value = 1500, is_enabled = TRUE
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'business');
""")
    # document_uploads: 500/mo fair-use cap (was unlimited)
    op.execute("""
UPDATE plan_entitlements SET limit_value = 500, is_enabled = TRUE
WHERE feature_key = 'document_uploads'
  AND plan_id = (SELECT id FROM plans WHERE code = 'business');
""")
    # custom_charts: unlimited (unchanged)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = TRUE
WHERE feature_key = 'custom_charts'
  AND plan_id = (SELECT id FROM plans WHERE code = 'business');
""")
    # export: enabled unlimited (unchanged)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = TRUE
WHERE feature_key = 'export'
  AND plan_id = (SELECT id FROM plans WHERE code = 'business');
""")
    # premium_themes: enabled (unchanged)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = TRUE
WHERE feature_key = 'premium_themes'
  AND plan_id = (SELECT id FROM plans WHERE code = 'business');
""")
    # priority_support: enabled (unchanged)
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = TRUE
WHERE feature_key = 'priority_support'
  AND plan_id = (SELECT id FROM plans WHERE code = 'business');
""")
    # saved_projects: unlimited (new feature key)
    op.execute("""
INSERT INTO plan_entitlements (plan_id, feature_key, limit_value, is_enabled)
SELECT p.id, 'saved_projects', NULL, TRUE
FROM plans p WHERE p.code = 'business'
ON CONFLICT (plan_id, feature_key) DO UPDATE SET limit_value = NULL, is_enabled = TRUE;
""")


def downgrade():
    # Revert occurred_at default
    op.alter_column('usage_events', 'occurred_at', server_default=None)

    # Revert plan prices
    op.execute("UPDATE plans SET price_cents = 0 WHERE code = 'free';")
    op.execute("UPDATE plans SET price_cents = 1200 WHERE code = 'pro';")
    op.execute("UPDATE plans SET price_cents = 2900 WHERE code = 'business';")

    # Revert Free plan entitlements
    op.execute("""
UPDATE plan_entitlements SET limit_value = 100
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
    op.execute("""
UPDATE plan_entitlements SET limit_value = 3
WHERE feature_key = 'document_uploads'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
    op.execute("""
UPDATE plan_entitlements SET limit_value = 10
WHERE feature_key = 'custom_charts'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
    op.execute("""
UPDATE plan_entitlements SET limit_value = 10, is_enabled = TRUE
WHERE feature_key = 'export'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")

    # Revert Pro plan entitlements
    op.execute("""
UPDATE plan_entitlements SET limit_value = 100
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    op.execute("""
UPDATE plan_entitlements SET limit_value = 25
WHERE feature_key = 'document_uploads'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    op.execute("""
UPDATE plan_entitlements SET limit_value = 20
WHERE feature_key = 'custom_charts'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL, is_enabled = TRUE
WHERE feature_key = 'priority_support'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")

    # Revert Business plan entitlements
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'business');
""")
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL
WHERE feature_key = 'document_uploads'
  AND plan_id = (SELECT id FROM plans WHERE code = 'business');
""")

    # Remove saved_projects rows
    op.execute("DELETE FROM plan_entitlements WHERE feature_key = 'saved_projects';")
