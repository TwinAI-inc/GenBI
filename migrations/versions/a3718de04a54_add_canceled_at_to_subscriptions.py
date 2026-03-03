"""add canceled_at to subscriptions

Revision ID: a3718de04a54
Revises: 0001_initial
Create Date: 2026-02-19 15:41:31.973536

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


# revision identifiers, used by Alembic.
revision = 'a3718de04a54'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa_inspect(conn)

    # Only add canceled_at if it doesn't already exist (initial migration creates it)
    existing_cols = [c['name'] for c in inspector.get_columns('subscriptions')]
    if 'canceled_at' not in existing_cols:
        op.add_column('subscriptions',
                      sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True))

    # Normalize constraint name only if the old name exists
    existing_constraints = [c['name'] for c in inspector.get_unique_constraints('plan_entitlements')]
    if 'plan_entitlements_plan_id_feature_key_key' in existing_constraints:
        with op.batch_alter_table('plan_entitlements', schema=None) as batch_op:
            batch_op.drop_constraint('plan_entitlements_plan_id_feature_key_key', type_='unique')
            batch_op.create_unique_constraint('uq_plan_feature', ['plan_id', 'feature_key'])


def downgrade():
    op.drop_column('subscriptions', 'canceled_at')

    with op.batch_alter_table('plan_entitlements', schema=None) as batch_op:
        batch_op.drop_constraint('uq_plan_feature', type_='unique')
        batch_op.create_unique_constraint('plan_entitlements_plan_id_feature_key_key',
                                          ['plan_id', 'feature_key'])
