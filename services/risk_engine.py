"""
GenBI Risk Analysis Engine V2 — TOPSIS + Monte Carlo

Domain-agnostic risk analysis pipeline:
  Step 1: AI extracts risk factors from data + adds domain knowledge
  Step 2: TOPSIS ranks risks by multiple criteria
  Step 3: Monte Carlo simulates top risks (probability distributions)
  Step 4: AI generates narrative + recommendations

Pure functions — no Flask dependencies.
"""

import json
import logging
import math
import random
from collections import defaultdict

from services.azure_ai_client import chat_completion_json

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: AI Risk Factor Extraction
# ══════════════════════════════════════════════════════════════════════════════

def extract_risk_factors(headers, rows, max_sample=200):
    """
    Use LLM to identify risk factors from the data + domain knowledge.
    Returns a list of risk factor dicts with scores for TOPSIS criteria.
    """
    sample = rows[:max_sample]

    # Build data summary
    col_summaries = {}
    for h in headers:
        vals = [r.get(h) for r in sample if r.get(h) is not None and str(r.get(h)).strip()]
        str_vals = [str(v).strip() for v in vals]
        unique = list(set(str_vals))[:10]
        num_vals = []
        for v in vals:
            try:
                num_vals.append(float(v))
            except (ValueError, TypeError):
                pass

        col_summaries[h] = {
            'type': 'numeric' if len(num_vals) > len(vals) * 0.6 else 'categorical',
            'unique_count': len(set(str_vals)),
            'sample': unique[:6],
        }
        if num_vals:
            col_summaries[h]['min'] = round(min(num_vals), 2)
            col_summaries[h]['max'] = round(max(num_vals), 2)
            col_summaries[h]['mean'] = round(sum(num_vals) / len(num_vals), 2)
            col_summaries[h]['std'] = round(_std(num_vals), 2)

    prompt = f"""You are a senior risk analyst. Analyze this dataset and identify ALL meaningful risk factors.

DATASET: {len(rows)} rows, {len(headers)} columns.
COLUMNS: {json.dumps(col_summaries, default=str)}

SAMPLE ROWS (first 5):
{json.dumps(sample[:5], default=str)}

═══ INSTRUCTIONS ═══

Identify 5-10 risk factors. For EACH factor, consider:
  A) DATA-DRIVEN risks: anomalies, high variance, declining trends, concentration, outliers you can see in the data.
  B) DOMAIN KNOWLEDGE risks: based on the type of data (pharma, supply chain, finance, sales, etc.), what are known industry risks that this data is exposed to?

For each risk factor, score on 3 TOPSIS criteria (1-10 scale):
  - impact: How severe would this risk be if it materialized? (1=trivial, 10=catastrophic)
  - likelihood: How probable is this risk based on the data signals? (1=unlikely, 10=almost certain)
  - controllability: How much can the organization control/mitigate this? (1=uncontrollable, 10=fully controllable). NOTE: higher = MORE controllable = LESS risky.

═══ OUTPUT FORMAT ═══
Return JSON:
{{
  "domain": "detected domain (pharma/supply-chain/finance/sales/hr/general)",
  "factors": [
    {{
      "id": "short_snake_case_id",
      "name": "Human readable name",
      "description": "One sentence explaining this risk",
      "source": "data" or "knowledge" or "both",
      "category": "operational|financial|compliance|market|technical|strategic",
      "metric_column": "column name this relates to, or null",
      "impact": 7,
      "likelihood": 5,
      "controllability": 6,
      "evidence": "Brief data evidence or domain reasoning"
    }}
  ]
}}

Be specific to THIS data — don't generate generic risks. Reference actual column names and values."""

    try:
        parsed, usage = chat_completion_json(prompt, temperature=0.3, max_tokens=3000)
        factors = parsed.get('factors', [])
        domain = parsed.get('domain', 'general')
        logger.info(f'Risk extraction: {len(factors)} factors, domain={domain}, usage={usage}')
        # Validate and clamp scores
        for f in factors:
            for key in ('impact', 'likelihood', 'controllability'):
                f[key] = max(1, min(10, int(f.get(key, 5))))
            f.setdefault('source', 'both')
            f.setdefault('category', 'operational')
            f.setdefault('metric_column', None)
        return {'domain': domain, 'factors': factors}
    except Exception as e:
        logger.error(f'Risk factor extraction failed: {e}')
        return {'domain': 'general', 'factors': []}


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: TOPSIS Ranking
# ══════════════════════════════════════════════════════════════════════════════

