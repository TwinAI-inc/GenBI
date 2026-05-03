"""
GenBI Cost Decomposition Engine

Auto-detects cost columns, categorical dimensions, and time periods in
arbitrary datasets. Decomposes costs by each dimension, builds waterfall
change data, computes efficiency metrics, and optionally generates
LLM-driven narratives with recommendations.

Pure functions -- no Flask dependencies.
"""

import json
import logging
import math
import re
from collections import defaultdict

from services.azure_ai_client import chat_completion_json

logger = logging.getLogger(__name__)

# -- Column detection patterns -----------------------------------------------

# Cost-column regex deliberately does NOT include 'budget' / 'target' /
# 'forecast' / 'plan' / 'projected' — those words map to the BUDGET column
# (the comparison column) and should never be picked up as the primary
# cost. Without this separation, a dataset with both `cost` and `budget`
# columns would treat the budget as a second cost and never trigger the
# variance computation.
_COST_COL = re.compile(
    r'cost|expense|spend|spending|price|invoice|payable|payment|investment|charges?|fees?|outlay', re.I
)
_TIME_COL = re.compile(
    r'date|month|year|quarter|week|period|time|day', re.I
)
_CATEGORICAL_SKIP = re.compile(
    r'id$|_id$|uuid|guid|url|link|path|hash|token|key$', re.I
)
_QUANTITY_COL = re.compile(
    r'count|qty|quantity|units?|enroll|headcount|fte|volume|records?|patients?|sites?|subjects?', re.I
)

# Cardinality bounds for treating a column as a categorical dimension
_MIN_CATEGORIES = 2
_MAX_CATEGORIES = 50

# Severity thresholds for cost changes (absolute pct)
_CHANGE_THRESHOLDS = {'low': 5, 'medium': 15, 'high': 30}


# -- Public API --------------------------------------------------------------

def analyze_costs(headers, rows, max_sample=500):
    """
    Main cost analysis entry point.

    Parameters
    ----------
    headers : list[str]
        Column names from the dataset.
    rows : list[dict]
        Row dicts keyed by header name.
    max_sample : int
        Cap on rows to process (for performance).

    Returns
    -------
    dict -- structured cost analysis result.
    """
    if not headers or not rows:
        return _empty_result()

    sample = rows[:max_sample]

    cost_cols = _detect_cost_columns(headers, sample)
    if not cost_cols:
        logger.info('No cost columns detected -- returning empty result')
        return _empty_result()

    cat_cols = _detect_categorical_columns(headers, sample, cost_cols)
    time_col = _detect_time_column(headers)
    quantity_cols = _detect_quantity_columns(headers, sample)

    logger.info(
        f'Cost columns: {cost_cols}, categoricals: {cat_cols}, '
        f'time: {time_col}, quantity: {quantity_cols}'
    )

    primary_cost = cost_cols[0]
    total_cost = _column_sum(sample, primary_cost)

    decomposition_by_dim = []
    for cat in cat_cols:
        breakdown = _decompose_by_dimension(sample, primary_cost, cat, time_col)
        decomposition_by_dim.append(breakdown)

    waterfall = _build_waterfall(sample, primary_cost, cat_cols, time_col)
    top_drivers = _extract_top_drivers(decomposition_by_dim, total_cost)
    efficiency = _compute_efficiency(sample, primary_cost, quantity_cols)

    # F1–F4 enrichments. Each is independently available — caller can
    # render whichever blocks come back populated.
    forecast = forecast_cost_series(sample, primary_cost, time_col, horizon=3)
    tornado = tornado_cost_drivers(decomposition_by_dim, total_cost,
                                   swing_pct=20.0, max_drivers=8)
    anomalies = detect_cost_anomalies(sample, primary_cost, time_col,
                                      z_threshold=2.5)
    budget_col = detect_budget_column(headers, sample, primary_cost)
    budget_variance = []
    if budget_col:
        budget_variance = compute_budget_variance(sample, primary_cost,
                                                  budget_col, cat_cols)

    return {
        'total_cost': round(total_cost, 2),
        'cost_columns': cost_cols,
        'primary_cost': primary_cost,
        'time_column': time_col,
        'budget_column': budget_col,
        'decomposition': {
            'by_dimension': decomposition_by_dim,
            'waterfall': waterfall,
        },
        'top_drivers': top_drivers,
        'efficiency': efficiency,
        'forecast': forecast,
        'tornado': tornado,
        'anomalies': anomalies,
        'budget_variance': budget_variance,
    }


