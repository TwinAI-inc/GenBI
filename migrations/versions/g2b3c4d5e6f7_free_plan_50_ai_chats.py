"""Increase free plan ai_queries from 25 to 50.

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-04-01 09:00:00.000000
"""

from alembic import op

revision = 'g2b3c4d5e6f7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
UPDATE plan_entitlements SET limit_value = 50
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")


def downgrade():
    op.execute("""
UPDATE plan_entitlements SET limit_value = 25
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
