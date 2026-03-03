"""fix free plan ai_queries limit to 100

The 0001_initial migration was applied to production before the seed
data was corrected from 10 → 100.  Alembic won't re-run an already-
applied revision, so this data-only migration updates the value.

Revision ID: c4f8a1d2e567
Revises: b5e2f1c8d903
Create Date: 2026-03-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4f8a1d2e567'
down_revision = 'b5e2f1c8d903'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
UPDATE plan_entitlements
SET limit_value = 100
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")


def downgrade():
    op.execute("""
UPDATE plan_entitlements
SET limit_value = 10
WHERE feature_key = 'ai_queries'
  AND plan_id = (SELECT id FROM plans WHERE code = 'free');
""")