def generate_cost_narrative(cost_data):
    """
    Call LLM to produce a plain-English narrative and recommendations.

    Parameters
    ----------
    cost_data : dict
        Output of analyze_costs().

    Returns
    -------
    dict -- {"narrative": "...", "recommendations": [...]}
    """
    if not cost_data or cost_data.get('total_cost', 0) == 0:
        return {
            'narrative': 'Insufficient cost data for narrative generation.',
            'recommendations': [],
        }

    # Build a concise summary for the LLM (abbreviated numbers)
    total_str = f'${_fmt_abbrev(cost_data["total_cost"])}'

    dim_bullets = []
    for dim_block in cost_data.get('decomposition', {}).get('by_dimension', [])[:4]:
        dim_name = dim_block.get('dimension', '?')
        top_cats = dim_block.get('breakdown', [])[:3]
        cats_str = ', '.join(
            f"{c['category']} ${_fmt_abbrev(c['cost'])} ({_fmt_pct(c['pct'])})"
            + (f" chg {c['change_vs_prior']:+.1f}%" if c.get('change_vs_prior') is not None else '')
            for c in top_cats
        )
        dim_bullets.append(f'- By {dim_name}: {cats_str}')

    driver_bullets = []
    for d in cost_data.get('top_drivers', [])[:5]:
        driver_bullets.append(f'- [{d["severity"].upper()}] {d["driver"]} ({d["impact"]})')

    wf = cost_data.get('decomposition', {}).get('waterfall', {})
    waterfall_summary = ''
    if wf and wf.get('changes'):
        changes_str = ', '.join(
            f"{c['label']} {'+' if c['value'] >= 0 else ''}{_fmt_abbrev_dollar(c['value'])}"
            for c in wf['changes'][:5]
        )
        waterfall_summary = (
            f"Waterfall: {wf['start']['label']} ${_fmt_abbrev(wf['start']['value'])} "
            f"-> {wf['end']['label']} ${_fmt_abbrev(wf['end']['value'])}. "
            f"Key changes: {changes_str}"
        )

    eff = cost_data.get('efficiency', {})
    eff_str = ''
    if eff.get('cost_per_record') is not None:
        eff_str = f"Cost/record: ${_fmt_abbrev(eff['cost_per_record'])}, trend: {eff.get('cost_trend', 'stable')}"

    prompt = f"""You are a financial analyst for a BI dashboard. Analyze the following cost decomposition and provide:
1. A concise narrative (3-5 sentences) explaining WHY costs are at their current level and what is driving changes.
2. A prioritized list of 3-5 actionable recommendations to optimize costs.

Use abbreviated dollar amounts ($1.2M, $450K, etc.).

COST SUMMARY:
- Total cost: {total_str}
- Cost columns: {', '.join(cost_data.get('cost_columns', []))}

DECOMPOSITION:
{chr(10).join(dim_bullets) if dim_bullets else '- No dimensional breakdown available'}

TOP DRIVERS:
{chr(10).join(driver_bullets) if driver_bullets else '- No major drivers identified'}

{waterfall_summary}

{eff_str}

Return JSON: {{"narrative": "...", "recommendations": ["...", "..."]}}"""

    try:
        parsed, _usage = chat_completion_json(
            prompt,
            system='You are a senior financial analyst. Be specific, cite category names and dollar amounts. Keep recommendations actionable and tied to the data.',
            temperature=0.3,
            max_tokens=1500,
        )
        narrative = parsed.get('narrative', '')
        recommendations = parsed.get('recommendations', [])
        if isinstance(recommendations, str):
            recommendations = [recommendations]
        return {
            'narrative': narrative,
            'recommendations': recommendations,
        }
    except json.JSONDecodeError as e:
        logger.error(f'Failed to parse cost narrative JSON: {e}')
        return _fallback_narrative(cost_data)
    except Exception as e:
        logger.error(f'Cost narrative generation failed: {e}')
        return _fallback_narrative(cost_data)


# -- Column Detection --------------------------------------------------------

def _detect_cost_columns(headers, sample):
    """
    Find all columns whose name matches a cost-related pattern AND whose
    values are predominantly numeric. Return list sorted by total descending.
    """
    candidates = []
    for h in headers:
        if _COST_COL.search(h):
            vals = _numeric_vals(sample, h)
            if len(vals) >= max(1, len(sample) * 0.3):
                total = sum(vals)
                candidates.append((h, total))

    # Sort by total cost descending so primary_cost is the largest
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates]


def _detect_categorical_columns(headers, sample, cost_cols):
    """
    Identify columns suitable for cost decomposition (categorical dimensions).
    A column qualifies if it has 2-50 unique values and is not numeric, not a
    cost column, not a time column, and not an ID column.
    """
    skip_set = set(cost_cols)
    categoricals = []

    for h in headers:
        if h in skip_set:
            continue
        if _CATEGORICAL_SKIP.search(h):
            continue
        if _TIME_COL.search(h):
            continue

        vals = [str(r.get(h, '')).strip() for r in sample if r.get(h) not in (None, '')]
        if not vals:
            continue

        unique = set(vals)
        if _MIN_CATEGORIES <= len(unique) <= _MAX_CATEGORIES:
            if not _is_numeric_column(vals):
                categoricals.append(h)

    return categoricals


