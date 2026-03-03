"""
Billing database models.
"""

import uuid
from datetime import datetime, timezone

from extensions import db


class Plan(db.Model):
    __tablename__ = 'plans'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    price_cents = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False, default='USD')
    interval = db.Column(db.String(20), nullable=False, default='month')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    stripe_price_id = db.Column(db.String(255))
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    entitlements = db.relationship('PlanEntitlement', backref='plan', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'description': self.description,
            'price_cents': self.price_cents,
            'currency': self.currency,
            'interval': self.interval,
            'is_active': self.is_active,
            'sort_order': self.sort_order,
            'entitlements': {e.feature_key: e.to_dict() for e in self.entitlements},
        }


class PlanEntitlement(db.Model):
    __tablename__ = 'plan_entitlements'

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('plans.id', ondelete='CASCADE'), nullable=False)
    feature_key = db.Column(db.String(100), nullable=False)
    limit_value = db.Column(db.Integer)  # NULL = unlimited
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint('plan_id', 'feature_key', name='uq_plan_feature'),
    )

    def to_dict(self):
        return {
            'feature_key': self.feature_key,
            'limit_value': self.limit_value,
            'is_enabled': self.is_enabled,
        }


class Subscription(db.Model):
    __tablename__ = 'subscriptions'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('plans.id'), nullable=False)
    status = db.Column(db.String(30), nullable=False, default='active')
    current_period_start = db.Column(db.DateTime(timezone=True), nullable=False)
    current_period_end = db.Column(db.DateTime(timezone=True), nullable=False)
    cancel_at_period_end = db.Column(db.Boolean, nullable=False, default=False)
    provider = db.Column(db.String(30), nullable=False, default='mock')
    canceled_at = db.Column(db.DateTime(timezone=True))
    provider_customer_id = db.Column(db.String(255))
    provider_subscription_id = db.Column(db.String(255))
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    plan = db.relationship('Plan')

    # Partial unique index: one active sub per user
    __table_args__ = (
        db.Index(
            'idx_one_active_sub_per_user',
            'user_id',
            unique=True,
            postgresql_where=db.text("status IN ('active', 'past_due')"),
        ),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'plan_id': self.plan_id,
            'plan': self.plan.to_dict() if self.plan else None,
            'status': self.status,
            'current_period_start': self.current_period_start.isoformat() if self.current_period_start else None,
            'current_period_end': self.current_period_end.isoformat() if self.current_period_end else None,
            'cancel_at_period_end': self.cancel_at_period_end,
            'canceled_at': self.canceled_at.isoformat() if self.canceled_at else None,
            'provider': self.provider,
            'provider_customer_id': self.provider_customer_id or None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class UsageEvent(db.Model):
    __tablename__ = 'usage_events'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    feature_key = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Integer, nullable=False, default=1)
    occurred_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    period_key = db.Column(db.String(7), nullable=False)  # YYYY-MM

    __table_args__ = (
        db.Index('idx_usage_user_feature_period', 'user_id', 'feature_key', 'period_key'),
    )


class WebhookEvent(db.Model):
    __tablename__ = 'webhook_events'

    id = db.Column(db.String(255), primary_key=True)
    event_type = db.Column(db.String(100), nullable=False)
    processed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
