"""
Server-side aggregation engine.

Takes a dataset_id + ChartSpec, queries DatasetRow, and returns
series data ready to render on the frontend.
"""

import re
from collections import defaultdict

from extensions import db
from .models import DatasetRow, DatasetColumn


def aggregate_chart(dataset_id, chart_spec, extra_filters=None):
    """
    Execute aggregation for a chart spec against stored rows.

    Returns:
    {
        "labels": [...],
        "series": [{"name": "...", "data": [...]}] or null,
        "data": [...] or null,
        "chart_spec": {...}
    }
    """
    rows = _load_rows(dataset_id, extra_filters)
    if not rows:
        return {'labels': [], 'series': None, 'data': [], 'chart_spec': chart_spec}

    x_col = chart_spec.get('x')
    y_col = chart_spec.get('y')
    agg_fn = chart_spec.get('agg', 'sum')
    series_col = chart_spec.get('series')
    sort = chart_spec.get('sort', 'desc')
    limit = chart_spec.get('limit')
    chart_type = chart_spec.get('type', 'bar')

    if chart_type == 'histogram':
        return _histogram(rows, x_col)

    if series_col:
        return _multi_series_agg(rows, x_col, y_col, agg_fn, series_col, sort, limit)
    else:
        return _single_agg(rows, x_col, y_col, agg_fn, sort, limit)


def drilldown(dataset_id, filters, metric_col, agg_fn, dim_cols=None):
    """
    Generic drilldown: given filters + a metric, compute:
    - total value
    - breakdown by best dimensions
    - sunburst hierarchy if 2+ dims available
    """
    rows = _load_rows(dataset_id, filters)
    if not rows:
        return {'total': 0, 'breakdown': [], 'sunburst': None}

    # Compute total
    total = _agg_values([r.get(metric_col) for r in rows], agg_fn)

    # Pick dimensions for breakdown
    if not dim_cols:
        col_profiles = DatasetColumn.query.filter_by(dataset_id=dataset_id).all()
        dim_cols = [
            c.name for c in col_profiles
            if c.inferred_type == 'categorical' and c.cardinality <= 50
        ][:3]

    result = {'total': total, 'breakdowns': {}, 'sunburst': None}

    # Breakdown per dim
    for dim in dim_cols:
        groups = defaultdict(list)
        for r in rows:
            key = str(r.get(dim, '')).strip()
            if key:
                groups[key].append(r.get(metric_col))
        breakdown = [
            {'name': k, 'value': _agg_values(v, agg_fn)}
            for k, v in groups.items()
        ]
        breakdown.sort(key=lambda x: x['value'], reverse=True)
        result['breakdowns'][dim] = breakdown[:15]

    # Sunburst hierarchy (2 dims)
    if len(dim_cols) >= 2:
        result['sunburst'] = _build_sunburst(rows, dim_cols[:2], metric_col, agg_fn)

    return result


# ── Internal helpers ──

def _load_rows(dataset_id, filters=None):
    """Load rows from DB, apply filters."""
    query = DatasetRow.query.filter_by(dataset_id=dataset_id)
    db_rows = query.all()
    rows = [r.data for r in db_rows]

    if filters:
        for f in filters:
            col = f.get('column')
            val = f.get('value')
            op = f.get('op', 'eq')
            if col and val is not None:
                if op == 'eq':
                    rows = [r for r in rows if str(r.get(col, '')).strip() == str(val).strip()]
                elif op == 'in':
                    vals = set(str(v) for v in val) if isinstance(val, list) else {str(val)}
                    rows = [r for r in rows if str(r.get(col, '')).strip() in vals]

    return rows