def topsis_rank(factors, weights=None):
    """
    Rank risk factors using TOPSIS multi-criteria decision making.

    Criteria:
      - impact (benefit = higher is worse risk)
      - likelihood (benefit = higher is worse risk)
      - controllability (cost = higher is MORE controllable = LESS risky)

    Parameters
    ----------
    factors : list[dict]
        Each has 'impact', 'likelihood', 'controllability' (1-10).
    weights : dict, optional
        {'impact': w1, 'likelihood': w2, 'controllability': w3}
        Default: equal weights.

    Returns
    -------
    list[dict] — factors with added 'topsis_score' (0-1, higher = riskier)
                 and 'rank' fields, sorted by topsis_score descending.
    """
    if not factors:
        return []

    if weights is None:
        weights = {'impact': 0.4, 'likelihood': 0.35, 'controllability': 0.25}

    # Normalize weights
    w_sum = sum(weights.values())
    w = {k: v / w_sum for k, v in weights.items()}

    n = len(factors)
    criteria = ['impact', 'likelihood', 'controllability']
    # benefit criteria: higher = riskier (impact, likelihood)
    # cost criteria: higher = less risky (controllability)
    benefit = {'impact', 'likelihood'}
    cost = {'controllability'}

    # Step 1: Build decision matrix
    matrix = []
    for f in factors:
        matrix.append([float(f.get(c, 5)) for c in criteria])

    # Step 2: Normalize (vector normalization)
    norm = []
    for j in range(len(criteria)):
        col_sum_sq = math.sqrt(sum(matrix[i][j] ** 2 for i in range(n)))
        col = [matrix[i][j] / col_sum_sq if col_sum_sq > 0 else 0 for i in range(n)]
        norm.append(col)

    # Step 3: Weighted normalized matrix
    weighted = []
    for j, c in enumerate(criteria):
        weighted.append([norm[j][i] * w[c] for i in range(n)])

    # Step 4: Ideal best and worst
    ideal_best = []
    ideal_worst = []
    for j, c in enumerate(criteria):
        col = weighted[j]
        if c in benefit:
            ideal_best.append(max(col))
            ideal_worst.append(min(col))
        else:  # cost
            ideal_best.append(min(col))  # lower controllability = riskier
            ideal_worst.append(max(col))

    # Step 5: Distance to ideal best/worst
    scores = []
    for i in range(n):
        d_best = math.sqrt(sum((weighted[j][i] - ideal_best[j]) ** 2 for j in range(len(criteria))))
        d_worst = math.sqrt(sum((weighted[j][i] - ideal_worst[j]) ** 2 for j in range(len(criteria))))
        # Closeness coefficient (higher = closer to worst = riskier)
        cc = d_worst / (d_best + d_worst) if (d_best + d_worst) > 0 else 0.5
        scores.append(cc)

    # Add scores and rank
    for i, f in enumerate(factors):
        f['topsis_score'] = round(scores[i], 4)
        f['risk_pct'] = round(scores[i] * 100, 1)

    # Sort by topsis_score descending (riskiest first)
    factors.sort(key=lambda x: x['topsis_score'], reverse=True)
    for i, f in enumerate(factors):
        f['rank'] = i + 1

    return factors


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Monte Carlo Simulation
# ══════════════════════════════════════════════════════════════════════════════

