"""
GenBI Comparative Safety Assessment (CSA) Engine

Pharma-domain safety analyzer: auto-detects safety and adverse-event
columns, computes entity-level safety rates and Proportional Reporting
Ratios (PRR), performs phase-normalized comparisons, and optionally
generates LLM-driven narratives with recommendations.

Pure functions -- no Flask dependencies.
"""

import json
import logging
import re
from collections import defaultdict

from services.azure_ai_client import chat_completion_json

logger = logging.getLogger(__name__)

# -- Column detection patterns -------------------------------------------------

_SAFETY_COL = re.compile(
    r'safety.?event|adverse.?event|ae.?count|sae.?count|'
    r'adverse.?reaction|safety.?signal|serious.?ae|teae', re.I
)
_ENTITY_COL = re.compile(
    r'molecule|product|drug|compound|entity|program|asset|treatment|arm', re.I
)
_PHASE_COL = re.compile(r'phase|stage', re.I)
_ENROLLMENT_COL = re.compile(r'enroll|subjects?|patients?|participants?|n_', re.I)
_TIME_COL = re.compile(
    r'date|month|year|quarter|week|period|time|day', re.I
)

# PRR signal threshold
_PRR_SIGNAL_THRESHOLD = 2.0

# Severity mapping for PRR values
_PRR_SEVERITY = {'low': 1.5, 'medium': 2.0, 'high': 3.0}


# -- Public API ----------------------------------------------------------------

def analyze_safety(headers, rows, max_sample=500):
    """
    Main safety analysis entry point.

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
    dict -- structured safety analysis result.
    """
    if not headers or not rows:
        return _empty_result()

    sample = rows[:max_sample]

    safety_cols = _detect_safety_columns(headers, sample)
    if not safety_cols:
        logger.info('No safety columns detected -- returning empty result')
        return _empty_result()

    entity_col = _detect_entity_column(headers, sample)
    phase_col = _detect_phase_column(headers)
    enrollment_col = _detect_enrollment_column(headers, sample)

    logger.info(
        f'Safety columns: {safety_cols}, entity: {entity_col}, '
        f'phase: {phase_col}, enrollment: {enrollment_col}'
    )

    comparisons = _build_comparisons(
        sample, safety_cols, entity_col, phase_col, enrollment_col
    )
    signals = _detect_signals(comparisons)
    phase_breakdown = _build_phase_breakdown(
        sample, safety_cols[0], entity_col, phase_col, enrollment_col
    )

    return {
        'safety_columns': safety_cols,
        'entity_column': entity_col,
        'comparisons': comparisons,
        'signals': signals,
        'phase_breakdown': phase_breakdown,
    }


