"""
Database models for authentication.
"""

import uuid
from datetime import datetime, timezone
from extensions import db


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Auth provider & verification
    auth_provider = db.Column(db.String(20), nullable=False, default='email')
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    avatar_url = db.Column(db.String(500), nullable=True)

    # OTP fields
    otp_hash = db.Column(db.String(255), nullable=True)
    otp_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    otp_attempts = db.Column(db.Integer, nullable=False, default=0)

    @property
    def has_password(self):
        return self.password_hash is not None

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'auth_provider': self.auth_provider,
            'email_verified': self.email_verified,
            'avatar_url': self.avatar_url,
            'has_password': self.has_password,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    token_hash = db.Column(db.String(255), nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref=db.backref('reset_tokens', lazy='dynamic'))

    @property
    def is_expired(self):
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_used(self):
        return self.used_at is not None


class Project(db.Model):
    """Saved user workspace.

    Stores enough state to restore the user's analytics view: the dataset
    they uploaded (rows, headers, original file name) plus their custom
    chart layout. Multi-tenant isolation is enforced at the application
    layer — every read/write must filter by ``owner_id`` against the
    authenticated user.

    Storage strategy: dataset and charts are JSON blobs on a single row.
    For very large uploads this is denormalised; we cap project payloads
    at the API layer (see services.project_service.MAX_PAYLOAD_BYTES).
    """

    __tablename__ = 'projects'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='CASCADE'),
                         nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    # Original dataset blob: { headers: [...], rows: [...], file_name, n_rows, sheet_name? }
    dataset_json = db.Column(db.JSON, nullable=False, default=dict)
    # Saved chart layout: { chartPlan, customCharts, kpis, theme, ... }
    charts_json = db.Column(db.JSON, nullable=False, default=dict)
    # Approximate size in bytes of the JSON payload (computed at write time)
    # so we can enforce per-user storage quotas without re-serialising.
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    owner = db.relationship('User', backref=db.backref('projects', lazy='dynamic',
                                                       cascade='all, delete-orphan'))

    def to_summary(self):
        """List view — does NOT include the dataset rows or chart blob."""
        return {
            'id': self.id,
            'name': self.name,
            'n_rows': (self.dataset_json or {}).get('n_rows', 0),
            'n_cols': len((self.dataset_json or {}).get('headers', [])),
            'size_bytes': self.size_bytes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_full(self):
        """Detail view — includes everything needed to restore the workspace."""
        return {
            'id': self.id,
            'name': self.name,
            'dataset': self.dataset_json or {},
            'charts': self.charts_json or {},
            'size_bytes': self.size_bytes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
