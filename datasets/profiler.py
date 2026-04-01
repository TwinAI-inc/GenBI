"""
Column profiler — infers types from raw CSV data.

Types: datetime, numeric, categorical, id_like, text_blob
"""

import re
from datetime import datetime

# Patterns for date detection (checked on sample values)
_DATE_PATTERNS = [
    (r'^\d{4}-\d{2}-\d{2}$', '%Y-%m-%d'),
    (r'^\d{4}-\d{2}$', '%Y-%m'),
    (r'^\d{1,2}/\d{1,2}/\d{2,4}$', None),  # US date
    (r'^\d{4}-\d{2}-\d{2}T', None),  # ISO8601
    (r'^Q[1-4]\s*\d{4}$', None),  # Quarter
    (r'^[A-Za-z]+\s+\d{4}$', None),  # "January 2024"
    (r'^(19|20)\d{2}$', None),  # Year only
]

_ID_NAME_PATTERNS = re.compile(
    r'(?:^|[_\s\-.])(id|uuid|guid|index|row|sr|sno|serial|code|sku|key)(?:$|[_\s\-.])', re.I
)


def _is_numeric(val):
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


def _is_date_like(vals):
    """Check if >70% of non-null values match a date pattern."""
    sample = [str(v).strip() for v in vals if v is not None and str(v).strip()][:50]
    if len(sample) < 3:
        return False
    for regex, _ in _DATE_PATTERNS:
        hits = sum(1 for v in sample if re.match(regex, v))
        if hits >= len(sample) * 0.7:
            return True
    return False


def profile_columns(rows, max_profile_rows=5000):
    """
    Profile columns from a list of dicts.

    Returns list of column profile dicts:
    [
        {
            "name": "col",
            "type": "numeric|datetime|categorical|id_like|text_blob",
            "cardinality": int,
            "null_pct": float,
            "sample_values": [...],
            "stats": {"min": ..., "max": ..., "mean": ...}  # numeric only
        }
    ]
    """
    if not rows:
        return []

    headers = list(rows[0].keys())
    sample = rows[:max_profile_rows]
    n = len(sample)
    profiles = []

    for col in headers:
        vals = [r.get(col) for r in sample]
        non_null = [v for v in vals if v is not None and str(v).strip() != '']
        null_count = n - len(non_null)
        null_pct = round(null_count / n * 100, 1) if n else 0.0

        str_vals = [str(v).strip() for v in non_null]
        unique_vals = set(str_vals)
        cardinality = len(unique_vals)

        # Sample values (first 8 unique, truncated)
        sample_vals = [v[:100] for v in list(unique_vals)[:8]]

        # Type inference
        col_type = _infer_type(col, str_vals, non_null, cardinality, n)

        profile = {
            'name': col,
            'type': col_type,
            'cardinality': cardinality,
            'null_pct': null_pct,
            'sample_values': sample_vals,
        }

        # Numeric stats
        if col_type == 'numeric' and non_null:
            nums = []
            for v in non_null:
                try:
                    nums.append(float(v))
                except (ValueError, TypeError):
                    pass
            if nums:
                profile['stats'] = {
                    'min': round(min(nums), 2),
                    'max': round(max(nums), 2),
                    'mean': round(sum(nums) / len(nums), 2),
                }

        profiles.append(profile)

    return profiles


def _infer_type(col_name, str_vals, non_null, cardinality, total_rows):
    """Infer the column type."""
    if not non_null:
        return 'text_blob'

    # Check numeric
    numeric_count = sum(1 for v in str_vals[:200] if _is_numeric(v))
    is_numeric = numeric_count > len(str_vals[:200]) * 0.8

    # Check ID-like by name
    is_id_name = bool(_ID_NAME_PATTERNS.search(col_name))

    # ID-like: column name contains id/code/sku/key pattern → always id_like
    if is_id_name:
        return 'id_like'
    # High cardinality text without id name → still id_like
    if not is_numeric and cardinality > total_rows * 0.9 and total_rows > 10:
        return 'id_like'

    # Check date
    if _is_date_like(non_null):
        return 'datetime'

    # Numeric
    if is_numeric:
        # But could be year — check
        if cardinality <= 30 and all(re.match(r'^(19|20)\d{2}$', v) for v in str_vals[:20] if v):
            return 'datetime'
        return 'numeric'

    # Categorical vs text blob
    if cardinality <= max(30, total_rows * 0.3):
        return 'categorical'

    return 'text_blob'