def generate_safety_narrative(safety_data):
    """
    Call LLM to produce a plain-English narrative and recommendations.

    Parameters
    ----------
    safety_data : dict
        Output of analyze_safety().

    Returns
    -------
    dict -- {"narrative": "...", "recommendations": [...]}
    """
    if not safety_data or not safety_data.get('comparisons'):
        return {
            'narrative': 'Insufficient safety data for narrative generation.',
            'recommendations': [],
        }

    # Build a concise summary for the LLM (abbreviated numbers)
    entity_bullets = []
    for c in safety_data['comparisons'][:10]:
        flag = ' [SIGNAL]' if c.get('signal') else ''
        entity_bullets.append(
            f"- {c['entity']}: {_fmt_num(c['total_events'])} events, "
            f"rate {_fmt_rate(c['rate'])}, PRR {c['prr']:.1f}, "
            f"phase-norm {c['phase_normalized']:.1f}{flag}"
        )

    signal_bullets = []
    for s in safety_data.get('signals', [])[:5]:
        signal_bullets.append(
            f"- [{s['severity'].upper()}] {s['entity']}: "
            f"{s['metric']} PRR {s['prr']:.1f} -- {s['detail']}"
        )

    phase_summary = []
    for phase, info in safety_data.get('phase_breakdown', {}).items():
        entity_count = len(info.get('entities', []))
        phase_summary.append(
            f"- {phase}: avg rate {_fmt_rate(info['avg_rate'])}, "
            f"{entity_count} entities"
        )

    prompt = f"""You are a pharmaceutical safety analyst. Analyze the following comparative safety data and provide:
1. A concise narrative (3-5 sentences) summarizing the safety landscape, highlighting concerning entities.
2. A prioritized list of 3-5 actionable recommendations.

Use abbreviated numbers (1.2K, 3.4M, etc.).

SAFETY SUMMARY:
- Safety columns: {', '.join(safety_data.get('safety_columns', []))}
- Entity column: {safety_data.get('entity_column', 'N/A')}
- Entities analyzed: {len(safety_data['comparisons'])}

ENTITY COMPARISONS:
{chr(10).join(entity_bullets)}

SIGNALS (PRR > {_PRR_SIGNAL_THRESHOLD}):
{chr(10).join(signal_bullets) if signal_bullets else '- No disproportionate signals detected'}

PHASE BREAKDOWN:
{chr(10).join(phase_summary) if phase_summary else '- No phase data available'}

Return JSON: {{"narrative": "...", "recommendations": ["...", "..."]}}"""

    try:
        parsed, _usage = chat_completion_json(
            prompt,
            system='You are a senior pharma safety analyst. Be specific, cite entity names and numbers. Keep recommendations actionable and tied to the data.',
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
        logger.error(f'Failed to parse safety narrative JSON: {e}')
        return _fallback_narrative(safety_data)
    except Exception as e:
        logger.error(f'Safety narrative generation failed: {e}')
        return _fallback_narrative(safety_data)


# -- Column Detection ----------------------------------------------------------

def _detect_safety_columns(headers, sample):
    """
    Find all columns whose name matches a safety/AE pattern AND whose
    values are predominantly numeric. Return list sorted by total descending.
    """
    candidates = []
    for h in headers:
        if _SAFETY_COL.search(h):
            vals = _numeric_vals(sample, h)
            if len(vals) >= max(1, len(sample) * 0.3):
                total = sum(vals)
                candidates.append((h, total))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates]


def _detect_entity_column(headers, sample):
    """
    Find the entity column (Molecule, Product, Drug, etc.).
    Falls back to the first low-cardinality categorical column.
    """
    for h in headers:
        if _ENTITY_COL.search(h):
            return h

    # Fallback: first low-cardinality categorical column
    for h in headers:
        if _SAFETY_COL.search(h) or _PHASE_COL.search(h) or _TIME_COL.search(h):
            continue
        vals = [str(r.get(h, '')).strip() for r in sample if r.get(h)]
        unique = set(vals)
        if 2 <= len(unique) <= 50 and not _is_numeric_column(vals):
            return h

    return None


def _detect_phase_column(headers):
    """Return the first header that matches a phase pattern, or None."""
    for h in headers:
        if _PHASE_COL.search(h):
            return h
    return None


def _detect_enrollment_column(headers, sample):
    """
    Find a numeric column that looks like enrollment/patient counts.
    """
    for h in headers:
        if _ENROLLMENT_COL.search(h):
            vals = _numeric_vals(sample, h)
            if len(vals) >= max(1, len(sample) * 0.3):
                return h
    return None


# -- Comparisons ---------------------------------------------------------------

