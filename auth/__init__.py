"""
GenBI Authentication Module
Blueprint registration and DB initialization.
"""

from flask import Blueprint

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')
pages_bp = Blueprint('auth_pages', __name__)

from . import routes  # noqa: E402, F401 – registers routes on blueprints
