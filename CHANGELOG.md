# Changelog

## 2026-02-19 — Stripe Billing: Switch Plan → Payment Gateway → Subscription Updated

### Added
- **Unified `POST /api/billing/switch-plan` endpoint** handling all plan transitions:
  - Free → Pro/Business: redirects to Stripe Checkout (`mode=subscription`)
  - Pro ↔ Business: redirects to Stripe Billing Portal (handles proration + plan changes)
  - Paid → Free: cancels subscription at period end (access until `current_period_end`)
- **`/billing/success` page route** — post-Checkout landing page with subscription polling
  - Polls `GET /api/billing/subscription` every 2s (up to 30s) until status = `active`
  - Auto-redirects to `/dashboard` once confirmed
- **`/billing/cancel` page route** — user-friendly Checkout-canceled page with retry option
- **Webhook handling for plan changes via Portal** — `customer.subscription.updated` now detects
  price ID changes and updates `plan_id` in the database
- **`invoice.paid` / `invoice.payment_failed` webhook handlers** for payment status sync
- **`canceled_at` column** on `subscriptions` table (nullable timestamp)
- **`get_plan_by_stripe_price()`** — look up plans by Stripe price ID for webhook processing
- **Stripe customer reuse** — returning users skip re-entering payment details
- **Alembic migration** `a3718de04a54` — adds `canceled_at`, normalizes constraint names
- **Billing spinner CSS** for success overlay loading state

### Changed
- Pricing cards now show "Downgrade to Free" button for paid users (previously no action)
- Frontend `switchPlan()` replaces `billingCheckout()` as the primary plan-change function
- `initApp()` now handles `/billing/success`, `/billing/cancel`, and `?billing=` query params
- `STRIPE_SUCCESS_URL` default → `/billing/success?session_id={CHECKOUT_SESSION_ID}`
- `STRIPE_CANCEL_URL` default → `/billing/cancel`
- Billing Portal return URL → `/dashboard?billing=updated`
- Rate limiter applied to `switch-plan` endpoint (10/minute)

### Schema
```sql
ALTER TABLE subscriptions ADD COLUMN canceled_at TIMESTAMPTZ;
```

### New Routes
| Route | Auth | Description |
|-------|------|-------------|
| `POST /api/billing/switch-plan` | Protected | Unified plan switch (Checkout/Portal/cancel) |
| `GET /billing/success` | Public | Post-Checkout success page |
| `GET /billing/cancel` | Public | Checkout canceled page |

### Stripe Setup (Local Testing)
```bash
# 1. Create products + prices in Stripe Dashboard (or via CLI)
# 2. Set env vars:
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID_PRO=price_...
STRIPE_PRICE_ID_BUSINESS=price_...

# 3. Forward webhooks locally:
stripe listen --forward-to localhost:8000/api/billing/webhook

# 4. Test:
#   Free → Pro: click "Upgrade to Pro" → Stripe Checkout → pay → /billing/success → polls → /dashboard
#   Pro → Business: click "Switch to Business" → Stripe Billing Portal → confirm → returns to /dashboard
#   Paid → Free: click "Downgrade to Free" → cancel_at_period_end toggled
```

---

## 2026-02-18 — User Flow & Animated Homepage

### Added
- **Animated marketing homepage** at `/` with:
  - Fixed top navigation bar (brand left, Login/Sign up right)
  - Hero section with fade-up text animations, particle network canvas, gradient glow
  - "How it works" 4-step workflow section
  - 6 feature cards with staggered animation and per-card accent colors on hover
  - Footer with brand and links
  - `prefers-reduced-motion` support (disables all animations)
- **Route protection**: `/dashboard` requires authentication (JWT in localStorage)
- **Redirect rules**:
  - Unauthenticated user visits `/dashboard` → redirected to `/login?next=/dashboard`
  - Authenticated user visits `/` → redirected to `/dashboard`
  - Authenticated user visits `/login` or `/signup` → redirected to `/dashboard`
  - Successful login/signup → redirects to `/dashboard`

### Changed
- Protected dashboard route changed from `/app` to `/dashboard`
- Login/signup templates now redirect to `/dashboard` (previously `/app`)
- Homepage at `/` now shows full animated marketing page instead of minimal gate

### Routes
| Route | Auth | Description |
|-------|------|-------------|
| `GET /` | Public | Animated homepage |
| `GET /login` | Public | Login page |
| `GET /signup` | Public | Signup page |
| `GET /dashboard` | Protected | Analytics & upload dashboard |
| `POST /api/auth/signup` | Public | Create account |
| `POST /api/auth/login` | Public | Login |
| `POST /api/auth/logout` | Public | Logout (client clears JWT) |
| `GET /api/auth/me` | Protected | Current user info |
