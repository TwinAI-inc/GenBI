"""
Dataset storage models.

Design: one datasets table for metadata, one dataset_rows table for all row data
(keyed by dataset_id). Columns are stored as JSONB per row for schema flexibility.
"""

import uuid
from datetime import datetime, timezone

from extensions import db


class Dataset(db.Model):
    __tablename__ = 'datasets'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = db.Column(
        db.String(36),
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(255), nullable=False)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    schema_json = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    columns = db.relationship(
        'DatasetColumn', backref='dataset', cascade='all, delete-orphan', lazy='selectin',
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'row_count': self.row_count,
            'columns': [c.to_dict() for c in self.columns],
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class DatasetColumn(db.Model):
    __tablename__ = 'dataset_columns'

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(
        db.String(36),
        db.ForeignKey('datasets.id', ondelete='CASCADE'),
        nullable=False,
    )
    name = db.Column(db.String(255), nullable=False)
    inferred_type = db.Column(db.String(50), nullable=False)  # datetime/numeric/categorical/id_like/text_blob
    cardinality = db.Column(db.Integer, default=0)
    null_pct = db.Column(db.Float, default=0.0)
    sample_values_json = db.Column(db.JSON, default=list)
    stats_json = db.Column(db.JSON, default=dict)  # min/max/mean for numeric

    __table_args__ = (
        db.UniqueConstraint('dataset_id', 'name', name='uq_dataset_col_name'),
    )

    def to_dict(self):
        d = {
            'name': self.name,
            'type': self.inferred_type,
            'cardinality': self.cardinality,
            'null_pct': self.null_pct,
            'sample_values': self.sample_values_json or [],
        }
        if self.stats_json:
            d['stats'] = self.stats_json
        return d


class DatasetRow(db.Model):
    __tablename__ = 'dataset_rows'

    id = db.Column(db.BigInteger, primary_key=True)
    dataset_id = db.Column(
        db.String(36),
        db.ForeignKey('datasets.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    data = db.Column(db.JSON, nullable=False)
