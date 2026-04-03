"""
GenBI Chart Intelligence Engine

LLM-driven chart plan generation with data profiling, guardrails,
and domain detection. Replaces rule-based autoMapExcelData logic.
"""

import json
import logging
import math
from collections import Counter

from services.azure_ai_client import chat_completion_json

logger = logging.getLogger(__name__)

# ── Chart family definitions ──────────────────────────────────────────────────

CHART_FAMILIES = {
    'comparison': ['bar', 'hbar'],
    'trend': ['line', 'multiline', 'area', 'stacked'],
    'composition': ['donut', 'treemap', 'funnel'],
    'relationship': ['scatter'],
    'distribution': ['gauge', 'boxplot', 'radar'],
    'geographic': ['usmap', 'worldmap'],
}

# ── Data Profiler ─────────────────────────────────────────────────────────────

def profile_data(headers, rows, max_sample=50):
    """
    Profile dataset columns: types, distributions, correlations, cardinality.
    Returns a structured summary for the LLM.
    """
    if not rows:
        return {}

    n_rows = len(rows)
    sample = rows[:max_sample]
    profile = {
        'row_count': n_rows,
        'column_count': len(headers),
        'columns': {}
    }

    for h in headers:
        vals = [r.get(h) for r in rows if r.get(h) is not None and str(r.get(h)).strip() != '']
        str_vals = [str(v).strip() for v in vals]
        unique = list(set(str_vals))

        # Type detection
        numeric_count = sum(1 for v in vals if _is_numeric(v))
        is_numeric = numeric_count > len(vals) * 0.8 if vals else False

        # Date detection
        is_date = bool(_detect_date_pattern(h, str_vals[:20]))

        # If date, override numeric
        if is_date:
            is_numeric = False

        # Geographic detection
        is_geo = _detect_geographic(h, unique[:20])

        # Boolean detection
        is_boolean = set(s.lower() for s in unique) <= {'true', 'false', 'yes', 'no', '1', '0', 't', 'f', 'y', 'n'}

        # ID/Index detection
        is_id = bool(_is_id_column(h, is_numeric, unique, len(vals)))

        # Numeric stats
        num_stats = None
        if is_numeric and not is_id:
            num_vals = [float(v) for v in vals if _is_numeric(v)]
            if num_vals:
                num_stats = {
                    'min': round(min(num_vals), 2),
                    'max': round(max(num_vals), 2),
                    'mean': round(sum(num_vals) / len(num_vals), 2),
                    'std': round(_std(num_vals), 2),
                    'null_pct': round((n_rows - len(vals)) / n_rows * 100, 1)
                }

        col_profile = {
            'type': 'date' if is_date else 'geographic' if is_geo else 'boolean' if is_boolean else 'numeric' if (is_numeric and not is_id) else 'id' if is_id else 'categorical',
            'cardinality': len(unique),
            'null_pct': round((n_rows - len(vals)) / n_rows * 100, 1),
            'sample_values': unique[:8],
        }
        if num_stats:
            col_profile['stats'] = num_stats
        if is_date:
            col_profile['date_granularity'] = _detect_date_granularity(str_vals[:20])
        if is_geo:
            col_profile['geo_type'] = is_geo  # 'us_state' or 'country'

        profile['columns'][h] = col_profile

    # Correlation matrix (top pairs)
    numeric_cols = [h for h in headers if profile['columns'][h]['type'] == 'numeric']
    if len(numeric_cols) >= 2:
        correlations = []
        for i, c1 in enumerate(numeric_cols[:8]):
            for c2 in numeric_cols[i+1:8]:
                corr = _pearson(rows, c1, c2)
                if abs(corr) > 0.3:
                    correlations.append({'col1': c1, 'col2': c2, 'correlation': round(corr, 3)})
        correlations.sort(key=lambda x: abs(x['correlation']), reverse=True)
        profile['correlations'] = correlations[:10]

    return profile


