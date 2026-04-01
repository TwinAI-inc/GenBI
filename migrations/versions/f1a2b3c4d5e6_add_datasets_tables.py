"""Add datasets, dataset_columns, dataset_rows tables.

Revision ID: f1a2b3c4d5e6
Revises: e8b4c2d5f789
Create Date: 2026-04-01 04:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = 'f1a2b3c4d5e6'
down_revision = 'e8b4c2d5f789'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'datasets',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('row_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('schema_json', sa.JSON, nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        'dataset_columns',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('datasets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('inferred_type', sa.String(50), nullable=False),
        sa.Column('cardinality', sa.Integer, server_default='0'),
        sa.Column('null_pct', sa.Float, server_default='0'),
        sa.Column('sample_values_json', sa.JSON, server_default='[]'),
        sa.Column('stats_json', sa.JSON),
        sa.UniqueConstraint('dataset_id', 'name', name='uq_dataset_col_name'),
    )

    op.create_table(
        'dataset_rows',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('datasets.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('data', sa.JSON, nullable=False),
    )


def downgrade():
    op.drop_table('dataset_rows')
    op.drop_table('dataset_columns')
    op.drop_table('datasets')
