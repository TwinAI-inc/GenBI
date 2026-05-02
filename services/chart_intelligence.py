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
from services.chart_knowledge import CHART_KNOWLEDGE_CONTEXT

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

# Flat set of every chart type the renderer actually supports. Used to reject
# unknown LLM output before it reaches the dashboard.
ALL_CHART_TYPES = {t for types in CHART_FAMILIES.values() for t in types}

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

        # Boolean detection — require at least TWO distinct values from the
        # boolean vocabulary. A single-value column ('1' only, or 'yes' only)
        # passing the subset check trivially is not actually boolean; it is a
        # constant. An all-null column (empty unique set) also trivially
        # passes the subset rule and must be excluded.
        bool_vocab = {'true', 'false', 'yes', 'no', '1', '0', 't', 'f', 'y', 'n'}
        is_empty = len(unique) == 0
        unique_lower = set(s.lower() for s in unique)
        is_boolean = (
            len(unique_lower) >= 2
            and unique_lower <= bool_vocab
        )

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
            'type': (
                'empty' if is_empty else
                'date' if is_date else
                'geographic' if is_geo else
                'boolean' if is_boolean else
                'numeric' if (is_numeric and not is_id) else
                'id' if is_id else
                'categorical'
            ),
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

    prompt = f"""You are an expert BI analyst. Analyze this dataset and generate chart candidates ranked by insight value.

{CHART_KNOWLEDGE_CONTEXT}

DATASET PROFILE:
- Rows: {profile['row_count']}
- Columns: {profile['column_count']}
- Column details: {json.dumps(col_summary, default=str)}
- Correlations: {json.dumps(profile.get('correlations', []), default=str)}

══ PIPELINE: Generate 3 categories of chart candidates ══

CATEGORY A — SINGLE FACTOR CHARTS (generate 4-5):
Charts based on ONE metric vs ONE category/dimension.
Examples: Revenue by Channel, Risk Score by Phase, Deals by Month.

CATEGORY B — COMBINED FACTOR CHARTS (generate 2-3):
Charts combining 2+ dimensions for deeper analysis.
Examples: Revenue Trend by Region (time + category), Correlation between Revenue and Deals (2 metrics), Stacked composition over time.

CATEGORY C — DECOMPOSED/DERIVED FACTOR CHARTS (generate 1-3):
Charts using derived features, new categories, or innovative decomposition.
Examples: Bin Revenue into High/Medium/Low → donut, Extract Quarter from Date → seasonal pattern, Create Boolean "Above Average" flag → comparison, Group long-tail into Top 5 + Other.

══ INSIGHT SCORING (rate each chart 1-10 on each criterion) ══

Score every chart on these 6 criteria:
1. **Information Density**: How much useful info does this chart pack? (1=trivial, 10=rich)
2. **Actionability**: Can a decision-maker act on this insight? (1=decorative, 10=directly actionable)
3. **Surprise Factor**: Does it reveal something non-obvious? (1=expected, 10=surprising pattern)
4. **Visual Clarity**: Is this the RIGHT chart type for this data? (1=wrong type, 10=perfect fit)
5. **Data Coverage**: How much of the dataset does it use? (1=tiny slice, 10=comprehensive)
6. **Business Relevance**: How important is this metric/dimension? (1=trivial, 10=critical KPI)

Total insight_score = average of 6 criteria (1.0 to 10.0).

══ CHART RULES ══
- Donut: max 6 categories. If 7-15 use treemap. If 16+ use hbar.
- Bar: max 8 bars, rest as "Other"
- Multi-line: max 4 series
- Scatter: ONLY if correlation >0.3
- Maps: ALWAYS if State/Country column detected (high priority)
- Equal distribution: use bar with note, NOT donut
- Titles must be INSIGHT-DRIVEN: "Revenue Peaks in Q4, Led by Enterprise" NOT "Revenue by Quarter"
- Aggregation: SUM for amounts, AVG for rates/scores, COUNT for records
- IMPORTANT — CHART TYPE DIVERSITY: Do NOT default everything to bar charts. Use the BEST chart type for the data:
  * Time series → multiline or area (NOT bar)
  * Proportions/shares → donut or treemap (NOT bar)
  * Rankings with long labels → hbar (NOT bar)
  * Sequential stages → funnel
  * Single KPI → gauge
  * Two numeric correlations → scatter
  * Geographic data → usmap/worldmap
  * Aim for at least 3 DIFFERENT chart types across your candidates

══ DOMAIN DETECTION ══
Auto-detect domain and apply domain-specific intelligence:
- Pharma: phases→funnel, risk→gauge, efficacy→scatter
- Sales: geography→map, time→trend, channels→comparison
- Supply Chain: defects→gauge, facilities→hbar, time→trend

══ OUTPUT FORMAT ══
Return JSON: {{"charts": [...]}} with each chart object:
{{
  "type": "bar|hbar|multiline|stacked|donut|treemap|scatter|gauge|boxplot|radar|usmap|worldmap|funnel|area",
  "category": "A_single|B_combined|C_derived",
  "title": "Insight-driven title",
  "xCol": "column or null",
  "yCol": "column or null",
  "groupCol": "column for grouping or null",
  "aggFn": "sum|avg|count",
  "maxItems": 8,
  "color": "cyan|teal|emerald|rose",
  "desc": "One sentence insight",
  "derived_column": "parseable derivation instruction or null. Use EXACTLY one of these patterns: 'Extract Quarter from DateCol' | 'Extract Month from DateCol' | 'Extract Year from DateCol' | 'Bin MetricCol into High/Medium/Low' | 'Above Average MetricCol' | 'Below Median MetricCol' | 'Top 5 + Other CatCol'. Set xCol to the source column; the frontend will create the new column and rebind xCol automatically.",
  "family": "comparison|trend|composition|relationship|distribution|geographic",
  "scores": {{
    "information_density": 7,
    "actionability": 8,
    "surprise_factor": 5,
    "visual_clarity": 9,
    "data_coverage": 6,
    "business_relevance": 8
  }},
  "insight_score": 7.2
}}

Generate 8-12 total candidates. The top {max_charts} by insight_score will be displayed."""

    try:
        # chat_completion_json expects a string prompt (not message list)
        # and returns (parsed_json, usage_dict) tuple
        parsed, usage = chat_completion_json(
            prompt,
            temperature=0.3,
            max_tokens=4500
        )
        logger.info(f'LLM chart plan parsed type: {type(parsed).__name__}, usage: {usage}')
        if isinstance(parsed, list):
            chart_plan = parsed
        elif isinstance(parsed, dict):
            # LLM wrapped in object — find the array
            chart_plan = (
                parsed.get('charts') or parsed.get('chart_plan') or
                parsed.get('plan') or parsed.get('data') or
                next((v for v in parsed.values() if isinstance(v, list)), [])
            )
        else:
            chart_plan = []

        logger.info(f'LLM returned {len(chart_plan)} charts')

        # Validate and enforce guardrails
        chart_plan = _validate_guardrails(chart_plan, profile)

        # Sort by insight score descending, then apply diversity selection.
        chart_plan.sort(key=lambda c: c.get('insight_score', 0), reverse=True)
        chart_plan = _select_diverse(chart_plan, max_charts)

        # If LLM output is unusable for any reason, fall back to a deterministic
        # rule-based planner so the dashboard never returns an empty plan.
        if not chart_plan:
            logger.warning('LLM chart plan empty after validation; using rule-based fallback')
            chart_plan = _fallback_chart_plan(profile, max_charts)

        logger.info(f'Final chart plan: {len(chart_plan)} charts, scores: {[c.get("insight_score", 0) for c in chart_plan]}')
        return chart_plan

    except json.JSONDecodeError as e:
        logger.error(f'Failed to parse chart plan JSON: {e}')
        return _fallback_chart_plan(profile, max_charts)
    except Exception as e:
        logger.error(f'Chart plan generation failed: {e}')
        return _fallback_chart_plan(profile, max_charts)


