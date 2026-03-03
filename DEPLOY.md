# GenBI Dashboard — Azure Deployment Guide

## Prerequisites

- Azure CLI installed (`az --version`)
- GitHub repo with `AZURE_WEBAPP_PUBLISH_PROFILE` secret configured
- PostgreSQL database (Azure Database for PostgreSQL Flexible Server recommended)

---

## Phase 1: Azure Resource Setup

```bash
# Variables — edit these
RG="genbi-rg"
LOCATION="eastus"
APP_NAME="genbi-dashboard"
PLAN_NAME="genbi-plan"
DB_SERVER="genbi-db"
DB_NAME="genbi_auth"
DB_USER="genbi_admin"
DB_PASS="$(openssl rand -base64 24)"

# Resource group
az group create --name $RG --location $LOCATION

# App Service Plan (B1 for staging, P1v3 for production)
az appservice plan create \
  --name $PLAN_NAME \
  --resource-group $RG \
  --sku B1 \
  --is-linux

# Web App (Python 3.12)
az webapp create \
  --name $APP_NAME \
  --resource-group $RG \
  --plan $PLAN_NAME \
  --runtime "PYTHON:3.12"

# PostgreSQL Flexible Server
az postgres flexible-server create \
  --name $DB_SERVER \
  --resource-group $RG \
  --location $LOCATION \
  --admin-user $DB_USER \
  --admin-password "$DB_PASS" \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --version 16 \
  --yes

# Create database
az postgres flexible-server db create \
  --resource-group $RG \
  --server-name $DB_SERVER \
  --database-name $DB_NAME

# Allow Azure services to connect
az postgres flexible-server firewall-rule create \
  --resource-group $RG \
  --name $DB_SERVER \
  --rule-name AllowAzure \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0
```

## Phase 2: Configure App Settings

```bash
# Generate secure keys
JWT_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
FLASK_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
DB_HOST="$DB_SERVER.postgres.database.azure.com"
DB_URL="postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:5432/${DB_NAME}?sslmode=require"

az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RG \
  --settings \
    FLASK_ENV=production \
    DATABASE_URL="$DB_URL" \
    JWT_SECRET_KEY="$JWT_KEY" \
    FLASK_SECRET_KEY="$FLASK_KEY" \
    EMAIL_PROVIDER=acs \
    APP_BASE_URL="https://${APP_NAME}.azurewebsites.net" \
    CORS_ORIGINS="https://${APP_NAME}.azurewebsites.net" \
    SCM_DO_BUILD_DURING_DEPLOYMENT=false \
    GOOGLE_OAUTH_CLIENT_ID="your-client-id" \
    GOOGLE_OAUTH_CLIENT_SECRET="your-client-secret" \
    GOOGLE_OAUTH_REDIRECT_URI="https://${APP_NAME}.azurewebsites.net/auth/google/callback"
```

## Phase 3: Startup Command + Hardening

```bash
# Set startup command
az webapp config set \
  --name $APP_NAME \
  --resource-group $RG \
  --startup-file "gunicorn --bind=0.0.0.0 --timeout 600 --workers 1 --chdir /home/site/wwwroot startup_wrapper:app"

# HTTPS only
az webapp update \
  --name $APP_NAME \
  --resource-group $RG \
  --https-only true

# Disable FTP
az webapp config set \
  --name $APP_NAME \
  --resource-group $RG \
  --ftps-state Disabled

# Minimum TLS 1.2
az webapp config set \
  --name $APP_NAME \
  --resource-group $RG \
  --min-tls-version 1.2

# Enable app logging
az webapp log config \
  --name $APP_NAME \
  --resource-group $RG \
  --application-logging filesystem \
  --level warning \
  --detailed-error-messages true
```

## Phase 4: GitHub Actions Setup

1. Download the publish profile:
   ```bash
   az webapp deployment list-publishing-profiles \
     --name $APP_NAME \
     --resource-group $RG \
     --xml > /dev/null  # Don't save to file — copy from portal instead
   ```

2. In GitHub repo → Settings → Secrets → Actions:
   - Add `AZURE_WEBAPP_PUBLISH_PROFILE` with the XML content

3. In GitHub repo → Settings → Variables → Actions:
   - Add `AZURE_WEBAPP_NAME` = your app name

4. Push to `main` to trigger deployment.

---

## Post-Deploy Validation

Run these checks after every deployment:

```bash
APP_URL="https://${APP_NAME}.azurewebsites.net"

# 1. Health check
curl -sf "$APP_URL/health" | jq .
# Expected: {"status":"ok"}

# 2. Security headers
curl -sI "$APP_URL/" | grep -E "^(X-|Content-Security|Strict-Transport|Referrer)"
# Expected: X-Content-Type-Options, X-Frame-Options, CSP, HSTS, etc.

# 3. CSRF token endpoint
curl -sf "$APP_URL/api/csrf-token" | jq .
# Expected: {"csrf_token":"..."}

# 4. Auth endpoint responds (should reject bad login, not 500)
curl -sf -X POST "$APP_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@test.com","password":"test"}' | jq .status
# Expected: 401 or 422, NOT 500

# 5. Rate limiting works
for i in {1..6}; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST "$APP_URL/api/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"email":"test@test.com","password":"wrong"}'
done
# Expected: 401 401 401 401 401 429

# 6. Static files served
curl -sf -o /dev/null -w "%{http_code}" "$APP_URL/static/auth.css"
# Expected: 200

# 7. Check logs for startup errors
az webapp log tail --name $APP_NAME --resource-group $RG --timeout 10
```

---

## Rollback Plan

### Quick rollback (redeploy previous version)

```bash
# List recent deployments
az webapp deployment list \
  --name $APP_NAME \
  --resource-group $RG \
  --output table

# Redeploy from a specific commit (via GitHub Actions)
# → Go to Actions tab → select the last successful deploy → "Re-run all jobs"
```

### Database rollback (if migration breaks)

```bash
# SSH into the app container
az webapp ssh --name $APP_NAME --resource-group $RG

# Inside the container:
flask db downgrade -1
```

### Emergency: swap to maintenance page

```bash
# Stop the app (shows Azure default maintenance page)
az webapp stop --name $APP_NAME --resource-group $RG

# Restart after fixing
az webapp start --name $APP_NAME --resource-group $RG
```

### Nuclear option: restore from backup

```bash
# Azure automatically backs up Flexible Server
az postgres flexible-server restore \
  --resource-group $RG \
  --name "${DB_SERVER}-restored" \
  --source-server $DB_SERVER \
  --restore-time "2024-01-15T00:00:00Z"
```

---

## Production Checklist

- [ ] `FLASK_ENV=production` is set
- [ ] `JWT_SECRET_KEY` is 64+ hex chars, not starting with `dev-`
- [ ] `FLASK_SECRET_KEY` is 32+ hex chars, not starting with `dev-`
- [ ] `DATABASE_URL` uses `?sslmode=require`
- [ ] `HTTPS-only` enabled on App Service
- [ ] FTP disabled
- [ ] TLS 1.2 minimum
- [ ] `CORS_ORIGINS` set to your domain (not `*`)
- [ ] `EMAIL_PROVIDER` set to `gmail`, `smtp`, or `acs` (not `console`)
- [ ] Google OAuth redirect URI updated to production URL
- [ ] Stripe webhook URL updated to production URL
- [ ] Rate limit storage uses Redis in production (`RATELIMIT_STORAGE_URI=redis://...`)
- [ ] App logging enabled
- [ ] Health check passes
- [ ] Security headers present in response
