"""
Billing & Subscription module.
"""

from flask import Blueprint

billing_bp = Blueprint('billing', __name__, url_prefix='/api/billing')
billing_pages_bp = Blueprint('billing_pages', __name__)

from . import routes  # noqa: E402, F401 – registers routes on blueprints