def generate_chart_plan(profile, max_charts=8):
    """
    Send data profile to LLM and get a smart chart plan back.
    Returns list of chart specs.
    """
    if not profile or not profile.get('columns'):
        return []

    # Build concise profile summary for LLM
    col_summary = {}
    for name, info in profile['columns'].items():
        entry = {'type': info['type'], 'cardinality': info['cardinality']}
        if info.get('stats'):
            entry['range'] = f"{info['stats']['min']} - {info['stats']['max']}"
            entry['mean'] = info['stats']['mean']
        if info.get('sample_values'):
            entry['samples'] = info['sample_values'][:5]
        if info.get('geo_type'):
            entry['geo'] = info['geo_type']
        if info.get('date_granularity'):
            entry['granularity'] = info['date_granularity']
        col_summary[name] = entry

    prompt = f"""You are a BI chart selection expert. Analyze this dataset and create an optimal chart plan.

DATASET PROFILE:
- Rows: {profile['row_count']}
- Columns: {profile['column_count']}
- Column details: {json.dumps(col_summary, default=str)}
- Correlations: {json.dumps(profile.get('correlations', []), default=str)}

CHART SELECTION RULES:
1. ONE chart per family maximum:
   - comparison (bar/hbar)
   - trend (line/multiline/area/stacked)
   - composition (donut/treemap/funnel)
   - relationship (scatter) — ONLY if correlation >0.3 exists
   - distribution (gauge/boxplot/radar)
   - geographic (usmap/worldmap) — ALWAYS if geographic column detected
2. Donut: max 6 categories. If 7-15, use treemap. If 16+, use hbar.
3. Bar: max 8 bars, aggregate rest as "Other"
4. Equal distribution (all values within 5% of each other): use bar with note, NOT donut
5. Multi-line: max 4 series. If more, pick top 4 by total.
6. Scatter: ONLY if 2 numeric columns have correlation >0.3
7. Maps: ALWAYS include if State/Country column detected
8. Rank metrics by business importance (revenue > profit > count > rate)
9. Auto-derive features: extract month/year from dates if useful
10. Generate INSIGHT titles, not template titles. E.g., "Revenue Peaks in Q4, Led by Enterprise" not "Revenue by Quarter"

AGGREGATION RULES:
- Revenue/Sales/Cost/Profit/Price → SUM
- Rate/Score/Percent/Satisfaction → AVERAGE
- Count/Number → SUM
- Let column name semantics guide the choice

DOMAIN DETECTION:
- Pharma: look for Phase, Molecule, Trial, Efficacy, Risk → use funnel for phases, gauge for rates
- Sales: look for Revenue, Channel, Region, Rep → use map for geography, trend for time
- Supply Chain: look for Facility, Defect, Lead Time → use gauge for rates, hbar for comparison

Respond with ONLY valid JSON array of chart objects:
[
  {{
    "type": "multiline|bar|hbar|donut|treemap|scatter|gauge|boxplot|radar|usmap|worldmap|funnel|stacked|area",
    "title": "Insight-driven title (not template)",
    "xCol": "column_name or null",
    "yCol": "column_name or null",
    "groupCol": "column_name for series/groups or null",
    "aggFn": "sum|avg|count",
    "maxItems": 8,
    "color": "cyan|teal|emerald|rose",
    "desc": "One sentence insight about what this chart reveals",
    "guardrail_notes": "Any data preprocessing needed (binning, top-N, etc.)",
    "derived_column": "If a new column should be extracted (e.g., 'Month from Quarter'), describe it here, else null",
    "family": "comparison|trend|composition|relationship|distribution|geographic"
  }}
]

Return between 4 and {max_charts} charts. Only include charts that provide genuine insight."""

    try:
        result = chat_completion_json(
            [{'role': 'user', 'content': prompt}],
            temperature=0.3,
            max_tokens=3000
        )
        content = result.get('content', '')

        # Parse JSON from response
        chart_plan = json.loads(content)
        if not isinstance(chart_plan, list):
            logger.warning('Chart plan is not a list, wrapping')
            chart_plan = [chart_plan]

        # Validate and enforce guardrails
        chart_plan = _validate_guardrails(chart_plan, profile)

        return chart_plan

    except json.JSONDecodeError as e:
        logger.error(f'Failed to parse chart plan JSON: {e}')
        return []
    except Exception as e:
        logger.error(f'Chart plan generation failed: {e}')
        return []


# ── Guardrail Validator ───────────────────────────────────────────────────────

def _validate_guardrails(plan, profile):
    """Validate and fix chart plan against guardrails."""
    validated = []
    used_families = set()

    for chart in plan:
        chart_type = chart.get('type', 'bar')
        family = chart.get('family', _get_family(chart_type))

        # One per family rule
        if family in used_families:
            continue

        # Donut cardinality check
        if chart_type == 'donut':
            group_col = chart.get('groupCol') or chart.get('xCol')
            if group_col and group_col in profile['columns']:
                card = profile['columns'][group_col]['cardinality']
                if card > 15:
                    chart['type'] = 'hbar'
                    chart['guardrail_notes'] = f'Switched from donut to hbar ({card} categories)'
                elif card > 6:
                    chart['type'] = 'treemap'
                    chart['guardrail_notes'] = f'Switched from donut to treemap ({card} categories)'

        # Bar max items
        if chart_type in ('bar', 'hbar'):
            chart['maxItems'] = min(chart.get('maxItems', 8), 8)

        # Scatter correlation check
        if chart_type == 'scatter':
            correlations = profile.get('correlations', [])
            x = chart.get('xCol')
            y = chart.get('yCol')
            has_corr = any(
                (c['col1'] == x and c['col2'] == y) or (c['col1'] == y and c['col2'] == x)
                for c in correlations
            )
            if not has_corr and correlations:
                # Use the strongest correlation instead
                best = correlations[0]
                chart['xCol'] = best['col1']
                chart['yCol'] = best['col2']
                chart['guardrail_notes'] = f'Reassigned to strongest correlation: {best["correlation"]}'
            elif not has_corr:
                continue  # Skip scatter if no correlation

        # Multi-line series limit
        if chart_type in ('multiline', 'stacked'):
            group_col = chart.get('groupCol')
            if group_col and group_col in profile['columns']:
                card = profile['columns'][group_col]['cardinality']
                if card > 4:
                    chart['maxItems'] = 4
                    chart['guardrail_notes'] = f'Limited to top 4 series (from {card})'

        validated.append(chart)
        used_families.add(family)

    return validated


