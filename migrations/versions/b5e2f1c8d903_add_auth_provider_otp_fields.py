"""add auth_provider, otp, and google oauth fields to users

Revision ID: b5e2f1c8d903
Revises: a3718de04a54
Create Date: 2026-02-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b5e2f1c8d903'
down_revision = 'a3718de04a54'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('auth_provider', sa.String(20), nullable=False, server_default='email'))
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('users', sa.Column('google_id', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('avatar_url', sa.String(500), nullable=True))
    op.add_column('users', sa.Column('otp_hash', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('otp_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('otp_attempts', sa.Integer(), nullable=False, server_default='0'))

    op.create_unique_constraint('uq_users_google_id', 'users', ['google_id'])

    # Make password_hash nullable (for Google OAuth users)
    op.alter_column('users', 'password_hash', existing_type=sa.String(255), nullable=True)


def downgrade():
    op.alter_column('users', 'password_hash', existing_type=sa.String(255), nullable=False)
    op.drop_constraint('uq_users_google_id', 'users', type_='unique')
    op.drop_column('users', 'otp_attempts')
    op.drop_column('users', 'otp_expires_at')
    op.drop_column('users', 'otp_hash')
    op.drop_column('users', 'avatar_url')
    op.drop_column('users', 'google_id')
    op.drop_column('users', 'email_verified')
    op.drop_column('users', 'auth_provider')