def _build_comparisons(rows, safety_cols, entity_col, phase_col, enrollment_col):
    """
    Build entity-level safety comparisons using the primary safety column.
    Computes total events, rate, PRR, and phase-normalized rate per entity.
    """
    primary_safety = safety_cols[0]

    # Compute overall rate across all rows
    all_events = _numeric_vals(rows, primary_safety)
    total_events_all = sum(all_events)
    if enrollment_col:
        all_enrollment = _numeric_vals(rows, enrollment_col)
        total_denom_all = sum(all_enrollment) if all_enrollment else len(rows)
    else:
        total_denom_all = len(rows)
    overall_rate = (total_events_all / max(total_denom_all, 1))

    # Compute phase-level averages for phase-normalized comparisons
    phase_avg = {}
    if phase_col:
        phase_avg = _compute_phase_averages(
            rows, primary_safety, phase_col, enrollment_col
        )

    # Group rows by entity
    if entity_col:
        groups = defaultdict(list)
        for r in rows:
            key = str(r.get(entity_col, 'Unknown')).strip()
            if key:
                groups[key].append(r)
    else:
        groups = {'All': list(rows)}

    comparisons = []
    for name, entity_rows in sorted(groups.items()):
        event_vals = _numeric_vals(entity_rows, primary_safety)
        total_events = sum(event_vals)

        if enrollment_col:
            enroll_vals = _numeric_vals(entity_rows, enrollment_col)
            denom = sum(enroll_vals) if enroll_vals else len(entity_rows)
        else:
            denom = len(entity_rows)

        rate = total_events / max(denom, 1)

        # PRR: entity rate / overall rate
        prr = round(rate / overall_rate, 2) if overall_rate > 0 else 0.0

        # Phase-normalized rate
        phase_norm = _compute_phase_normalized(
            entity_rows, primary_safety, phase_col, enrollment_col, phase_avg
        )

        signal = prr >= _PRR_SIGNAL_THRESHOLD

        comparisons.append({
            'entity': name,
            'total_events': int(total_events),
            'rate': round(rate, 2),
            'prr': prr,
            'phase_normalized': phase_norm,
            'signal': signal,
        })

    # Sort by PRR descending (most concerning first)
    comparisons.sort(key=lambda c: c['prr'], reverse=True)
    return comparisons


def _compute_phase_averages(rows, safety_col, phase_col, enrollment_col):
    """
    Compute average safety rate per phase across all entities.
    Returns dict of phase -> average rate.
    """
    phase_groups = defaultdict(list)
    for r in rows:
        phase = str(r.get(phase_col, '')).strip()
        if phase:
            phase_groups[phase].append(r)

    phase_avg = {}
    for phase, phase_rows in phase_groups.items():
        events = sum(_numeric_vals(phase_rows, safety_col))
        if enrollment_col:
            enroll = sum(_numeric_vals(phase_rows, enrollment_col))
            denom = enroll if enroll > 0 else len(phase_rows)
        else:
            denom = len(phase_rows)
        phase_avg[phase] = events / max(denom, 1)

    return phase_avg


def _compute_phase_normalized(rows, safety_col, phase_col, enrollment_col, phase_avg):
    """
    Compute the phase-normalized rate for an entity: entity's rate in each
    phase divided by the phase average, then averaged across phases.
    Returns a ratio (1.0 = at average).
    """
    if not phase_col or not phase_avg:
        return 1.0

    phase_groups = defaultdict(list)
    for r in rows:
        phase = str(r.get(phase_col, '')).strip()
        if phase:
            phase_groups[phase].append(r)

    if not phase_groups:
        return 1.0

    ratios = []
    for phase, phase_rows in phase_groups.items():
        avg = phase_avg.get(phase)
        if avg is None or avg == 0:
            continue

        events = sum(_numeric_vals(phase_rows, safety_col))
        if enrollment_col:
            enroll = sum(_numeric_vals(phase_rows, enrollment_col))
            denom = enroll if enroll > 0 else len(phase_rows)
        else:
            denom = len(phase_rows)

        entity_rate = events / max(denom, 1)
        ratios.append(entity_rate / avg)

    if not ratios:
        return 1.0

    return round(sum(ratios) / len(ratios), 2)


# -- Signal Detection ----------------------------------------------------------

def _detect_signals(comparisons):
    """
    Flag entities with PRR >= threshold as potential safety signals.
    Returns a list of signal dicts sorted by PRR descending.
    """
    signals = []
    for c in comparisons:
        if not c.get('signal'):
            continue

        prr = c['prr']
        severity = _prr_severity(prr)

        signals.append({
            'entity': c['entity'],
            'metric': 'Safety Events',
            'prr': prr,
            'detail': f'{prr:.1f}x above average rate',
            'severity': severity,
        })

    signals.sort(key=lambda s: s['prr'], reverse=True)
    return signals


def _prr_severity(prr):
    """Map a PRR value to a severity label."""
    if prr >= _PRR_SEVERITY['high']:
        return 'high'
    if prr >= _PRR_SEVERITY['medium']:
        return 'medium'
    return 'low'


