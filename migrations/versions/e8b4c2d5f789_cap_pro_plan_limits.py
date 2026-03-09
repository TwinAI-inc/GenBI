"""Cap Pro plan limits (charts/exports/projects)

Pro plan changes:
- custom_charts: NULL (unlimited) → 50/mo
- export: NULL (unlimited) → 100/mo
- saved_projects: NULL (unlimited) → 25

Free and Business plans unchanged.

Revision ID: e8b4c2d5f789
Revises: d7a3b9e1f456
Create Date: 2026-03-09 12:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e8b4c2d5f789'
down_revision = 'd7a3b9e1f456'
branch_labels = None
depends_on = None


def upgrade():
    # ── Pro plan: custom_charts 50/mo (was unlimited) ──────────────────────
    op.execute("""
UPDATE plan_entitlements SET limit_value = 50
WHERE feature_key = 'custom_charts'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")

    # ── Pro plan: export capped at 100/mo (was unlimited) ──────────────────
    op.execute("""
UPDATE plan_entitlements SET limit_value = 100
WHERE feature_key = 'export'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")

    # ── Pro plan: saved_projects 25 (was unlimited) ────────────────────────
    op.execute("""
UPDATE plan_entitlements SET limit_value = 25
WHERE feature_key = 'saved_projects'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")


def downgrade():
    # Revert Pro plan to unlimited
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL
WHERE feature_key = 'custom_charts'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL
WHERE feature_key = 'export'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
    op.execute("""
UPDATE plan_entitlements SET limit_value = NULL
WHERE feature_key = 'saved_projects'
  AND plan_id = (SELECT id FROM plans WHERE code = 'pro');
""")
