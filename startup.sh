#!/bin/bash
# Azure App Service startup script.
# Runs migrations then starts gunicorn.
set -e

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
