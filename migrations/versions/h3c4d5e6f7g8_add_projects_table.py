"""Add projects table for saved user workspaces.

Revision ID: h3c4d5e6f7g8
Revises: g2b3c4d5e6f7
Create Date: 2026-05-02 16:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = 'h3c4d5e6f7g8'
down_revision = 'g2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'projects',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36),
                  sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('dataset_json', sa.JSON, nullable=False, server_default='{}'),
        sa.Column('charts_json', sa.JSON, nullable=False, server_default='{}'),
        sa.Column('size_bytes', sa.Integer, nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    # Composite index supports the common "list my projects, newest first"
    # query without a separate sort step.
    op.create_index('ix_projects_owner_updated',
                    'projects', ['owner_id', 'updated_at'])


def downgrade():
    op.drop_index('ix_projects_owner_updated', table_name='projects')
    op.drop_table('projects')
