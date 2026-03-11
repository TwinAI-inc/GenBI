"""Expire mock paid subscriptions — no free upgrades without Stripe.

Revision ID: a1f9c3d7e234
Revises: e8b4c2d5f789
Create Date: 2026-03-11
"""
from alembic import op

revision = 'a1f9c3d7e234'
down_revision = 'e8b4c2d5f789'
branch_labels = None
depends_on = None


def upgrade():
    # Expire all mock subscriptions for paid plans (pro, business).
    # These were created by the mock billing mode before it was disabled.
    # Users will revert to the free plan (no active subscription = free).
    op.execute("""
        UPDATE subscriptions
        SET    status     = 'expired',
               canceled_at = NOW(),
               updated_at  = NOW()
        WHERE  provider = 'mock'
          AND  status IN ('active', 'past_due')
          AND  plan_id IN (
                 SELECT id FROM plans WHERE code IN ('pro', 'business')
               );
    """)


def downgrade():
    # Cannot safely re-activate mock subscriptions — data may have changed.
    pass