def _detect_time_column(headers):
    """Return the first header that matches a time pattern, or None."""
    for h in headers:
        if _TIME_COL.search(h):
            return h
    return None


def _detect_quantity_columns(headers, sample):
    """
    Find numeric columns that look like counts/quantities for efficiency
    calculations.
    """
    candidates = []
    for h in headers:
        if _QUANTITY_COL.search(h):
            vals = _numeric_vals(sample, h)
            if len(vals) >= max(1, len(sample) * 0.3):
                candidates.append(h)
    return candidates


# -- Cost Decomposition by Dimension -----------------------------------------

def _decompose_by_dimension(rows, cost_col, cat_col, time_col):
    """
    For a given cost column and categorical column, compute the breakdown:
    cost, pct, and period-over-period change for each category.
    """
    # Group rows by category
    groups = defaultdict(list)
    for r in rows:
        cat = str(r.get(cat_col, '')).strip()
        if cat:
            groups[cat].append(r)

    total_cost = _column_sum(rows, cost_col)
    if total_cost == 0:
        total_cost = 1.0  # Avoid division by zero

    # Compute period-based changes if time column exists
    period_costs = {}
    if time_col:
        period_costs = _compute_period_costs(rows, cost_col, cat_col, time_col)

    breakdown = []
    for cat, cat_rows in groups.items():
        cat_cost = _column_sum(cat_rows, cost_col)
        pct = (cat_cost / total_cost) * 100

        change = None
        if cat in period_costs:
            change = period_costs[cat]

        breakdown.append({
            'category': cat,
            'cost': round(cat_cost, 2),
            'pct': round(pct, 1),
            'change_vs_prior': round(change, 1) if change is not None else None,
        })

    # Sort by cost descending
    breakdown.sort(key=lambda x: x['cost'], reverse=True)

    return {
        'dimension': cat_col,
        'breakdown': breakdown,
    }


def _compute_period_costs(rows, cost_col, cat_col, time_col):
    """
    Split rows into two halves by time period and compute pct change per
    category between the earlier half and the later half.

    Returns dict of category -> pct change.
    """
    # Collect all time values and sort them
    time_vals = sorted(set(
        str(r.get(time_col, '')).strip()
        for r in rows
        if r.get(time_col) not in (None, '')
    ))

    if len(time_vals) < 2:
        return {}

    # Split into prior and current periods at midpoint
    mid = len(time_vals) // 2
    prior_periods = set(time_vals[:mid])
    current_periods = set(time_vals[mid:])

    # Accumulate costs by category for each period group
    prior_costs = defaultdict(float)
    current_costs = defaultdict(float)

    for r in rows:
        t = str(r.get(time_col, '')).strip()
        cat = str(r.get(cat_col, '')).strip()
        val = _safe_float(r.get(cost_col))
        if not t or not cat or val is None:
            continue

        if t in prior_periods:
            prior_costs[cat] += val
        elif t in current_periods:
            current_costs[cat] += val

    # Compute pct change
    result = {}
    all_cats = set(prior_costs.keys()) | set(current_costs.keys())
    for cat in all_cats:
        prior = prior_costs.get(cat, 0)
        current = current_costs.get(cat, 0)
        if prior > 0:
            result[cat] = ((current - prior) / prior) * 100
        elif current > 0:
            result[cat] = 100.0  # New category, treat as 100% increase
        # If both are zero, skip

    return result


# -- Waterfall Builder -------------------------------------------------------

