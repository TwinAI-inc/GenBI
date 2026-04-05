"""
GenBI Risk Analysis Engine

Pharma-domain risk analyzer: auto-detects risk-relevant columns,
computes composite risk scores from multiple signals, and optionally
generates LLM-driven narratives with recommendations.

Pure functions — no Flask dependencies.
"""

import json
import logging
import math
import re
from collections import defaultdict

from services.azure_ai_client import chat_completion_json

logger = logging.getLogger(__name__)

# ── Column detection patterns ────────────────────────────────────────────────

_RISK_DIRECT = re.compile(
    r'risk|score|safety|events?|adverse|compliance', re.I
)
_STATUS_COL = re.compile(r'status|state|disposition', re.I)
_PHASE_COL = re.compile(r'phase|stage', re.I)
_TIME_COL = re.compile(
    r'date|month|year|quarter|week|period|time|day', re.I
)
_ENTITY_COL = re.compile(
    r'molecule|product|entity|compound|drug|program|asset|trial|study', re.I
)
_ENROLLMENT_COL = re.compile(r'enroll', re.I)
_TARGET_COL = re.compile(r'target|planned|expected|goal', re.I)
_COST_ACTUAL_COL = re.compile(r'actual.*(cost|spend|budget)|cost.*actual|spend', re.I)
_COST_EXPECTED_COL = re.compile(
    r'(expected|planned|budget).*(cost|spend)|budget', re.I
)
_RISK_SCORE_COL = re.compile(r'risk.?score', re.I)
_SAFETY_EVENTS_COL = re.compile(r'safety.?event|adverse.?event|ae.?count', re.I)

_AT_RISK_VALUES = re.compile(
    r'at\s*risk|delayed|discontinued|failed|terminated|on\s*hold', re.I
)

# Risk-level thresholds
_THRESHOLDS = {'low': 30, 'medium': 55, 'high': 80}

# Phase-aware weight multipliers (later phases = higher stakes)
_PHASE_WEIGHTS = {
    'preclinical': 0.6, 'phase i': 0.8, 'phase 1': 0.8,
    'phase ii': 1.0, 'phase 2': 1.0,
    'phase iii': 1.3, 'phase 3': 1.3,
    'phase iv': 1.1, 'phase 4': 1.1,
    'filing': 1.4, 'approved': 0.5,
}


# ── Public API ───────────────────────────────────────────────────────────────

def analyze_risk(headers, rows, max_sample=200):
    """
    Main risk analysis entry point.

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
    dict  — structured risk analysis result (see module docstring).
    """
    if not headers or not rows:
        return _empty_result()

    sample = rows[:max_sample]
    col_map = _detect_columns(headers, sample)
    logger.info(f'Risk column map: {json.dumps({k: v for k, v in col_map.items() if v}, default=str)}')

    if not _has_risk_signals(col_map):
        logger.info('No risk-relevant columns detected — returning empty result')
        return _empty_result()

    entities = _build_entity_breakdown(col_map, sample)
    overall = _compute_overall_risk(entities)
    risk_matrix = _build_risk_matrix(entities)
    top_risks = _extract_top_risks(entities)

    return {
        'overall_risk': overall['score'],
        'risk_level': overall['level'],
        'entities': entities,
        'risk_matrix': risk_matrix,
        'top_risks': top_risks,
    }