# -- Phase Breakdown -----------------------------------------------------------

def _build_phase_breakdown(rows, safety_col, entity_col, phase_col, enrollment_col):
    """
    Build a breakdown of safety rates by phase, with per-entity detail
    within each phase.
    """
    if not phase_col:
        return {}

    # Group rows by phase, then by entity within phase
    phase_entity = defaultdict(lambda: defaultdict(list))
    for r in rows:
        phase = str(r.get(phase_col, '')).strip()
        entity = str(r.get(entity_col, 'All')).strip() if entity_col else 'All'
        if phase:
            phase_entity[phase][entity].append(r)

    breakdown = {}
    for phase, entities in sorted(phase_entity.items()):
        # Compute phase-wide average rate
        all_phase_rows = []
        for entity_rows in entities.values():
            all_phase_rows.extend(entity_rows)

        total_events = sum(_numeric_vals(all_phase_rows, safety_col))
        if enrollment_col:
            total_enroll = sum(_numeric_vals(all_phase_rows, enrollment_col))
            denom = total_enroll if total_enroll > 0 else len(all_phase_rows)
        else:
            denom = len(all_phase_rows)

        avg_rate = round(total_events / max(denom, 1), 2)

        # Per-entity detail within this phase
        entity_details = []
        for entity_name, entity_rows in sorted(entities.items()):
            e_events = sum(_numeric_vals(entity_rows, safety_col))
            if enrollment_col:
                e_enroll = sum(_numeric_vals(entity_rows, enrollment_col))
                e_denom = e_enroll if e_enroll > 0 else len(entity_rows)
            else:
                e_denom = len(entity_rows)
            e_rate = round(e_events / max(e_denom, 1), 2)
            entity_details.append({'name': entity_name, 'rate': e_rate})

        entity_details.sort(key=lambda e: e['rate'], reverse=True)

        breakdown[phase] = {
            'avg_rate': avg_rate,
            'entities': entity_details,
        }

    return breakdown


# -- Fallback Narrative --------------------------------------------------------

def _fallback_narrative(safety_data):
    """
    Generate a rule-based narrative when LLM is unavailable.
    """
    comparisons = safety_data.get('comparisons', [])
    signals = safety_data.get('signals', [])

    narrative = f'{len(comparisons)} entities analyzed for comparative safety'

    signal_entities = [c for c in comparisons if c.get('signal')]
    if signal_entities:
        names = ', '.join(e['entity'] for e in signal_entities[:3])
        narrative += (
            f'; {len(signal_entities)} flagged with disproportionate '
            f'reporting: {names}.'
        )
    else:
        narrative += '. No disproportionate safety signals detected.'

    recs = []
    if signals:
        top = signals[0]
        recs.append(
            f'Investigate {top["entity"]} (PRR {top["prr"]:.1f}) -- '
            f'{top["detail"]}.'
        )

    for s in signals[1:3]:
        recs.append(
            f'Review {s["entity"]} safety profile (PRR {s["prr"]:.1f}, '
            f'severity: {s["severity"]}).'
        )

    if not recs:
        recs.append(
            'Continue routine safety monitoring; no disproportionate '
            'signals detected.'
        )

    return {'narrative': narrative, 'recommendations': recs}


# -- Helper Functions ----------------------------------------------------------

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


def _is_numeric_column(vals):
    """Return True if >80% of non-empty values parse as float."""
    if not vals:
        return False
    numeric_count = sum(1 for v in vals if _safe_float(v) is not None)
    return numeric_count > len(vals) * 0.8


def _empty_result():
    """Return the default empty safety analysis structure."""
    return {
        'safety_columns': [],
        'entity_column': None,
        'comparisons': [],
        'signals': [],
        'phase_breakdown': {},
    }


# -- Number Formatting (abbreviated) ------------------------------------------

def _fmt_num(val):
    """Format an integer with commas: 1234 -> '1,234'."""
    return f'{int(val):,}'


def _fmt_rate(val):
    """Format a rate as e.g. '3.75'."""
    return f'{val:.2f}'


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