def _build_waterfall(rows, cost_col, cat_cols, time_col):
    """
    Build waterfall data showing starting total, per-category changes, and
    ending total. Uses the best categorical dimension (highest variance in
    period changes) and the time column.
    """
    if not time_col or not cat_cols:
        # Without both time and categoricals, build a simple waterfall from
        # the single best categorical showing contribution to total
        return _build_static_waterfall(rows, cost_col, cat_cols)

    # Collect all time values and sort them
    time_vals = sorted(set(
        str(r.get(time_col, '')).strip()
        for r in rows
        if r.get(time_col) not in (None, '')
    ))

    if len(time_vals) < 2:
        return _build_static_waterfall(rows, cost_col, cat_cols)

    # Split into prior and current halves
    mid = len(time_vals) // 2
    prior_periods = set(time_vals[:mid])
    current_periods = set(time_vals[mid:])

    prior_rows = [r for r in rows if str(r.get(time_col, '')).strip() in prior_periods]
    current_rows = [r for r in rows if str(r.get(time_col, '')).strip() in current_periods]

    prior_total = _column_sum(prior_rows, cost_col)
    current_total = _column_sum(current_rows, cost_col)

    # Pick the categorical with the most interesting changes
    best_cat = cat_cols[0]
    best_variance = -1
    for cat in cat_cols:
        changes = _compute_period_costs(rows, cost_col, cat, time_col)
        if changes:
            vals = list(changes.values())
            variance = _variance(vals)
            if variance > best_variance:
                best_variance = variance
                best_cat = cat

    # Build the change items using the best categorical
    period_costs = _compute_period_costs(rows, cost_col, best_cat, time_col)

    # Compute absolute dollar changes per category
    prior_by_cat = defaultdict(float)
    current_by_cat = defaultdict(float)
    for r in prior_rows:
        cat = str(r.get(best_cat, '')).strip()
        val = _safe_float(r.get(cost_col))
        if cat and val is not None:
            prior_by_cat[cat] += val
    for r in current_rows:
        cat = str(r.get(best_cat, '')).strip()
        val = _safe_float(r.get(cost_col))
        if cat and val is not None:
            current_by_cat[cat] += val

    changes = []
    all_cats = set(prior_by_cat.keys()) | set(current_by_cat.keys())
    for cat in all_cats:
        delta = current_by_cat.get(cat, 0) - prior_by_cat.get(cat, 0)
        if abs(delta) < 0.01:
            continue
        changes.append({
            'label': cat,
            'value': round(delta, 2),
            'type': 'increase' if delta > 0 else 'decrease',
        })

    # Sort by absolute impact descending
    changes.sort(key=lambda c: abs(c['value']), reverse=True)

    # Keep top 10 changes, roll remaining into "Other"
    if len(changes) > 10:
        top_changes = changes[:9]
        other_val = sum(c['value'] for c in changes[9:])
        if abs(other_val) >= 0.01:
            top_changes.append({
                'label': 'Other changes',
                'value': round(other_val, 2),
                'type': 'increase' if other_val > 0 else 'decrease',
            })
        changes = top_changes

    return {
        'start': {
            'label': 'Previous Period',
            'value': round(prior_total, 2),
        },
        'changes': changes,
        'end': {
            'label': 'Current Period',
            'value': round(current_total, 2),
        },
    }


def _build_static_waterfall(rows, cost_col, cat_cols):
    """
    Fallback waterfall when no time column exists: show how each category
    in the best dimension contributes to the total.
    """
    total = _column_sum(rows, cost_col)

    if not cat_cols:
        return {
            'start': {'label': 'Total', 'value': round(total, 2)},
            'changes': [],
            'end': {'label': 'Total', 'value': round(total, 2)},
        }

    # Use the first categorical dimension
    cat_col = cat_cols[0]

    groups = defaultdict(float)
    for r in rows:
        cat = str(r.get(cat_col, '')).strip()
        val = _safe_float(r.get(cost_col))
        if cat and val is not None:
            groups[cat] += val

    # Sort by value descending
    sorted_cats = sorted(groups.items(), key=lambda x: x[1], reverse=True)

    changes = []
    for cat, val in sorted_cats[:10]:
        changes.append({
            'label': cat,
            'value': round(val, 2),
            'type': 'increase',
        })

    # Roll up remainder
    if len(sorted_cats) > 10:
        other_val = sum(v for _, v in sorted_cats[10:])
        if other_val > 0:
            changes.append({
                'label': 'Other',
                'value': round(other_val, 2),
                'type': 'increase',
            })

    return {
        'start': {'label': 'Base (0)', 'value': 0},
        'changes': changes,
        'end': {'label': 'Total', 'value': round(total, 2)},
    }


# -- Top Drivers Extraction --------------------------------------------------

def _extract_top_drivers(decomposition_by_dim, total_cost, max_drivers=10):
    """
    Pull out the most impactful cost drivers across all dimensions, based
    on both absolute pct contribution and period-over-period change.
    """
    drivers = []

    for dim_block in decomposition_by_dim:
        dim_name = dim_block.get('dimension', '?')
        for entry in dim_block.get('breakdown', []):
            cat = entry['category']
            cost = entry['cost']
            pct = entry['pct']
            change = entry.get('change_vs_prior')

            # Driver from high concentration (any single category > 30% of total)
            if pct >= 30:
                drivers.append({
                    'driver': f'{cat} accounts for {_fmt_pct(pct)} of total cost',
                    'impact': f'${_fmt_abbrev(cost)}',
                    'severity': _change_severity(pct),
                    '_sort_score': pct,
                })

            # Driver from significant period change
            if change is not None and abs(change) >= _CHANGE_THRESHOLDS['low']:
                direction = 'grew' if change > 0 else 'declined'
                abs_change = abs(change)
                if total_cost > 0:
                    dollar_impact = cost * (abs_change / 100)
                else:
                    dollar_impact = 0

                drivers.append({
                    'driver': f'{cat} ({dim_name}) {direction} {abs_change:.0f}%',
                    'impact': f'${_fmt_abbrev(dollar_impact)} {"increase" if change > 0 else "decrease"}',
                    'severity': _change_severity(abs_change),
                    '_sort_score': abs_change,
                })

    # De-duplicate by driver text (keep highest score)
    seen = {}
    for d in drivers:
        key = d['driver']
        if key not in seen or d['_sort_score'] > seen[key]['_sort_score']:
            seen[key] = d

    result = sorted(seen.values(), key=lambda d: d['_sort_score'], reverse=True)

    # Strip internal sort key
    for d in result[:max_drivers]:
        del d['_sort_score']

    return result[:max_drivers]