def generate_risk_narrative(risk_data):
    """
    Call LLM to produce a plain-English narrative and recommendations.

    Parameters
    ----------
    risk_data : dict
        Output of analyze_risk().

    Returns
    -------
    dict  — {"narrative": "...", "recommendations": ["..."]}
    """
    if not risk_data or not risk_data.get('entities'):
        return {
            'narrative': 'Insufficient risk data for narrative generation.',
            'recommendations': [],
        }

    # Build a concise summary for the LLM (abbreviated numbers)
    entity_bullets = []
    for e in risk_data['entities'][:10]:
        drivers_str = ', '.join(
            f"{d['factor']} ({_fmt_score(d['score'])})" for d in e.get('drivers', [])[:3]
        )
        entity_bullets.append(
            f"- {e['name']}: risk {_fmt_score(e['risk_score'])} ({e['risk_level']})"
            f"  | drivers: {drivers_str}"
        )

    top_risk_bullets = []
    for tr in risk_data.get('top_risks', [])[:5]:
        top_risk_bullets.append(f"- [{tr['severity'].upper()}] {tr['entity']}: {tr['factor']}")

    prompt = f"""You are a pharmaceutical risk analyst. Analyze the following portfolio risk data and provide:
1. A concise narrative (3-5 sentences) summarizing the risk landscape.
2. A prioritized list of 3-5 actionable recommendations.

PORTFOLIO RISK SUMMARY:
- Overall risk: {_fmt_score(risk_data['overall_risk'])} ({risk_data['risk_level']})
- Entities analyzed: {len(risk_data['entities'])}

ENTITY BREAKDOWN:
{chr(10).join(entity_bullets)}

TOP RISKS:
{chr(10).join(top_risk_bullets) if top_risk_bullets else '- None critical'}

RISK MATRIX DIMENSIONS: {', '.join(risk_data.get('risk_matrix', {}).get('dimensions', []))}

Return JSON: {{"narrative": "...", "recommendations": ["...", "..."]}}"""

    try:
        parsed, _usage = chat_completion_json(
            prompt,
            system='You are a senior pharma risk analyst. Be specific, cite entity names and numbers. Keep recommendations actionable.',
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
        logger.error(f'Failed to parse risk narrative JSON: {e}')
        return _fallback_narrative(risk_data)
    except Exception as e:
        logger.error(f'Risk narrative generation failed: {e}')
        return _fallback_narrative(risk_data)


# ── Column Detection ─────────────────────────────────────────────────────────

def _detect_columns(headers, sample):
    """
    Scan headers and sample values to map logical roles to actual columns.
    Returns a dict of role -> column name (or None).
    """
    col_map = {
        'entity': None,
        'risk_score': None,
        'safety_events': None,
        'enrollment': None,
        'enrollment_target': None,
        'cost_actual': None,
        'cost_expected': None,
        'status': None,
        'phase': None,
        'time': None,
        'direct_risk': [],   # any column matching broad risk pattern
    }

    for h in headers:
        hl = h.strip()
        if _RISK_SCORE_COL.search(hl) and col_map['risk_score'] is None:
            col_map['risk_score'] = h
        elif _SAFETY_EVENTS_COL.search(hl) and col_map['safety_events'] is None:
            col_map['safety_events'] = h
        elif _ENROLLMENT_COL.search(hl):
            if _TARGET_COL.search(hl):
                col_map['enrollment_target'] = col_map['enrollment_target'] or h
            else:
                col_map['enrollment'] = col_map['enrollment'] or h
        elif _COST_ACTUAL_COL.search(hl) and col_map['cost_actual'] is None:
            col_map['cost_actual'] = h
        elif _COST_EXPECTED_COL.search(hl) and col_map['cost_expected'] is None:
            col_map['cost_expected'] = h
        elif _STATUS_COL.search(hl) and col_map['status'] is None:
            # Verify it actually has risk-related status values
            vals = {str(r.get(h, '')).strip() for r in sample if r.get(h)}
            if any(_AT_RISK_VALUES.search(v) for v in vals):
                col_map['status'] = h
        elif _PHASE_COL.search(hl) and col_map['phase'] is None:
            col_map['phase'] = h
        elif _TIME_COL.search(hl) and col_map['time'] is None:
            col_map['time'] = h
        elif _ENTITY_COL.search(hl) and col_map['entity'] is None:
            col_map['entity'] = h
        elif _RISK_DIRECT.search(hl):
            col_map['direct_risk'].append(h)

    # Fallback entity detection: first low-cardinality categorical column
    if col_map['entity'] is None:
        for h in headers:
            vals = [str(r.get(h, '')).strip() for r in sample if r.get(h)]
            unique = set(vals)
            if 2 <= len(unique) <= 50 and not _is_numeric_column(vals):
                col_map['entity'] = h
                break

    return col_map


# ── Entity Breakdown ─────────────────────────────────────────────────────────

def _build_entity_breakdown(col_map, rows):
    """
    Group rows by entity, compute per-entity risk scores and drivers.
    """
    entity_col = col_map['entity']
    if not entity_col:
        # Treat entire dataset as one entity
        return [_score_entity('Portfolio', rows, col_map)]

    groups = defaultdict(list)
    for r in rows:
        key = str(r.get(entity_col, 'Unknown')).strip()
        if key:
            groups[key].append(r)

    entities = []
    for name, entity_rows in sorted(groups.items()):
        entities.append(_score_entity(name, entity_rows, col_map))

    entities.sort(key=lambda e: e['risk_score'], reverse=True)
    return entities


def _score_entity(name, rows, col_map):
    """
    Compute composite risk score for a single entity from all available signals.
    """
    drivers = []
    weights_used = 0.0

    # 1. Direct risk score
    risk_score_col = col_map.get('risk_score')
    if risk_score_col:
        vals = _numeric_vals(rows, risk_score_col)
        if vals:
            avg_risk = sum(vals) / len(vals)
            # Normalise to 0-100 if values look like they are on a different scale
            if max(vals) <= 10:
                avg_risk = avg_risk * 10
            elif max(vals) <= 1:
                avg_risk = avg_risk * 100
            weight = 0.35
            drivers.append({
                'factor': 'Risk Score',
                'score': round(avg_risk, 1),
                'weight': weight,
                'detail': f'Avg {_fmt_score(avg_risk)} across {len(vals)} records',
            })
            weights_used += weight

    # 2. Safety event rate
    safety_col = col_map.get('safety_events')
    enrollment_col = col_map.get('enrollment')
    if safety_col:
        safety_vals = _numeric_vals(rows, safety_col)
        if safety_vals:
            total_events = sum(safety_vals)
            enroll_vals = _numeric_vals(rows, enrollment_col) if enrollment_col else []
            total_enrolled = sum(enroll_vals) if enroll_vals else len(rows)
            rate = (total_events / max(total_enrolled, 1)) * 100
            # Normalise: 0-5% = low, 5-15% = medium, 15%+ = high
            normalised = min(rate / 20.0 * 100, 100)
            weight = 0.25
            phase_mult = _get_phase_multiplier(rows, col_map)
            normalised = min(normalised * phase_mult, 100)
            detail = f'{total_events} events / {total_enrolled} enrolled ({_fmt_pct(rate)})'
            if phase_mult > 1.0:
                detail += f', phase-weighted x{phase_mult:.1f}'
            drivers.append({
                'factor': 'Safety Events',
                'score': round(normalised, 1),
                'weight': weight,
                'detail': detail,
            })
            weights_used += weight

    # 3. Enrollment gap
    if enrollment_col and col_map.get('enrollment_target'):
        enroll_vals = _numeric_vals(rows, enrollment_col)
        target_vals = _numeric_vals(rows, col_map['enrollment_target'])
        if enroll_vals and target_vals:
            total_enrolled = sum(enroll_vals)
            total_target = sum(target_vals)
            if total_target > 0:
                fill_pct = (total_enrolled / total_target) * 100
                # Gap score: 100% filled = 0 risk, 0% filled = 100 risk
                gap_score = max(0, min(100, 100 - fill_pct))
                weight = 0.20
                drivers.append({
                    'factor': 'Enrollment Gap',
                    'score': round(gap_score, 1),
                    'weight': weight,
                    'detail': f'{_fmt_pct(fill_pct)} of target ({_fmt_num(total_enrolled)}/{_fmt_num(total_target)})',
                })
                weights_used += weight

    # 4. Cost overrun
    cost_actual_col = col_map.get('cost_actual')
    cost_expected_col = col_map.get('cost_expected')
    if cost_actual_col and cost_expected_col:
        actual_vals = _numeric_vals(rows, cost_actual_col)
        expected_vals = _numeric_vals(rows, cost_expected_col)
        if actual_vals and expected_vals:
            total_actual = sum(actual_vals)
            total_expected = sum(expected_vals)
            if total_expected > 0:
                overrun_pct = ((total_actual - total_expected) / total_expected) * 100
                # Score: 0% overrun = 0 risk, 50%+ overrun = 100 risk
                overrun_score = max(0, min(100, overrun_pct * 2))
                weight = 0.15
                detail = f'${_fmt_abbrev(total_actual)} vs ${_fmt_abbrev(total_expected)}'
                if overrun_pct > 0:
                    detail += f' (+{_fmt_pct(overrun_pct)} overrun)'
                else:
                    detail += f' ({_fmt_pct(overrun_pct)} under budget)'
                drivers.append({
                    'factor': 'Cost Overrun',
                    'score': round(overrun_score, 1),
                    'weight': weight,
                    'detail': detail,
                })
                weights_used += weight

    # 5. Status distribution
    status_col = col_map.get('status')
    if status_col:
        statuses = [str(r.get(status_col, '')).strip() for r in rows if r.get(status_col)]
        if statuses:
            total = len(statuses)
            at_risk_count = sum(1 for s in statuses if _AT_RISK_VALUES.search(s))
            risk_pct = (at_risk_count / total) * 100
            # Score: 0% at risk = 0, 50%+ = 100
            status_score = min(100, risk_pct * 2)
            weight = 0.20
            drivers.append({
                'factor': 'Status Distribution',
                'score': round(status_score, 1),
                'weight': weight,
                'detail': f'{at_risk_count}/{total} ({_fmt_pct(risk_pct)}) at risk/delayed/discontinued',
            })
            weights_used += weight

    # 6. Direct risk columns (fallback signal)
    for rc in col_map.get('direct_risk', []):
        if rc == risk_score_col or rc == safety_col:
            continue  # Already counted
        vals = _numeric_vals(rows, rc)
        if vals:
            avg_val = sum(vals) / len(vals)
            if max(vals) <= 1:
                avg_val *= 100
            elif max(vals) <= 10:
                avg_val *= 10
            weight = 0.10
            drivers.append({
                'factor': rc,
                'score': round(min(avg_val, 100), 1),
                'weight': weight,
                'detail': f'Avg {_fmt_score(avg_val)}',
            })
            weights_used += weight

    # Compute composite score (weighted average)
    if drivers:
        if weights_used > 0:
            composite = sum(d['score'] * d['weight'] for d in drivers) / weights_used
        else:
            composite = sum(d['score'] for d in drivers) / len(drivers)
    else:
        composite = 0.0

    composite = round(max(0, min(100, composite)), 1)

    # Trend over time
    trend = _compute_trend(rows, col_map)

    # Determine current status from data
    status_col = col_map.get('status')
    current_status = 'Unknown'
    if status_col:
        statuses = [str(r.get(status_col, '')).strip() for r in rows if r.get(status_col)]
        if statuses:
            # Most recent or most common status
            current_status = max(set(statuses), key=statuses.count)

    return {
        'name': name,
        'risk_score': composite,
        'risk_level': _score_to_level(composite),
        'drivers': sorted(drivers, key=lambda d: d['score'] * d['weight'], reverse=True),
        'trend': trend,
        'status': current_status,
    }


# ── Composite Aggregation ────────────────────────────────────────────────────

def _compute_overall_risk(entities):
    """Weighted average of entity risk scores (higher-risk entities weighted more)."""
    if not entities:
        return {'score': 0.0, 'level': 'low'}

    # Weight each entity by its own risk (so high-risk entities pull the average up)
    total_weight = 0
    weighted_sum = 0
    for e in entities:
        w = 1 + (e['risk_score'] / 100)  # 1.0 to 2.0
        weighted_sum += e['risk_score'] * w
        total_weight += w

    score = round(weighted_sum / total_weight, 1) if total_weight else 0.0
    return {'score': score, 'level': _score_to_level(score)}


def _build_risk_matrix(entities):
    """
    Build a risk matrix with standard pharma dimensions.
    Each entity gets a rating per dimension based on its drivers.
    """
    dimensions = ['Safety', 'Enrollment', 'Cost', 'Efficacy', 'Timeline']
    dimension_map = {
        'Safety Events': 'Safety',
        'Risk Score': 'Efficacy',
        'Enrollment Gap': 'Enrollment',
        'Cost Overrun': 'Cost',
        'Status Distribution': 'Timeline',
    }

    entity_names = [e['name'] for e in entities[:15]]
    values = []

    for e in entities[:15]:
        driver_lookup = {}
        for d in e.get('drivers', []):
            dim = dimension_map.get(d['factor'])
            if dim:
                driver_lookup[dim] = d['score']

        row = []
        for dim in dimensions:
            score = driver_lookup.get(dim, 0)
            row.append(_score_to_level(score))
        values.append(row)

    return {
        'dimensions': dimensions,
        'entities': entity_names,
        'values': values,
    }


def _extract_top_risks(entities, max_risks=10):
    """
    Pull out the most alarming individual risk factors across all entities.
    """
    risks = []
    for e in entities:
        for d in e.get('drivers', []):
            if d['score'] >= _THRESHOLDS['medium']:
                severity = _score_to_level(d['score'])
                risks.append({
                    'entity': e['name'],
                    'factor': f"{d['factor']}: {d['detail']}",
                    'severity': severity,
                    '_sort_score': d['score'],
                })

    risks.sort(key=lambda r: r['_sort_score'], reverse=True)
    # Strip internal sort key
    for r in risks[:max_risks]:
        del r['_sort_score']
    return risks[:max_risks]


# ── Trend Computation ────────────────────────────────────────────────────────

def _compute_trend(rows, col_map):
    """
    If a time column exists, compute risk score per time period.
    Returns a list of floats (risk scores over time) or empty list.
    """
    time_col = col_map.get('time')
    risk_score_col = col_map.get('risk_score')
    safety_col = col_map.get('safety_events')

    if not time_col:
        return []

    # Pick the best signal column for trending
    signal_col = risk_score_col or safety_col
    if not signal_col:
        return []

    # Group by time period
    time_groups = defaultdict(list)
    for r in rows:
        t = str(r.get(time_col, '')).strip()
        val = _safe_float(r.get(signal_col))
        if t and val is not None:
            time_groups[t].append(val)

    if len(time_groups) < 2:
        return []

    # Sort time periods lexicographically (works for ISO dates, quarters, etc.)
    sorted_periods = sorted(time_groups.keys())
    trend = []
    for period in sorted_periods:
        vals = time_groups[period]
        avg = sum(vals) / len(vals)
        # Normalise like the main scoring
        if signal_col == risk_score_col:
            if max(vals) <= 10:
                avg *= 10
            elif max(vals) <= 1:
                avg *= 100
        else:
            # Safety events: use rate as proxy
            avg = min(avg * 10, 100)
        trend.append(round(avg, 1))

    return trend


# ── Fallback Narrative ───────────────────────────────────────────────────────

def _fallback_narrative(risk_data):
    """
    Generate a rule-based narrative when LLM is unavailable.
    """
    entities = risk_data.get('entities', [])
    overall = risk_data.get('overall_risk', 0)
    level = risk_data.get('risk_level', 'unknown')

    high_risk = [e for e in entities if e['risk_level'] in ('high', 'critical')]
    recs = []

    narrative = (
        f'Portfolio overall risk is {_fmt_score(overall)} ({level}). '
        f'{len(entities)} entities analyzed'
    )
    if high_risk:
        names = ', '.join(e['name'] for e in high_risk[:3])
        narrative += f', with {len(high_risk)} high-risk: {names}.'
        recs.append(f'Prioritize review of {high_risk[0]["name"]} (risk {_fmt_score(high_risk[0]["risk_score"])}).')
    else:
        narrative += '. No entities in high-risk territory.'

    top_risks = risk_data.get('top_risks', [])
    if top_risks:
        recs.append(f'Address top risk factor: {top_risks[0]["entity"]} — {top_risks[0]["factor"]}.')

    if not recs:
        recs.append('Continue monitoring; no immediate action required.')

    return {'narrative': narrative, 'recommendations': recs}


# ── Helper Functions ─────────────────────────────────────────────────────────

def _has_risk_signals(col_map):
    """Return True if we found at least one usable risk signal."""
    return any([
        col_map.get('risk_score'),
        col_map.get('safety_events'),
        col_map.get('status'),
        col_map.get('enrollment') and col_map.get('enrollment_target'),
        col_map.get('cost_actual') and col_map.get('cost_expected'),
        col_map.get('direct_risk'),
    ])


def _score_to_level(score):
    """Map 0-100 score to risk level string."""
    if score >= _THRESHOLDS['high']:
        return 'critical' if score >= 90 else 'high'
    if score >= _THRESHOLDS['medium']:
        return 'medium'
    return 'low'


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


def _safe_float(v):
    """Try to parse a value as float, return None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        # Handle currency/comma strings like "$1,234.56"
        if isinstance(v, str):
            cleaned = re.sub(r'[$,% ]', '', v)
            try:
                return float(cleaned)
            except (ValueError, TypeError):
                return None
        return None


def _is_numeric_column(vals):
    """Return True if >80% of non-empty values parse as float."""
    if not vals:
        return False
    numeric_count = sum(1 for v in vals if _safe_float(v) is not None)
    return numeric_count > len(vals) * 0.8


def _get_phase_multiplier(rows, col_map):
    """
    Determine phase-aware weight multiplier for the entity.
    Later phases carry higher risk weight.
    """
    phase_col = col_map.get('phase')
    if not phase_col:
        return 1.0

    phases = [str(r.get(phase_col, '')).strip().lower() for r in rows if r.get(phase_col)]
    if not phases:
        return 1.0

    # Use most common phase for this entity
    most_common = max(set(phases), key=phases.count)
    return _PHASE_WEIGHTS.get(most_common, 1.0)


def _empty_result():
    """Return the default empty risk analysis structure."""
    return {
        'overall_risk': 0.0,
        'risk_level': 'low',
        'entities': [],
        'risk_matrix': {
            'dimensions': ['Safety', 'Enrollment', 'Cost', 'Efficacy', 'Timeline'],
            'entities': [],
            'values': [],
        },
        'top_risks': [],
    }


# ── Number Formatting (abbreviated) ─────────────────────────────────────────

def _fmt_score(val):
    """Format a score as e.g. '72.3'."""
    return f'{val:.1f}'


def _fmt_pct(val):
    """Format a percentage as e.g. '82%'."""
    return f'{val:.0f}%'


def _fmt_num(val):
    """Format an integer with commas: 1234 -> '1,234'."""
    return f'{int(val):,}'


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