def monte_carlo_simulate(factors, rows, headers, n_iterations=10000, top_n=3):
    """
    Run Monte Carlo simulation on the top N risk factors.

    For each numeric metric associated with a risk factor:
      - Fit a distribution from the data (normal or triangular)
      - Run n_iterations samples
      - Compute probability of exceeding thresholds (mean, mean+1σ, mean+2σ)
      - Return histogram bins and statistics

    Parameters
    ----------
    factors : list[dict]
        TOPSIS-ranked factors (must have 'metric_column').
    rows : list[dict]
        Dataset rows.
    headers : list[str]
        Column names.
    n_iterations : int
        Number of Monte Carlo iterations.
    top_n : int
        Simulate only the top N factors.

    Returns
    -------
    list[dict] — simulation results for each factor.
    """
    results = []
    simulated = 0

    for f in factors:
        if simulated >= top_n:
            break

        col = f.get('metric_column')
        if not col or col not in headers:
            # Try to find a related numeric column
            col = _find_related_column(f, headers, rows)
            if not col:
                continue

        # Extract numeric values
        vals = []
        for r in rows:
            try:
                v = float(r.get(col, ''))
                if math.isfinite(v):
                    vals.append(v)
            except (ValueError, TypeError):
                continue

        if len(vals) < 10:
            continue

        # Compute statistics
        mean = sum(vals) / len(vals)
        std = _std(vals)
        min_v = min(vals)
        max_v = max(vals)

        if std < 1e-9:
            continue

        # Monte Carlo: sample from fitted normal distribution
        random.seed(42)  # Reproducible
        samples = [random.gauss(mean, std) for _ in range(n_iterations)]

        # Compute probabilities at data-driven thresholds (percentile-based)
        # Thresholds come from actual data distribution, not the fitted normal
        sorted_vals = sorted(vals)
        # Moderate threshold: 75th percentile of actual data
        threshold_moderate = sorted_vals[int(len(sorted_vals) * 0.75)]
        # High threshold: 90th percentile of actual data
        threshold_high = sorted_vals[int(len(sorted_vals) * 0.90)]
        # Low threshold: 25th percentile (for downside risk)
        threshold_low = sorted_vals[int(len(sorted_vals) * 0.25)]

        p_moderate = sum(1 for s in samples if s > threshold_moderate) / n_iterations
        p_high = sum(1 for s in samples if s > threshold_high) / n_iterations
        p_below_low = sum(1 for s in samples if s < threshold_low) / n_iterations

        # Build histogram (20 bins)
        hist_min = mean - 3 * std
        hist_max = mean + 3 * std
        n_bins = 20
        bin_width = (hist_max - hist_min) / n_bins
        bins = []
        for b in range(n_bins):
            lo = hist_min + b * bin_width
            hi = lo + bin_width
            count = sum(1 for s in samples if lo <= s < hi)
            bins.append({
                'lo': round(lo, 2),
                'hi': round(hi, 2),
                'mid': round((lo + hi) / 2, 2),
                'count': count,
                'pct': round(count / n_iterations * 100, 2),
            })

        # Percentiles
        sorted_samples = sorted(samples)
        p5 = sorted_samples[int(n_iterations * 0.05)]
        p25 = sorted_samples[int(n_iterations * 0.25)]
        p50 = sorted_samples[int(n_iterations * 0.50)]
        p75 = sorted_samples[int(n_iterations * 0.75)]
        p95 = sorted_samples[int(n_iterations * 0.95)]

        results.append({
            'factor_id': f['id'],
            'factor_name': f['name'],
            'column': col,
            'iterations': n_iterations,
            'data_stats': {
                'mean': round(mean, 2),
                'std': round(std, 2),
                'min': round(min_v, 2),
                'max': round(max_v, 2),
                'n': len(vals),
            },
            'probabilities': {
                'exceed_moderate': round(p_moderate * 100, 1),
                'exceed_high': round(p_high * 100, 1),
                'below_low': round(p_below_low * 100, 1),
            },
            'thresholds': {
                'moderate': round(threshold_moderate, 2),
                'high': round(threshold_high, 2),
                'low': round(threshold_low, 2),
            },
            'percentiles': {
                'p5': round(p5, 2),
                'p25': round(p25, 2),
                'p50': round(p50, 2),
                'p75': round(p75, 2),
                'p95': round(p95, 2),
            },
            'histogram': bins,
        })
        simulated += 1

    return results


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: AI Narrative
# ══════════════════════════════════════════════════════════════════════════════

