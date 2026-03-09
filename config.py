"""
Application configuration classes.

All environment variables are read lazily inside class properties
or at app init time — never at module import time.
"""

import os


class _BaseConfig:
    """Shared defaults. Subclasses override as needed."""
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session cookie security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # CSRF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_CHECK_DEFAULT = False  # We check manually on API routes
    WTF_CSRF_HEADERS = ['X-CSRFToken', 'X-CSRF-Token']

    @staticmethod
    def init_app(app):
        """Hook for subclass-specific initialisation."""
        pass


class DevelopmentConfig(_BaseConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False  # localhost is HTTP

    @staticmethod
    def init_app(app):
        app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret')
        app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
            'DATABASE_URL', 'postgresql://localhost/genbi_auth'
        )


class ProductionConfig(_BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True  # HTTPS only

    # Required env vars — fail fast if missing
    _REQUIRED = [
        'FLASK_SECRET_KEY', 'JWT_SECRET_KEY', 'DATABASE_URL',
    ]

    @staticmethod
    def init_app(app):
        missing = [k for k in ProductionConfig._REQUIRED if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

        jwt_key = os.environ.get('JWT_SECRET_KEY', '')
        if jwt_key.startswith('dev-') or len(jwt_key) < 32:
            raise RuntimeError(
                'JWT_SECRET_KEY must be at least 32 chars and must not use a dev default'
            )

        flask_key = os.environ.get('FLASK_SECRET_KEY', '')
        if flask_key.startswith('dev-') or len(flask_key) < 16:
            raise RuntimeError(
                'FLASK_SECRET_KEY must be at least 16 chars and must not use a dev default'
            )

        app.config['SECRET_KEY'] = flask_key
        app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']


_config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
}


def get_config():
    """Return the config class for the current FLASK_ENV."""
    env = os.environ.get('FLASK_ENV', 'development').lower()
    return _config_map.get(env, DevelopmentConfig)
