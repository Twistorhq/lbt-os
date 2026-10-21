# LBT OS — Deployment Guide

## Prerequisites

- Supabase project (free tier works for MVP)
- Clerk account + application
- Stripe account (test mode first)
- OpenAI API key
- Fly.io account (backend, free tier)
- Vercel / Netlify account (frontend)

---

## 1. Database Setup (Supabase)

```bash
# In Supabase SQL Editor, run in order:
1. supabase/schema.sql
2. supabase/rls_policies.sql
```

**Key setting**: In Supabase → Settings → API → copy your:
- Project URL → `SUPABASE_URL`
- `service_role` key (secret) → `SUPABASE_SERVICE_KEY`

---

## 2. Clerk Setup

1. Create a new Clerk application
2. Enable **Organizations** in Clerk Dashboard → Organizations
3. Copy publishable key → `VITE_CLERK_PUBLISHABLE_KEY`
4. Copy secret key → `CLERK_SECRET_KEY`
5. Create a webhook pointing to `https://your-api.com/api/v1/billing/webhook`
   - Events: `organization.created`, `organization.deleted`
   - Copy signing secret → `CLERK_WEBHOOK_SECRET`

---

## 3. Stripe Setup

1. Create products and prices in Stripe Dashboard:
   - Basic: $29/month recurring
   - Pro: $79/month recurring
   - Premium: $149/month recurring
2. Copy price IDs → `STRIPE_PRICE_BASIC`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_PREMIUM`
3. Copy secret key → `STRIPE_SECRET_KEY`
4. Create webhook endpoint: `https://your-api.com/api/v1/billing/webhook`
   - Events: `customer.subscription.*`, `checkout.session.completed`
5. Copy signing secret → `STRIPE_WEBHOOK_SECRET`

---

## 4. Backend Deployment (Fly.io free tier — $0)

```bash
# 1. Install flyctl and log in (one-time, Justynn's account)
# 2. From the repo:
cd lbt-os/backend
flyctl launch --no-deploy   # accepts backend/fly.toml as-is
# 3. Set secrets (never in the repo):
flyctl secrets set \
  SUPABASE_URL=... \
  SUPABASE_SERVICE_KEY=... \
  CLERK_SECRET_KEY=... \
  CLERK_PUBLISHABLE_KEY=... \
  CLERK_WEBHOOK_SECRET=... \
  CLERK_JWT_AUDIENCE=... \
  STRIPE_SECRET_KEY=... \
  STRIPE_WEBHOOK_SECRET=... \
  STRIPE_PRICE_BASIC=... \
  STRIPE_PRICE_PRO=... \
  STRIPE_PRICE_PREMIUM=... \
  OWNER_EMAILS="Twistorhq@gmail.com,Twistorhq@twistor.co,holla@twistor.co" \
  FRONTEND_URL=https://twistorhq.github.io/lbt-os \
  CORS_ORIGINS=https://twistorhq.github.io \
  TRUSTED_HOSTS=lbt-os-backend.fly.dev
# 4. Deploy:
flyctl deploy
```
> **Note:** `llm_provider` defaults to `ollama` (localhost:11434) — no Ollama
> runs on Fly, so `ai_audit` (Pro/Premium tiers) will degrade unless you set
> `LLM_PROVIDER=openai` + `OPENAI_API_KEY` as secrets.


The app serves at `https://lbt-os-backend.fly.dev` (or your chosen name).
Free tier: 1 shared CPU / 512MB, sleeps when idle, wakes on request —
health check on `/health`. Supabase stays as the database (free tier).

---

## 5. Frontend Deployment (Vercel recommended)

```bash
# 1. Push to GitHub
# 2. Import project in Vercel
# 3. Set:
#    Root Directory: lbt-os/frontend
#    Build Command: npm run build
#    Output Directory: dist

# 4. Add environment variables:
VITE_CLERK_PUBLISHABLE_KEY=pk_live_...
VITE_API_URL=https://lbt-os-backend.fly.dev
```

**Or local:**
```bash
cd lbt-os/frontend
cp .env.example .env    # fill in values
npm install
npm run dev
```

---

## 6. Local Development (Full Stack)

**Terminal 1 — Backend:**
```bash
cd lbt-os/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in values
uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — Frontend:**
```bash
cd lbt-os/frontend
npm install
cp .env.example .env
npm run dev
```

API docs available at: `http://localhost:8000/docs` (dev mode only)

---

## 7. Post-Deployment Checklist

- [ ] Supabase schema applied
- [ ] RLS policies applied
- [ ] Clerk org ID syncing to organizations table (via first user onboarding)
- [ ] Stripe test checkout works end-to-end
- [ ] Stripe webhook events received and subscriptions syncing
- [ ] AI audit returns insights (test with 30 days of sample data)
- [ ] CORS origin set correctly in backend config
- [ ] `APP_ENV=production` set (disables /docs endpoint)
