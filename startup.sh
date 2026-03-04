#!/bin/bash
# Azure App Service startup script.
# Sets up the vendored package path, runs migrations, then starts gunicorn.
set -e

# Add vendored packages to Python path (Azure zip-deploy layout)
SITE_PACKAGES="/home/site/wwwroot/.python_packages/lib/site-packages"
if [ -d "$SITE_PACKAGES" ]; then
    export PYTHONPATH="${SITE_PACKAGES}:${PYTHONPATH:-}"
    echo "[startup] PYTHONPATH includes $SITE_PACKAGES"
fi

export FLASK_APP=server:app

echo "[startup] Running database migrations..."
flask db upgrade

echo "[startup] Starting gunicorn..."
exec gunicorn \
  --bind=0.0.0.0:8000 \
  --workers=2 \
  --threads=4 \
  --timeout=120 \
  --access-logfile=- \
  --error-logfile=- \
  "server:app"