# -- Efficiency Metrics ------------------------------------------------------

def _compute_efficiency(rows, cost_col, quantity_cols):
    """
    Compute cost-per-unit metrics using the primary cost column and any
    detected quantity columns.
    """
    total_cost = _column_sum(rows, cost_col)
    record_count = len(rows)

    if record_count == 0:
        return {
            'cost_per_record': None,
            'cost_trend': 'unknown',
            'metrics': [],
        }

    cost_per_record = total_cost / record_count

    # Additional per-quantity metrics
    metrics = []
    for qcol in quantity_cols:
        total_qty = _column_sum(rows, qcol)
        if total_qty > 0:
            per_unit = total_cost / total_qty
            metrics.append({
                'name': f'cost_per_{_sanitize_name(qcol)}',
                'label': f'Cost per {qcol}',
                'value': round(per_unit, 2),
            })

    # Estimate cost trend from row order (first half vs second half)
    cost_trend = _estimate_cost_trend(rows, cost_col)

    return {
        'cost_per_record': round(cost_per_record, 2),
        'cost_trend': cost_trend,
        'metrics': metrics,
    }


def _estimate_cost_trend(rows, cost_col):
    """
    Estimate whether cost is increasing, decreasing, or stable by comparing
    the average cost in the first half of rows vs the second half.
    """
    if len(rows) < 4:
        return 'stable'

    mid = len(rows) // 2
    first_half = _numeric_vals(rows[:mid], cost_col)
    second_half = _numeric_vals(rows[mid:], cost_col)

    if not first_half or not second_half:
        return 'stable'

    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)

    if avg_first == 0:
        return 'increasing' if avg_second > 0 else 'stable'

    change_pct = ((avg_second - avg_first) / avg_first) * 100

    if change_pct > 10:
        return 'increasing'
    elif change_pct < -10:
        return 'decreasing'
    return 'stable'


# -- Fallback Narrative ------------------------------------------------------

def _fallback_narrative(cost_data):
    """
    Generate a rule-based narrative when LLM is unavailable.
    """
    total = cost_data.get('total_cost', 0)
    dims = cost_data.get('decomposition', {}).get('by_dimension', [])
    drivers = cost_data.get('top_drivers', [])
    efficiency = cost_data.get('efficiency', {})

    narrative = f'Total cost is ${_fmt_abbrev(total)}'

    # Mention top dimension
    if dims:
        top_dim = dims[0]
        dim_name = top_dim.get('dimension', '?')
        top_cats = top_dim.get('breakdown', [])[:2]
        if top_cats:
            cat_strs = [
                f'{c["category"]} (${_fmt_abbrev(c["cost"])}, {_fmt_pct(c["pct"])})'
                for c in top_cats
            ]
            narrative += f'. By {dim_name}, the largest contributors are {" and ".join(cat_strs)}'

    # Mention trend
    trend = efficiency.get('cost_trend', 'stable')
    if trend != 'stable':
        narrative += f'. Cost trend is {trend}'

    narrative += '.'

    recs = []
    if drivers:
        for d in drivers[:3]:
            recs.append(f'{d["driver"]} -- {d["impact"]}.')

    if not recs:
        recs.append('Continue monitoring cost distribution; no significant anomalies detected.')

    return {'narrative': narrative, 'recommendations': recs}


# -- Helper Functions --------------------------------------------------------

