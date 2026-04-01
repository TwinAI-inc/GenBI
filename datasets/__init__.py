"""
Datasets module — generic CSV upload, profiling, chart suggestion, and drilldown.
"""

from flask import Blueprint

datasets_bp = Blueprint('datasets', __name__, url_prefix='/api/datasets')