def generate_risk_narrative_v2(domain, factors, simulations):
    """
    Generate AI narrative combining TOPSIS ranking + MC simulation results.
    """
    # Build concise summary for LLM
    top5 = factors[:5]
    factor_lines = []
    for f in top5:
        factor_lines.append(
            f"#{f['rank']} {f['name']} — risk score {f['risk_pct']}% "
            f"(impact:{f['impact']}, likelihood:{f['likelihood']}, "
            f"controllability:{f['controllability']}) — {f['evidence']}"
        )

    mc_lines = []
    for s in simulations:
        mc_lines.append(
            f"- {s['factor_name']} ({s['column']}): "
            f"mean={s['data_stats']['mean']}, std={s['data_stats']['std']}, "
            f"{s['probabilities']['exceed_moderate']}% chance of moderate exceedance, "
            f"{s['probabilities']['exceed_high']}% chance of high exceedance, "
            f"95th percentile={s['percentiles']['p95']}"
        )

    prompt = f"""You are a senior risk analyst writing an executive risk briefing.

DOMAIN: {domain}
METHOD: TOPSIS multi-criteria ranking + Monte Carlo simulation (10,000 iterations)

TOP RISKS (TOPSIS-ranked):
{chr(10).join(factor_lines)}

MONTE CARLO RESULTS:
{chr(10).join(mc_lines) if mc_lines else 'No numeric metrics available for simulation.'}

Write a concise risk briefing:
1. "executive_summary": 2-3 sentences for C-suite. What's the overall risk posture?
2. "key_findings": list of 3-5 specific findings with numbers.
3. "recommendations": list of 3-5 actionable recommendations, each with priority (critical/high/medium).
4. "methodology_note": 1 sentence explaining TOPSIS + MC approach for the reader.

Return JSON:
{{"executive_summary": "...", "key_findings": ["..."], "recommendations": [{{"action": "...", "priority": "critical|high|medium"}}], "methodology_note": "..."}}"""

    try:
        parsed, usage = chat_completion_json(prompt, temperature=0.3, max_tokens=2000)
        return parsed
    except Exception as e:
        logger.error(f'Risk narrative generation failed: {e}')
        return {
            'executive_summary': 'Risk narrative generation unavailable.',
            'key_findings': [],
            'recommendations': [],
            'methodology_note': 'Analysis used TOPSIS ranking and Monte Carlo simulation.',
        }


# ══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def run_full_risk_analysis(headers, rows, weights=None, top_mc=3):
    """
    Run the complete TOPSIS + Monte Carlo risk analysis pipeline.

    Returns
    -------
    dict with keys: domain, factors, simulations, narrative
    """
    # Step 1: Extract risk factors via AI
    extraction = extract_risk_factors(headers, rows)
    domain = extraction['domain']
    factors = extraction['factors']

    if not factors:
        return {
            'domain': domain,
            'factors': [],
            'simulations': [],
            'narrative': {
                'executive_summary': 'No risk factors detected in this dataset.',
                'key_findings': [],
                'recommendations': [],
                'methodology_note': '',
            },
        }

    # Step 2: TOPSIS ranking
    factors = topsis_rank(factors, weights)

    # Step 3: Monte Carlo simulation on top factors
    simulations = monte_carlo_simulate(factors, rows, headers, top_n=top_mc)

    # Step 4: AI narrative
    narrative = generate_risk_narrative_v2(domain, factors, simulations)

    return {
        'domain': domain,
        'factors': factors,
        'simulations': simulations,
        'narrative': narrative,
    }


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _std(vals):
    if len(vals) < 2:
        return 0
    mean = sum(vals) / len(vals)
    return math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))


def _find_related_column(factor, headers, rows):
    """Try to find a numeric column related to the factor name."""
    name_lower = factor.get('name', '').lower()
    desc_lower = factor.get('description', '').lower()
    keywords = set(name_lower.split() + desc_lower.split())
    # Remove common stop words
    keywords -= {'the', 'a', 'an', 'is', 'of', 'in', 'to', 'and', 'or', 'for', 'with', 'high', 'low', 'risk'}

    best_col = None
    best_score = 0
    for h in headers:
        # Check if column is numeric
        num_count = 0
        for r in rows[:50]:
            try:
                float(r.get(h, ''))
                num_count += 1
            except (ValueError, TypeError):
                pass
        if num_count < len(rows[:50]) * 0.5:
            continue

        # Score by keyword overlap
        h_words = set(h.lower().replace('_', ' ').split())
        overlap = len(keywords & h_words)
        if overlap > best_score:
            best_score = overlap
            best_col = h

    return best_col if best_score > 0 else None
