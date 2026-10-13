# Prepared by Twistor Holdings LLC

# TW-488 evidence: lbt-os backend Railway → Fly.io free tier

## What
Railway trial expired; Justynn won't pay. Backend moves to Fly.io free tier
($0). Supabase stays (free tier, untouched). Frontend stays on GitHub Pages.

## Changes (branch dre/tw-488-flyio-migration)
- `backend/fly.toml` (new): app `lbt-os-backend`, region `den` (Denver),
  Docker build, `http_service` on port 8000 with `/health` check,
  1 shared CPU / 512MB, auto-stop/start for free-tier efficiency.
- `docs/deployment.md`: section 4 rewritten for Fly.io (launch, secrets,
  deploy); prereqs line updated; `VITE_API_URL` now points at
  `https://lbt-os-backend.fly.dev`.

## Secrets needed at deploy (flyctl secrets set)
SUPABASE_URL, SUPABASE_SERVICE_KEY, CLERK_SECRET_KEY, CLERK_PUBLISHABLE_KEY,
CLERK_WEBHOOK_SECRET, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
STRIPE_PRICE_BASIC/PRO/PREMIUM, FRONTEND_URL, CORS_ORIGINS,
OWNER_EMAILS="Twistorhq@gmail.com,Twistorhq@twistor.co,holla@twistor.co" (TW-484).

## Verification
- fly.toml: valid TOML, app + region parse (flyctl config validate needs
  auth — Justynn's login required, not available in sandbox).
- Dockerfile: unchanged, standard python:3.12-slim + uvicorn; Fly builds
  remotely (no Docker in sandbox to pre-build).
- `/health` endpoint confirmed present in backend/app/main.py.
- Backend tests: unchanged by this diff (config + docs only).

## Not done (needs Justynn)
- `flyctl auth login` — his Fly.io account.
- `flyctl launch --no-deploy` + `flyctl secrets set` + `flyctl deploy`.
- Frontend `VITE_API_URL` rebuild/redeploy after backend URL is final.
- End-to-end: health check, auth, owner SSO flag — verified post-deploy.
