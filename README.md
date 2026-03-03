# GenBI — AI-Powered Analytics Dashboard

Upload any CSV or Excel dataset and instantly get smart charts, cross-filtering, drill-down analysis, and AI-driven Key Influencers.

## Quick Start

```bash
# 1. Clone and install dependencies
pip install -r requirements.txt

# 2. Set up environment variables
cp .env.example .env
# Edit .env with your DATABASE_URL, JWT_SECRET_KEY, FLASK_SECRET_KEY

# 3. Create the PostgreSQL database
createdb genbi_auth

# 4. Run database migrations
flask db upgrade

# 5. Seed billing plans (optional)
python3 seed_plans.py

# 6. Start the server
python3 server.py
# → http://localhost:8000
```

## Routes

| Route | Auth | Description |
|-------|------|-------------|
| `/` | Public | Animated homepage with product info |
| `/login` | Public | Login page |
| `/signup` | Public | Signup page |
| `/dashboard` | Protected | Analytics & dataset upload dashboard |
| `/forgot-password` | Public | Password reset request |
| `/reset-password` | Public | Password reset form |
| `/pricing` | Public | Pricing plans |
| `/billing/success` | Public | Post-Checkout success page |
| `/billing/cancel` | Public | Checkout canceled page |

## Billing API

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/billing/plans` | No | List plans with entitlements |
| `GET` | `/api/billing/subscription` | Yes | Current subscription + usage |
| `POST` | `/api/billing/switch-plan` | Yes | Switch plan (Checkout/Portal/cancel) |
| `POST` | `/api/billing/checkout` | Yes | Legacy checkout (delegates to switch-plan) |
| `POST` | `/api/billing/cancel` | Yes | Cancel subscription at period end |
| `POST` | `/api/billing/resume` | Yes | Resume canceled subscription |
| `GET` | `/api/billing/portal` | Yes | Stripe customer portal URL |
| `GET` | `/api/billing/usage` | Yes | Detailed usage for current period |
| `POST` | `/api/billing/webhook` | No | Stripe webhook (signature verified) |

## Stripe Setup (Local Testing)

```bash
# 1. Install Stripe CLI: https://stripe.com/docs/stripe-cli
# 2. Create products + prices in Stripe Dashboard
# 3. Set env vars in .env:
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID_PRO=price_...
STRIPE_PRICE_ID_BUSINESS=price_...

# 4. Forward webhooks locally:
stripe listen --forward-to localhost:8000/api/billing/webhook

# 5. Test flows:
#   Free → Pro: "Upgrade to Pro" → Stripe Checkout → pay → /billing/success → dashboard
#   Pro ↔ Business: "Switch to Business" → Stripe Billing Portal → confirm → dashboard
#   Paid → Free: "Downgrade to Free" → cancel_at_period_end → keeps access until period end
```

Without Stripe keys, billing runs in **mock mode** (subscriptions activate instantly, no payment required).

## Tech Stack

- **Backend**: Flask, PostgreSQL, SQLAlchemy, Flask-Migrate
- **Auth**: JWT (PyJWT), bcrypt password hashing
- **AI**: Google Gemini API (chart assistant, key influencers)
- **Billing**: Stripe Checkout + Billing Portal (optional, mock mode available)
- **Frontend**: Vanilla JS SPA, Canvas-based charts, Inter font
