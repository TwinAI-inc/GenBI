"""
Generic chart suggestion engine.

Given a column profile, returns a list of ChartSpec dicts that make sense for the data.
"""


def suggest_charts(columns_profile, max_charts=8):
    """
    Generate chart suggestions from column profiles.

    Args:
        columns_profile: list of column profile dicts from profiler.profile_columns()
        max_charts: max number of suggestions

    Returns:
        list of ChartSpec dicts
    """
    specs = []
    idx = 0

    dates = [c for c in columns_profile if c['type'] == 'datetime']
    numerics = [c for c in columns_profile if c['type'] == 'numeric']
    categoricals = [c for c in columns_profile if c['type'] == 'categorical']

    # Rank numerics by likely importance (revenue-ish names first)
    _importance_re = [
        r'revenue|sales|income|profit',
        r'cost|expense|spend|amount|price|value|total',
        r'count|quantity|deals|orders',
    ]
    import re

    def _rank_numeric(col):
        name = col['name'].lower()
        for i, pat in enumerate(_importance_re):
            if re.search(pat, name):
                return i
        return len(_importance_re)

    numerics.sort(key=_rank_numeric)

    primary_metric = numerics[0] if numerics else None
    secondary_metric = numerics[1] if len(numerics) > 1 else None
    date_col = dates[0] if dates else None

    # ── 1) Time series for primary metric by best categorical ──
    if date_col and primary_metric and categoricals:
        best_cat = _best_series_cat(categoricals)
        if best_cat:
            specs.append(_spec(
                idx, 'stacked_bar',
                x=date_col['name'], y=primary_metric['name'],
                agg='sum', series=best_cat['name'],
                sort='time',
                title=f"{_label(primary_metric['name'])} by {_label(best_cat['name'])} over Time",
            ))
            idx += 1

    # ── 2) Time series line for primary metric (total) ──
    if date_col and primary_metric and len(specs) < max_charts:
        specs.append(_spec(
            idx, 'line',
            x=date_col['name'], y=primary_metric['name'],
            agg='sum', sort='time',
            title=f"{_label(primary_metric['name'])} Trend",
        ))
        idx += 1

    # ── 3) Bar: primary metric by top categorical ──
    if primary_metric and categoricals and len(specs) < max_charts:
        best_bar_cat = _best_bar_cat(categoricals)
        if best_bar_cat:
            specs.append(_spec(
                idx, 'bar',
                x=best_bar_cat['name'], y=primary_metric['name'],
                agg='sum', sort='desc', limit=10,
                title=f"{_label(primary_metric['name'])} by {_label(best_bar_cat['name'])}",
            ))
            idx += 1

    # ── 4) Donut: count by top categorical ──
    if categoricals and len(specs) < max_charts:
        donut_cat = _best_bar_cat(categoricals)
        if donut_cat:
            specs.append(_spec(
                idx, 'donut',
                x=donut_cat['name'], y=None,
                agg='count', limit=8,
                title=f"Distribution by {_label(donut_cat['name'])}",
            ))
            idx += 1

    # ── 5) Secondary metric bar ──
    if secondary_metric and categoricals and len(specs) < max_charts:
        cat = _best_bar_cat(categoricals)
        if cat:
            is_rate = bool(re.search(r'rate|pct|percent|avg|score', secondary_metric['name'], re.I))
            specs.append(_spec(
                idx, 'bar',
                x=cat['name'], y=secondary_metric['name'],
                agg='avg' if is_rate else 'sum',
                sort='desc', limit=10,
                title=f"{_label(secondary_metric['name'])} by {_label(cat['name'])}",
            ))
            idx += 1

    # ── 6) Histogram for primary numeric ──
    if primary_metric and len(specs) < max_charts:
        specs.append(_spec(
            idx, 'histogram',
            x=primary_metric['name'], y=None,
            agg='count',
            title=f"{_label(primary_metric['name'])} Distribution",
        ))
        idx += 1

    # ── 7) Time series for secondary metric ──
    if date_col and secondary_metric and len(specs) < max_charts:
        specs.append(_spec(
            idx, 'line',
            x=date_col['name'], y=secondary_metric['name'],
            agg='sum', sort='time',
            title=f"{_label(secondary_metric['name'])} Trend",
        ))
        idx += 1

    # ── 8) Stacked bar with two categoricals ──
    if len(categoricals) >= 2 and primary_metric and len(specs) < max_charts:
        c1 = categoricals[0]
        c2 = categoricals[1]
        specs.append(_spec(
            idx, 'stacked_bar',
            x=c1['name'], y=primary_metric['name'],
            agg='sum', series=c2['name'], limit=10,
            title=f"{_label(primary_metric['name'])} by {_label(c1['name'])} & {_label(c2['name'])}",
        ))
        idx += 1

    return specs[:max_charts]


def _spec(idx, chart_type, x, y, agg, series=None, sort=None, limit=None, title=''):
    return {
        'chart_id': f'auto_{idx}',
        'type': chart_type,
        'x': x,
        'y': y,
        'agg': agg,
        'series': series,
        'filters': [],
        'limit': limit,
        'sort': sort or ('time' if chart_type == 'line' else 'desc'),
        'title': title,
    }


def _label(col_name):
    """Convert column name to title case label."""
    return col_name.replace('_', ' ').title()


def _best_series_cat(categoricals):
    """Best categorical for multi-series (2-6 unique values)."""
    for c in categoricals:
        if 2 <= c['cardinality'] <= 6:
            return c
    for c in categoricals:
        if 2 <= c['cardinality'] <= 10:
            return c
    return None


def _best_bar_cat(categoricals):
    """Best categorical for bar charts (3-20 unique)."""
    for c in categoricals:
        if 3 <= c['cardinality'] <= 20:
            return c
    return categoricals[0] if categoricals else None
