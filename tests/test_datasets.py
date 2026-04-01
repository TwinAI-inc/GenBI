"""
Tests for the generic dataset profiler, chart suggestions, and aggregation.

Profiler and chart_suggestions are pure-Python and need no Flask context.
Aggregator helpers are tested via direct import of the internal functions.
"""

import sys
import os
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datasets.profiler import profile_columns
from datasets.chart_suggestions import suggest_charts

# Import aggregator internals without triggering DB imports
# by mocking extensions before import
from unittest.mock import MagicMock
sys.modules.setdefault('flask_migrate', MagicMock())
if 'extensions' not in sys.modules:
    import importlib
    spec = importlib.util.spec_from_file_location('extensions',
        os.path.join(os.path.dirname(os.path.dirname(__file__)), 'extensions.py'))
    mod = importlib.util.module_from_spec(spec)
    mod.db = MagicMock()
    mod.migrate = MagicMock()
    sys.modules['extensions'] = mod

from datasets.aggregator import _single_agg, _multi_series_agg, _histogram


# ── Sample data fixtures ──

def _sales_rows():
    """Minimal sales dataset."""
    rows = []
    months = ['2024-01', '2024-02', '2024-03', '2024-06', '2024-12']
    channels = ['Direct Sales', 'Online', 'Enterprise']
    for m in months:
        for ch in channels:
            rows.append({
                'Month': m,
                'Channel': ch,
                'Revenue': 100_000 + hash(m + ch) % 50_000,
                'Deals': 5 + hash(m + ch) % 10,
                'Rep_ID': f'REP-{hash(m+ch) % 100:03d}',
            })
    return rows


def _simple_rows():
    """Minimal categorical + numeric dataset."""
    return [
        {'Category': 'A', 'Value': 10},
        {'Category': 'B', 'Value': 20},
        {'Category': 'A', 'Value': 30},
        {'Category': 'C', 'Value': 15},
        {'Category': 'B', 'Value': 25},
    ]


# ── Profiler tests ──

class TestProfiler:
    def test_detects_datetime(self):
        rows = _sales_rows()
        profiles = profile_columns(rows)
        month_col = next(p for p in profiles if p['name'] == 'Month')
        assert month_col['type'] == 'datetime'

    def test_detects_numeric(self):
        rows = _sales_rows()
        profiles = profile_columns(rows)
        rev_col = next(p for p in profiles if p['name'] == 'Revenue')
        assert rev_col['type'] == 'numeric'
        assert 'stats' in rev_col
        assert rev_col['stats']['min'] <= rev_col['stats']['max']

    def test_detects_categorical(self):
        rows = _sales_rows()
        profiles = profile_columns(rows)
        ch_col = next(p for p in profiles if p['name'] == 'Channel')
        assert ch_col['type'] == 'categorical'
        assert ch_col['cardinality'] == 3

    def test_detects_id_like(self):
        rows = _sales_rows()
        profiles = profile_columns(rows)
        id_col = next(p for p in profiles if p['name'] == 'Rep_ID')
        assert id_col['type'] == 'id_like'

    def test_null_pct(self):
        rows = [{'A': 1}, {'A': None}, {'A': 3}, {'A': ''}, {'A': 5}]
        profiles = profile_columns(rows)
        assert profiles[0]['null_pct'] == 40.0  # 2 out of 5

    def test_empty_dataset(self):
        assert profile_columns([]) == []


# ── Chart suggestion tests ──

class TestChartSuggestions:
    def test_generates_suggestions(self):
        rows = _sales_rows()
        profiles = profile_columns(rows)
        suggestions = suggest_charts(profiles)
        assert len(suggestions) >= 3
        assert all('chart_id' in s for s in suggestions)
        assert all('type' in s for s in suggestions)

    def test_no_id_like_in_suggestions(self):
        rows = _sales_rows()
        profiles = profile_columns(rows)
        suggestions = suggest_charts(profiles)
        for s in suggestions:
            assert s['x'] != 'Rep_ID'
            assert s.get('y') != 'Rep_ID'
            assert s.get('series') != 'Rep_ID'

    def test_time_series_included(self):
        rows = _sales_rows()
        profiles = profile_columns(rows)
        suggestions = suggest_charts(profiles)
        time_charts = [s for s in suggestions if s['sort'] == 'time']
        assert len(time_charts) >= 1

    def test_stacked_bar_for_channel(self):
        rows = _sales_rows()
        profiles = profile_columns(rows)
        suggestions = suggest_charts(profiles)
        stacked = [s for s in suggestions if s['type'] == 'stacked_bar']
        assert len(stacked) >= 1
        assert stacked[0]['series'] is not None

    def test_simple_data_suggestions(self):
        rows = _simple_rows()
        profiles = profile_columns(rows)
        suggestions = suggest_charts(profiles)
        assert len(suggestions) >= 1


# ── Aggregation tests ──

class TestAggregation:
    def test_single_agg_sum(self):
        rows = _simple_rows()
        result = _single_agg(rows, 'Category', 'Value', 'sum', 'desc', None)
        assert 'A' in result['labels']
        idx_a = result['labels'].index('A')
        assert result['data'][idx_a] == 40  # 10 + 30

    def test_single_agg_count(self):
        rows = _simple_rows()
        result = _single_agg(rows, 'Category', 'Value', 'count', 'desc', None)
        idx_a = result['labels'].index('A')
        assert result['data'][idx_a] == 2

    def test_single_agg_avg(self):
        rows = _simple_rows()
        result = _single_agg(rows, 'Category', 'Value', 'avg', 'desc', None)
        idx_a = result['labels'].index('A')
        assert result['data'][idx_a] == 20.0  # (10+30)/2

    def test_chronological_sort(self):
        rows = _sales_rows()
        result = _single_agg(rows, 'Month', 'Revenue', 'sum', 'time', None)
        labels = result['labels']
        assert labels == sorted(labels), f"Not chronological: {labels}"

    def test_multi_series(self):
        rows = _sales_rows()
        result = _multi_series_agg(rows, 'Month', 'Revenue', 'sum', 'Channel', 'time', None)
        assert result['series'] is not None
        assert len(result['series']) == 3  # 3 channels
        assert len(result['labels']) == 5  # 5 months
        # Labels must be chronological
        assert result['labels'] == sorted(result['labels'])

    def test_limit(self):
        rows = _simple_rows()
        result = _single_agg(rows, 'Category', 'Value', 'sum', 'desc', 2)
        assert len(result['labels']) <= 2

    def test_histogram(self):
        rows = [{'Val': i * 10} for i in range(100)]
        result = _histogram(rows, 'Val', bins=10)
        assert len(result['labels']) == 10
        assert sum(result['data']) == 100


# ── Insight validation helpers ──

class TestInsightComputation:
    def test_peak_and_low(self):
        """Verify that peak/low can be computed from aggregated data."""
        rows = _sales_rows()
        result = _single_agg(rows, 'Month', 'Revenue', 'sum', 'time', None)
        labels = result['labels']
        data = result['data']
        peak_idx = data.index(max(data))
        low_idx = data.index(min(data))
        assert labels[peak_idx] is not None
        assert labels[low_idx] is not None
        assert data[peak_idx] >= data[low_idx]