# ── Helper Functions ──────────────────────────────────────────────────────────

def _get_family(chart_type):
    for family, types in CHART_FAMILIES.items():
        if chart_type in types:
            return family
    return 'comparison'


def _is_numeric(v):
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _std(vals):
    if len(vals) < 2:
        return 0
    mean = sum(vals) / len(vals)
    return math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))


def _pearson(rows, col1, col2):
    pairs = []
    for r in rows:
        try:
            v1, v2 = float(r.get(col1, '')), float(r.get(col2, ''))
            pairs.append((v1, v2))
        except (ValueError, TypeError):
            continue
    if len(pairs) < 10:
        return 0
    n = len(pairs)
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    sx2 = sum(p[0] ** 2 for p in pairs)
    sy2 = sum(p[1] ** 2 for p in pairs)
    denom = math.sqrt((n * sx2 - sx ** 2) * (n * sy2 - sy ** 2))
    if denom == 0:
        return 0
    return (n * sxy - sx * sy) / denom


def _detect_date_pattern(col_name, sample_vals):
    import re
    if re.search(r'date|month|year|week|day|time|period|quarter', col_name, re.I):
        return True
    date_patterns = [
        r'\d{4}-\d{2}(-\d{2})?',  # 2024-01 or 2024-01-15
        r'\d{4}-Q[1-4]',  # 2024-Q1
        r'\d{1,2}/\d{1,2}/\d{2,4}',  # MM/DD/YYYY
    ]
    matches = 0
    for v in sample_vals[:10]:
        for pat in date_patterns:
            if re.match(pat, str(v)):
                matches += 1
                break
    return matches > len(sample_vals[:10]) * 0.5


def _detect_date_granularity(sample_vals):
    import re
    for v in sample_vals[:5]:
        s = str(v)
        if re.match(r'\d{4}-Q[1-4]', s):
            return 'quarterly'
        if re.match(r'\d{4}-\d{2}-\d{2}', s):
            return 'daily'
        if re.match(r'\d{4}-\d{2}$', s):
            return 'monthly'
        if re.match(r'\d{4}$', s):
            return 'yearly'
    return 'unknown'


def _detect_geographic(col_name, unique_vals):
    import re
    if re.search(r'\bstate\b', col_name, re.I):
        return 'us_state'
    if re.search(r'\bcountry\b|\bnation\b|\blocation\b', col_name, re.I):
        return 'country'

    us_states = {'california', 'texas', 'new york', 'florida', 'illinois', 'ohio',
                 'pennsylvania', 'georgia', 'michigan', 'north carolina'}
    countries = {'united states', 'china', 'japan', 'germany', 'france', 'india',
                 'united kingdom', 'brazil', 'canada', 'australia'}

    lower_vals = {str(v).lower() for v in unique_vals}
    if len(lower_vals & us_states) >= 3:
        return 'us_state'
    if len(lower_vals & countries) >= 2:
        return 'country'

    return None


def _is_id_column(col_name, is_numeric, unique_vals, total_vals):
    import re
    # Explicit ID column names
    if re.search(r'\b(id|index|#|row|sr|sno|serial|code|sku)\b', col_name, re.I):
        return True
    # High cardinality numeric BUT not if it looks like a metric
    metric_patterns = r'revenue|sales|cost|price|profit|income|amount|total|score|rate|percent|pct|count|deals|customers|churn|nps|satisfaction|risk|efficacy|enrollment|salary|budget|expense|margin|growth|roi|yield'
    if re.search(metric_patterns, col_name, re.I):
        return False  # Never treat metrics as IDs
    if is_numeric and len(unique_vals) > total_vals * 0.95 and total_vals > 20:
        return True
    return False