# ── Guardrail Validator ───────────────────────────────────────────────────────

def _validate_guardrails(plan, profile):
    """Validate and fix chart plan against guardrails.

    Rejects charts that don't conform to the renderer's contract: must be a
    dict, must have a recognised type, and must reference real columns when
    they specify any. Family-diversity selection is handled separately by
    ``_select_diverse`` after this pass.
    """
    validated = []

    if not isinstance(plan, list):
        return validated

    valid_columns = set((profile or {}).get('columns', {}).keys())

    for chart in plan:
        if not isinstance(chart, dict):
            continue

        chart_type = chart.get('type', 'bar')
        # Coerce unknown types to the closest safe default rather than letting
        # them reach the renderer (where they would silently render nothing).
        if chart_type not in ALL_CHART_TYPES:
            chart_type = 'bar'
            chart['type'] = 'bar'
            chart['guardrail_notes'] = 'Coerced unknown chart type to bar'
        family = chart.get('family', _get_family(chart_type))
        chart['family'] = family

        # Drop charts that reference columns that don't exist in the dataset.
        for key in ('xCol', 'yCol', 'groupCol'):
            ref = chart.get(key)
            if ref and valid_columns and ref not in valid_columns:
                chart[key] = None

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

    return validated


def _select_diverse(plan, max_charts):
    """Pick up to ``max_charts`` charts with one-per-family priority.

    First pass: best-scoring chart from each family, in family order. Second
    pass: fill any remaining slots from the leftovers (allowing repeat
    families) by score. Guarantees the returned plan covers the maximum
    number of distinct families before any family is duplicated.
    """
    if not plan or max_charts <= 0:
        return []

    by_family = {}
    for chart in plan:
        family = chart.get('family') or _get_family(chart.get('type', 'bar'))
        by_family.setdefault(family, []).append(chart)

    selected = []
    taken_ids = set()

    # First pass: one chart per family, best score first.
    for family, charts in by_family.items():
        charts.sort(key=lambda c: c.get('insight_score', 0), reverse=True)
        if len(selected) >= max_charts:
            break
        best = charts[0]
        selected.append(best)
        taken_ids.add(id(best))

    # Second pass: fill remaining slots by overall score, allowing repeats.
    remaining = [c for c in plan if id(c) not in taken_ids]
    remaining.sort(key=lambda c: c.get('insight_score', 0), reverse=True)
    for chart in remaining:
        if len(selected) >= max_charts:
            break
        selected.append(chart)

    return selected


