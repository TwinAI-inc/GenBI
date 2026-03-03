"""add canceled_at to subscriptions

Revision ID: a3718de04a54
Revises:
Create Date: 2026-02-19 15:41:31.973536

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3718de04a54'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add canceled_at column (nullable, no backfill needed)
    op.add_column('subscriptions',
                  sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True))

    # Normalize constraint name
    with op.batch_alter_table('plan_entitlements', schema=None) as batch_op:
        batch_op.drop_constraint('plan_entitlements_plan_id_feature_key_key', type_='unique')
        batch_op.create_unique_constraint('uq_plan_feature', ['plan_id', 'feature_key'])


def downgrade():
    op.drop_column('subscriptions', 'canceled_at')

    with op.batch_alter_table('plan_entitlements', schema=None) as batch_op:
        batch_op.drop_constraint('uq_plan_feature', type_='unique')
        batch_op.create_unique_constraint('plan_entitlements_plan_id_feature_key_key',
                                          ['plan_id', 'feature_key'])
