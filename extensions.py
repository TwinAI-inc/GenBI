"""
Shared Flask extensions – imported by models and app factory.
Keeps circular imports at bay.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()