def _fallback_chart_plan(profile, max_charts):
    """Deterministic rule-based chart plan used when the LLM path fails or
    returns nothing usable. Picks one representative chart per applicable
    family from the data profile.
    """
    if not profile or not profile.get('columns'):
        return []

    cols = profile['columns']
    by_type = {t: [] for t in ('numeric', 'date', 'categorical', 'geographic', 'boolean')}
    for name, info in cols.items():
        t = info.get('type')
        if t in by_type:
            by_type[t].append((name, info))

    plan = []

    def add(chart):
        chart.setdefault('family', _get_family(chart.get('type', 'bar')))
        chart.setdefault('insight_score', 5.0)
        chart.setdefault('color', 'cyan')
        chart.setdefault('guardrail_notes', 'Rule-based fallback')
        plan.append(chart)

    numeric = by_type['numeric']
    dates   = by_type['date']
    cats    = by_type['categorical']
    geos    = by_type['geographic']

    # 1. Trend: first date column × first numeric column.
    if dates and numeric:
        add({
            'type': 'line', 'title': f'{numeric[0][0]} over {dates[0][0]}',
            'xCol': dates[0][0], 'yCol': numeric[0][0], 'aggFn': 'sum',
            'desc': f'Trend of {numeric[0][0]} across {dates[0][0]}.',
        })

    # 2. Comparison: first low-cardinality categorical × first numeric.
    cat_pick = next(
        ((n, i) for n, i in cats if 2 <= i.get('cardinality', 0) <= 30),
        None,
    )
    if cat_pick and numeric:
        add({
            'type': 'bar', 'title': f'{numeric[0][0]} by {cat_pick[0]}',
            'xCol': cat_pick[0], 'yCol': numeric[0][0], 'aggFn': 'sum',
            'maxItems': 8,
            'desc': f'{numeric[0][0]} broken down by {cat_pick[0]}.',
        })

    # 3. Composition: small-cardinality categorical (<=6) -> donut.
    small_cat = next(
        ((n, i) for n, i in cats if 2 <= i.get('cardinality', 0) <= 6),
        None,
    )
    if small_cat and numeric:
        add({
            'type': 'donut', 'title': f'{numeric[0][0]} share by {small_cat[0]}',
            'xCol': small_cat[0], 'yCol': numeric[0][0], 'aggFn': 'sum',
            'desc': f'Share of {numeric[0][0]} across {small_cat[0]}.',
        })

    # 4. Relationship: highest-correlation numeric pair.
    correlations = profile.get('correlations') or []
    if correlations:
        best = correlations[0]
        add({
            'type': 'scatter', 'title': f'{best["col1"]} vs {best["col2"]}',
            'xCol': best['col1'], 'yCol': best['col2'],
            'desc': f'Relationship between {best["col1"]} and {best["col2"]} (r = {best["correlation"]}).',
        })

    # 5. Distribution: first numeric -> boxplot.
    if numeric:
        add({
            'type': 'boxplot', 'title': f'Distribution of {numeric[0][0]}',
            'yCol': numeric[0][0],
            'desc': f'Spread, median, and outliers of {numeric[0][0]}.',
        })

    # 6. Geographic: any geo column with a numeric measure.
    if geos and numeric:
        geo_name, geo_info = geos[0]
        chart_type = 'usmap' if geo_info.get('geo_type') == 'us_state' else 'worldmap'
        add({
            'type': chart_type, 'title': f'{numeric[0][0]} by {geo_name}',
            'xCol': geo_name, 'yCol': numeric[0][0], 'aggFn': 'sum',
            'desc': f'{numeric[0][0]} distribution across {geo_name}.',
        })

    return plan[:max_charts]


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