def _safe_float(v):
    """Try to parse a value as float, return None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        if isinstance(v, str):
            cleaned = re.sub(r'[$,% ]', '', v)
            try:
                return float(cleaned)
            except (ValueError, TypeError):
                return None
        return None


def _numeric_vals(rows, col):
    """Extract numeric values from a column, skipping nulls/strings."""
    if not col:
        return []
    vals = []
    for r in rows:
        v = _safe_float(r.get(col))
        if v is not None:
            vals.append(v)
    return vals


def _column_sum(rows, col):
    """Sum all numeric values in a column."""
    return sum(_numeric_vals(rows, col))


def _is_numeric_column(vals):
    """Return True if >80% of non-empty values parse as float."""
    if not vals:
        return False
    numeric_count = sum(1 for v in vals if _safe_float(v) is not None)
    return numeric_count > len(vals) * 0.8


def _variance(vals):
    """Compute variance of a list of numbers."""
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def _change_severity(abs_change):
    """Map an absolute percentage change to a severity label."""
    if abs_change >= _CHANGE_THRESHOLDS['high']:
        return 'high'
    if abs_change >= _CHANGE_THRESHOLDS['medium']:
        return 'medium'
    return 'low'


# ════════════════════════════════════════════════════════════════════════════
# F1 — Time-series forecast (Holt's linear trend exponential smoothing)
# ════════════════════════════════════════════════════════════════════════════

def forecast_cost_series(rows, cost_col, time_col, horizon=3):
    """Aggregate cost by time period and forecast ``horizon`` periods ahead.

    Uses Holt's linear trend method (double exponential smoothing). Returns
    historical points + forecast with 80% / 95% confidence intervals based
    on the in-sample residual standard deviation.

    Hardened:
      - Returns ``{'available': False, 'reason': ...}`` if fewer than 4
        usable periods, no time column, or no cost column. The caller
        inspects ``available`` instead of getting a malformed payload.
      - Skips periods with non-finite aggregates and re-checks the count.
      - alpha / beta auto-tuned in [0.1, 0.9] by minimising in-sample SSE.
      - Residual std clamped at >= 1e-9 so the CI band is never zero-width
        on perfectly linear input (which would otherwise mislead the user
        into thinking the forecast is exact).
      - Forecast values clipped at >= 0 — costs cannot be negative; if
        Holt projects below zero we clamp and flag with ``clipped=True``.
    """
    if not cost_col or not time_col or not rows:
        return {'available': False, 'reason': 'missing_inputs'}

    # Aggregate cost by time period (sum)
    by_period = defaultdict(float)
    counts = defaultdict(int)
    for r in rows:
        t = r.get(time_col)
        if t is None or t == '':
            continue
        c = _safe_float(r.get(cost_col))
        if c is None or not math.isfinite(c):
            continue
        key = str(t)
        by_period[key] += c
        counts[key] += 1

    # Sort periods. Try date-ish parsing first, fall back to string sort.
    periods = list(by_period.keys())
    try:
        periods.sort(key=lambda s: (len(s), s))
    except Exception:
        pass
    series = [by_period[p] for p in periods if math.isfinite(by_period[p])]
    if len(series) < 4:
        return {'available': False, 'reason': 'insufficient_history',
                'min_required': 4, 'actual': len(series)}

    n = len(series)

    # Holt's method: search over (alpha, beta) for the pair that minimises
    # in-sample SSE. Small grid keeps this O(81 * n) which is trivial.
    def holt_run(alpha, beta):
        level = series[0]
        trend = series[1] - series[0]
        fits = [series[0]]
        for t in range(1, n):
            prev_level = level
            level = alpha * series[t] + (1 - alpha) * (prev_level + trend)
            trend = beta * (level - prev_level) + (1 - beta) * trend
            fits.append(prev_level + trend)
        sse = sum((series[t] - fits[t]) ** 2 for t in range(n))
        return level, trend, fits, sse

    best = None
    for a in [i / 10 for i in range(1, 10)]:
        for b in [i / 10 for i in range(1, 10)]:
            try:
                level, trend, fits, sse = holt_run(a, b)
            except Exception:
                continue
            if not math.isfinite(sse):
                continue
            if best is None or sse < best['sse']:
                best = {'alpha': a, 'beta': b, 'level': level,
                        'trend': trend, 'fits': fits, 'sse': sse}

    if best is None:
        return {'available': False, 'reason': 'fit_failed'}

    # Residual std for CI bands. Skip the first 2 fits — t=0 is initialised
    # to series[0] and t=1 is mechanically series[1] given the warm-up
    # trend, so both are zero-residual by construction. Including them
    # would collapse the residual std (and the entire CI band) to ~0 on
    # any clean signal, which would mislead the user into treating the
    # forecast as exact.
    residuals = [series[t] - best['fits'][t] for t in range(2, n)]
    if len(residuals) >= 2:
        rmean = sum(residuals) / len(residuals)
        rvar = sum((r - rmean) ** 2 for r in residuals) / max(1, len(residuals) - 1)
        rstd = max(math.sqrt(max(rvar, 0.0)), 0.01 * (max(series) - min(series)) or 1.0)
    else:
        # Fall back to a fraction of the series spread so the CI band is
        # never invisible — if we know nothing, signal "wide uncertainty".
        spread = max(series) - min(series)
        rstd = max(0.05 * spread, 1.0)

    # Forecast h steps ahead. CI band widens with sqrt(h) for random-walk
    # error accumulation — a defensible approximation for Holt without
    # pulling in a real state-space library.
    forecasts = []
    for h in range(1, horizon + 1):
        pt = best['level'] + h * best['trend']
        clipped = False
        if pt < 0:
            pt = 0.0
            clipped = True
        sigma_h = rstd * math.sqrt(h)
        forecasts.append({
            'h': h,
            'point': round(pt, 2),
            'lo80': round(max(0.0, pt - 1.282 * sigma_h), 2),
            'hi80': round(pt + 1.282 * sigma_h, 2),
            'lo95': round(max(0.0, pt - 1.96 * sigma_h), 2),
            'hi95': round(pt + 1.96 * sigma_h, 2),
            'clipped_negative': clipped,
        })

    return {
        'available': True,
        'time_col': time_col,
        'cost_col': cost_col,
        'history': [{'period': p, 'value': round(v, 2)}
                    for p, v in zip(periods, series)],
        'forecast': forecasts,
        'fit_alpha': round(best['alpha'], 2),
        'fit_beta': round(best['beta'], 2),
        'residual_std': round(rstd, 2),
        'mape_pct': round(_mape(series, best['fits']), 2),
    }


def _mape(actual, fitted):
    """Mean Absolute Percentage Error on the genuine fitted region.

    Skips index 0 and 1 — those are determined by the Holt initialisation
    and are zero-residual by construction, so including them artificially
    deflates the error and makes the model look better than it is.
    """
    pairs = [(a, f) for k, (a, f) in enumerate(zip(actual, fitted))
             if k >= 2 and a != 0 and math.isfinite(a) and math.isfinite(f)]
    if not pairs:
        return 0.0
    return 100.0 * sum(abs((a - f) / a) for a, f in pairs) / len(pairs)


# ════════════════════════════════════════════════════════════════════════════
# F2 — Tornado / sensitivity analysis on top cost drivers
# ════════════════════════════════════════════════════════════════════════════

def tornado_cost_drivers(decomposition_by_dim, total_cost, swing_pct=20.0,
                         max_drivers=8):
    """One-at-a-time perturbation of top categorical contributors.

    For each top driver we compute total cost when that driver's contribution
    is shifted by ±swing_pct (default 20%). Bars are returned sorted by the
    absolute swing — the steepest mover sits at the top, classic tornado
    layout.

    Reuses the existing decomposition output. No extra LLM call.
    """
    bars = []
    seen = set()
    for dim_block in decomposition_by_dim:
        dim_name = dim_block.get('dimension', '')
        for cat in dim_block.get('breakdown', [])[:max_drivers]:
            cat_name = cat.get('category', '')
            cat_cost = float(cat.get('cost', 0) or 0)
            key = (dim_name, cat_name)
            if key in seen or cat_cost <= 0 or not math.isfinite(cat_cost):
                continue
            seen.add(key)
            delta = cat_cost * (swing_pct / 100.0)
            low_total = total_cost - delta
            high_total = total_cost + delta
            bars.append({
                'dimension': dim_name,
                'category': cat_name,
                'base_cost': round(cat_cost, 2),
                'low_total': round(max(0.0, low_total), 2),
                'high_total': round(high_total, 2),
                'swing': round(2 * delta, 2),
                'swing_pct_of_total': round(
                    100.0 * (2 * delta) / total_cost, 2) if total_cost > 0 else 0,
            })

    bars.sort(key=lambda b: b['swing'], reverse=True)
    return bars[:max_drivers]


# ════════════════════════════════════════════════════════════════════════════
# F3 — Anomaly detection (z-score on per-period cost)
# ════════════════════════════════════════════════════════════════════════════

def detect_cost_anomalies(rows, cost_col, time_col, z_threshold=2.5,
                          max_anomalies=20):
    """Flag periods whose total cost deviates from the historical mean by
    more than ``z_threshold`` standard deviations.

    Hardened:
      - Returns empty list on fewer than 5 periods (z-score on a tiny
        sample is meaningless).
      - Std clamped at 1e-9 to avoid divide-by-zero on flat series.
    """
    if not cost_col or not time_col or not rows:
        return []

    by_period = defaultdict(float)
    for r in rows:
        t = r.get(time_col)
        if t is None or t == '':
            continue
        c = _safe_float(r.get(cost_col))
        if c is None or not math.isfinite(c):
            continue
        by_period[str(t)] += c

    periods = list(by_period.keys())
    if len(periods) < 5:
        return []

    vals = [by_period[p] for p in periods]
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = max(math.sqrt(max(var, 0.0)), 1e-9)

    anomalies = []
    for p, v in zip(periods, vals):
        z = (v - mean) / std
        if abs(z) >= z_threshold:
            anomalies.append({
                'period': p,
                'value': round(v, 2),
                'z_score': round(z, 2),
                'severity': 'high' if abs(z) >= 3 else 'medium',
                'direction': 'spike' if z > 0 else 'dip',
                'deviation_pct': round(100.0 * (v - mean) / max(abs(mean), 1e-9), 1),
            })

    anomalies.sort(key=lambda a: abs(a['z_score']), reverse=True)
    return anomalies[:max_anomalies]


# ════════════════════════════════════════════════════════════════════════════
# F4 — Budget variance (actual vs target columns)
# ════════════════════════════════════════════════════════════════════════════

_BUDGET_COL = re.compile(r'budget|target|forecast|plan|projected', re.I)


def detect_budget_column(headers, sample, cost_col):
    """Return the first numeric column whose name matches a budget pattern,
    excluding the primary cost column itself."""
    for h in headers:
        if h == cost_col:
            continue
        if not _BUDGET_COL.search(h):
            continue
        vals = [r.get(h) for r in sample if r.get(h) not in (None, '')]
        if _is_numeric_column(vals):
            return h
    return None


def compute_budget_variance(rows, cost_col, budget_col, dim_cols,
                            max_dim_categories=20):
    """Per-dimension actual vs budget variance.

    Returns a list of { dimension, breakdown: [{category, actual, budget,
    variance, variance_pct, severity}] } where severity is on:
      - 'over' if actual exceeds budget by >10%
      - 'on_track' if within ±10%
      - 'under' if actual is more than 10% below budget
    """
    if not cost_col or not budget_col or not rows or not dim_cols:
        return []

    out = []
    for dim in dim_cols:
        groups = defaultdict(lambda: {'actual': 0.0, 'budget': 0.0})
        for r in rows:
            cat = r.get(dim)
            if cat is None or cat == '':
                continue
            a = _safe_float(r.get(cost_col))
            b = _safe_float(r.get(budget_col))
            if a is not None and math.isfinite(a):
                groups[str(cat)]['actual'] += a
            if b is not None and math.isfinite(b):
                groups[str(cat)]['budget'] += b
        breakdown = []
        for cat, vals in groups.items():
            actual = vals['actual']
            budget = vals['budget']
            if budget <= 0:
                # Can't compute % variance without a positive budget.
                continue
            variance = actual - budget
            var_pct = 100.0 * variance / budget
            if var_pct > 10:
                severity = 'over'
            elif var_pct < -10:
                severity = 'under'
            else:
                severity = 'on_track'
            breakdown.append({
                'category': cat,
                'actual': round(actual, 2),
                'budget': round(budget, 2),
                'variance': round(variance, 2),
                'variance_pct': round(var_pct, 1),
                'severity': severity,
            })
        if breakdown:
            breakdown.sort(key=lambda c: abs(c['variance']), reverse=True)
            out.append({
                'dimension': dim,
                'breakdown': breakdown[:max_dim_categories],
            })
    return out


# ════════════════════════════════════════════════════════════════════════════
# F5 — Drill-down: re-decompose a single category
# ════════════════════════════════════════════════════════════════════════════

def decompose_drilldown(headers, rows, dimension, category):
    """Filter rows where ``dimension == category`` and re-run the cost
    decomposition on that slice. Returns the same shape as analyze_costs
    so the frontend can swap the results in place.
    """
    if not dimension or not category or not rows:
        return _empty_result()
    filtered = [r for r in rows if str(r.get(dimension, '')) == str(category)]
    if not filtered:
        return _empty_result()
    return analyze_costs(headers, filtered)


def _sanitize_name(col_name):
    """Turn a column name into a safe snake_case key."""
    return re.sub(r'[^a-zA-Z0-9]+', '_', col_name).strip('_').lower()


def _empty_result():
    """Return the default empty cost analysis structure."""
    return {
        'total_cost': 0,
        'cost_columns': [],
        'primary_cost': None,
        'decomposition': {
            'by_dimension': [],
            'waterfall': {
                'start': {'label': 'Previous Period', 'value': 0},
                'changes': [],
                'end': {'label': 'Current Period', 'value': 0},
            },
        },
        'top_drivers': [],
        'efficiency': {
            'cost_per_record': None,
            'cost_trend': 'unknown',
            'metrics': [],
        },
    }


# -- Number Formatting (abbreviated) ----------------------------------------

def _fmt_pct(val):
    """Format a percentage as e.g. '82%'."""
    return f'{val:.0f}%'


def _fmt_abbrev(val):
    """Abbreviate large numbers: 1200000 -> '1.2M', 45000 -> '45K'."""
    abs_val = abs(val)
    sign = '-' if val < 0 else ''
    if abs_val >= 1_000_000_000:
        return f'{sign}{abs_val / 1_000_000_000:.1f}B'
    if abs_val >= 1_000_000:
        return f'{sign}{abs_val / 1_000_000:.1f}M'
    if abs_val >= 1_000:
        return f'{sign}{abs_val / 1_000:.1f}K'
    return f'{sign}{abs_val:.0f}'


def _fmt_abbrev_dollar(val):
    """Format a dollar amount with sign: +$150K, -$30K."""
    sign = '+' if val >= 0 else ''
    return f'{sign}${_fmt_abbrev(val)}'