def _parse_num(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _agg_values(vals, agg_fn):
    nums = [_parse_num(v) for v in vals if v is not None]
    if not nums:
        return 0
    if agg_fn == 'sum':
        return round(sum(nums), 2)
    if agg_fn == 'avg':
        return round(sum(nums) / len(nums), 2)
    if agg_fn == 'min':
        return round(min(nums), 2)
    if agg_fn == 'max':
        return round(max(nums), 2)
    if agg_fn == 'count':
        return len(vals)
    return round(sum(nums), 2)


def _is_date_val(val):
    return bool(re.match(r'^\d{4}[-/]\d{2}', str(val)))


def _sort_labels(labels, sort):
    if sort == 'time':
        return sorted(labels, key=lambda x: str(x))
    return labels  # sorted by value later


def _single_agg(rows, x_col, y_col, agg_fn, sort, limit):
    groups = defaultdict(list)
    for r in rows:
        key = str(r.get(x_col, '')).strip()
        if not key:
            continue
        if y_col and agg_fn != 'count':
            groups[key].append(r.get(y_col))
        else:
            groups[key].append(1)

    entries = [
        {'label': k, 'value': _agg_values(v, agg_fn)}
        for k, v in groups.items()
    ]

    if sort == 'time':
        entries.sort(key=lambda e: e['label'])
    elif sort == 'asc':
        entries.sort(key=lambda e: e['value'])
    else:
        entries.sort(key=lambda e: e['value'], reverse=True)

    if limit:
        entries = entries[:limit]

    return {
        'labels': [e['label'] for e in entries],
        'data': [e['value'] for e in entries],
        'series': None,
    }


def _multi_series_agg(rows, x_col, y_col, agg_fn, series_col, sort, limit):
    # Group by (series, x)
    grouped = defaultdict(lambda: defaultdict(list))
    series_totals = defaultdict(float)

    for r in rows:
        sv = str(r.get(series_col, '')).strip()
        xv = str(r.get(x_col, '')).strip()
        if not sv or not xv:
            continue
        val = r.get(y_col) if y_col else 1
        grouped[sv][xv].append(val)
        series_totals[sv] += _parse_num(val)

    # Top 6 series by total
    top_series = sorted(series_totals.keys(), key=lambda s: series_totals[s], reverse=True)[:6]

    # All labels (union across series)
    all_labels = set()
    for s in top_series:
        all_labels.update(grouped[s].keys())

    if sort == 'time':
        labels = sorted(all_labels)
    else:
        labels = sorted(all_labels)

    if limit and sort != 'time':
        # For non-time, limit labels by total value
        label_totals = {}
        for lbl in labels:
            label_totals[lbl] = sum(
                _agg_values(grouped[s].get(lbl, []), agg_fn) for s in top_series
            )
        labels = sorted(label_totals.keys(), key=lambda l: label_totals[l], reverse=True)[:limit]

    colors = ['cyan', 'violet', 'emerald', 'amber', 'rose', 'blue']
    series = []
    for i, s in enumerate(top_series):
        data = [_agg_values(grouped[s].get(lbl, []), agg_fn) for lbl in labels]
        series.append({'name': s, 'data': data, 'color': colors[i % len(colors)]})

    return {
        'labels': labels,
        'series': series,
        'data': None,
    }


def _histogram(rows, col, bins=20):
    """Compute histogram buckets for a numeric column."""
    vals = []
    for r in rows:
        try:
            vals.append(float(r.get(col, 0)))
        except (ValueError, TypeError):
            pass

    if not vals:
        return {'labels': [], 'data': [], 'series': None}

    mn, mx = min(vals), max(vals)
    if mn == mx:
        return {'labels': [str(mn)], 'data': [len(vals)], 'series': None}

    step = (mx - mn) / bins
    labels = []
    counts = [0] * bins
    for i in range(bins):
        lo = mn + step * i
        labels.append(f"{lo:.0f}")

    for v in vals:
        idx = int((v - mn) / step)
        if idx >= bins:
            idx = bins - 1
        counts[idx] += 1

    return {'labels': labels, 'data': counts, 'series': None}


def _build_sunburst(rows, dims, metric_col, agg_fn):
    """Build sunburst hierarchy from 2 dimensions."""
    d1, d2 = dims[0], dims[1]
    tree = defaultdict(lambda: defaultdict(list))

    for r in rows:
        v1 = str(r.get(d1, '')).strip()
        v2 = str(r.get(d2, '')).strip()
        if v1 and v2:
            tree[v1][v2].append(r.get(metric_col))

    children = []
    for k1, sub in tree.items():
        sub_children = []
        for k2, vals in sub.items():
            sub_children.append({
                'name': k2,
                'value': _agg_values(vals, agg_fn),
            })
        sub_children.sort(key=lambda x: x['value'], reverse=True)
        children.append({
            'name': k1,
            'value': sum(c['value'] for c in sub_children),
            'children': sub_children[:10],
        })
    children.sort(key=lambda x: x['value'], reverse=True)

    return {
        'name': 'All',
        'children': children[:10],
    }
