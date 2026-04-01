"""
Dataset API endpoints:
  POST /api/datasets/upload    — upload CSV, profile, return suggestions
  POST /api/datasets/chart     — aggregate chart data from a ChartSpec
  POST /api/datasets/drilldown — drilldown aggregation
  GET  /api/datasets/list      — list user's datasets
"""

import csv
import io
import logging

from flask import request, jsonify

from extensions import db
from auth.routes import auth_required
from . import datasets_bp
from .models import Dataset, DatasetColumn, DatasetRow
from .profiler import profile_columns
from .chart_suggestions import suggest_charts
from .aggregator import aggregate_chart, drilldown

logger = logging.getLogger(__name__)

MAX_UPLOAD_ROWS = 50_000
MAX_PROFILE_ROWS = 5_000
MAX_COLUMNS = 200


@datasets_bp.route('/upload', methods=['POST'])
@auth_required
def upload_dataset():
    """
    Upload a CSV dataset. Accepts either:
      - multipart file upload (field name: "file")
      - JSON body with {"name": "...", "rows": [...], "headers": [...]}
    """
    user = request.current_user

    # Check document_uploads quota
    from billing.services.entitlement_service import can_consume, record_usage
    check = can_consume(user.id, 'document_uploads', 1)
    if not check['allowed']:
        return jsonify({'error': check['reason'], 'upgrade_required': True}), 402

    try:
        name, headers, rows = _parse_upload(request)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    if len(headers) > MAX_COLUMNS:
        return jsonify({'error': f'Too many columns (max {MAX_COLUMNS}).'}), 400
    if len(rows) > MAX_UPLOAD_ROWS:
        return jsonify({'error': f'Too many rows (max {MAX_UPLOAD_ROWS:,}).'}), 400
    if not rows:
        return jsonify({'error': 'Dataset is empty.'}), 400

    # Profile columns (on sample)
    profiles = profile_columns(rows, max_profile_rows=MAX_PROFILE_ROWS)

    # Create dataset record
    ds = Dataset(owner_id=user.id, name=name, row_count=len(rows))
    db.session.add(ds)
    db.session.flush()  # get ds.id

    # Save column profiles
    for p in profiles:
        col = DatasetColumn(
            dataset_id=ds.id,
            name=p['name'],
            inferred_type=p['type'],
            cardinality=p['cardinality'],
            null_pct=p['null_pct'],
            sample_values_json=p.get('sample_values', []),
            stats_json=p.get('stats'),
        )
        db.session.add(col)

    # Save rows in batches
    batch = []
    for r in rows:
        batch.append(DatasetRow(dataset_id=ds.id, data=r))
        if len(batch) >= 500:
            db.session.bulk_save_objects(batch)
            batch = []
    if batch:
        db.session.bulk_save_objects(batch)

    db.session.commit()

    # Record usage
    record_usage(user.id, 'document_uploads', 1)

    # Generate chart suggestions
    suggestions = suggest_charts(profiles)

    return jsonify({
        'dataset': ds.to_dict(),
        'suggestions': suggestions,
    }), 201


@datasets_bp.route('/chart', methods=['POST'])
@auth_required
def chart_data():
    """
    Aggregate data for a chart spec.

    Body: {"dataset_id": "...", "chart_spec": {...}, "filters": [...]}
    """
    data = request.get_json(silent=True) or {}
    dataset_id = data.get('dataset_id')
    chart_spec = data.get('chart_spec')
    filters = data.get('filters', [])

    if not dataset_id or not chart_spec:
        return jsonify({'error': 'dataset_id and chart_spec are required.'}), 422

    # Verify ownership
    ds = Dataset.query.filter_by(id=dataset_id, owner_id=request.current_user.id).first()
    if not ds:
        return jsonify({'error': 'Dataset not found.'}), 404

    result = aggregate_chart(dataset_id, chart_spec, extra_filters=filters)
    result['chart_spec'] = chart_spec
    return jsonify(result)


@datasets_bp.route('/drilldown', methods=['POST'])
@auth_required
def drilldown_endpoint():
    """
    Drilldown into data.

    Body: {"dataset_id": "...", "filters": [...], "metric": "col", "agg": "sum", "dims": ["col1", "col2"]}
    """
    data = request.get_json(silent=True) or {}
    dataset_id = data.get('dataset_id')
    metric = data.get('metric')
    agg_fn = data.get('agg', 'sum')
    filters = data.get('filters', [])
    dims = data.get('dims')

    if not dataset_id or not metric:
        return jsonify({'error': 'dataset_id and metric are required.'}), 422

    ds = Dataset.query.filter_by(id=dataset_id, owner_id=request.current_user.id).first()
    if not ds:
        return jsonify({'error': 'Dataset not found.'}), 404

    result = drilldown(dataset_id, filters, metric, agg_fn, dim_cols=dims)
    return jsonify(result)


@datasets_bp.route('/explain-point', methods=['POST'])
@auth_required
def explain_point():
    """
    Explain a clicked data point: sunburst drilldown + key influencers.

    Body: {
        "dataset_id": "...",
        "measure": "Revenue",
        "clicked": {"Month": "2024-06", "Channel": "Enterprise"},
        "filters": [{"column": "...", "value": "..."}],
        "max_levels": 3,
        "max_children_per_level": 12
    }
    """
    data = request.get_json(silent=True) or {}
    dataset_id = data.get('dataset_id')
    measure = data.get('measure')
    clicked = data.get('clicked', {})
    filters = data.get('filters', [])
    max_levels = min(data.get('max_levels', 3), 4)
    max_children = min(data.get('max_children_per_level', 12), 20)

    if not dataset_id or not measure:
        return jsonify({'error': 'dataset_id and measure are required.'}), 422

    ds = Dataset.query.filter_by(id=dataset_id, owner_id=request.current_user.id).first()
    if not ds:
        return jsonify({'error': 'Dataset not found.'}), 404

    from .aggregator import explain_point as _explain
    result = _explain(dataset_id, measure, clicked, filters, max_levels, max_children)
    return jsonify(result)


@datasets_bp.route('/list', methods=['GET'])
@auth_required
def list_datasets():
    """List all datasets for the current user."""
    datasets = Dataset.query.filter_by(owner_id=request.current_user.id)\
        .order_by(Dataset.created_at.desc()).limit(50).all()
    return jsonify({'datasets': [d.to_dict() for d in datasets]})


# ── Helpers ──

def _parse_upload(req):
    """Parse upload from either file or JSON body. Returns (name, headers, rows)."""
    # Try file upload first
    if 'file' in req.files:
        f = req.files['file']
        if not f.filename:
            raise ValueError('No file selected.')
        name = f.filename
        content = f.read().decode('utf-8-sig', errors='replace')
        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []
        rows = []
        for i, row in enumerate(reader):
            if i >= MAX_UPLOAD_ROWS:
                break
            rows.append(dict(row))
        return name, headers, rows

    # Try JSON body
    data = req.get_json(silent=True) or {}
    if 'rows' in data and 'headers' in data:
        name = data.get('name', 'Uploaded Dataset')
        headers = data['headers']
        rows = data['rows'][:MAX_UPLOAD_ROWS]
        return name, headers, rows

    raise ValueError('Provide a CSV file or JSON body with headers and rows.')
