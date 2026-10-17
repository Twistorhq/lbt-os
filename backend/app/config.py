
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    supabase_url: str
    supabase_service_key: str

    # Clerk
    clerk_secret_key: str
    clerk_publishable_key: str
    clerk_webhook_secret: str

    # LLM — supports OpenAI or Ollama
    # For Ollama: set llm_provider=ollama, ollama_model=llama3.2, leave openai_api_key empty
    # For OpenAI: set llm_provider=openai, openai_api_key=sk-...
    llm_provider: str = "ollama"          # "openai" | "ollama"
    openai_api_key: str = "not-needed"   # only required when llm_provider=openai
    openai_model: str = "gpt-4o-mini"    # used when llm_provider=openai, plan=pro
    openai_premium_model: str = "gpt-4o" # used for premium/enterprise plan
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"       # any model you have pulled

    # Stripe
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_price_basic: str = "legacy-grandfathered"   # grandfathered; not sold
    stripe_price_pro: str = "legacy-grandfathered"     # grandfathered; not sold
    stripe_price_premium: str = "legacy-grandfathered" # grandfathered; not sold
    stripe_price_trades_starter: str  # $299/shop/mo flat (TW-550)
    stripe_price_trades_pro: str      # $499/shop/mo flat (TW-550)

    # App
    app_env: str = "development"
    frontend_url: str = "http://localhost:5173"
    # Comma-separated production CORS origins. Defaults to FRONTEND_URL only.
    cors_origins: str = ""
    # Comma-separated hostnames accepted by TrustedHostMiddleware in production.
    trusted_hosts: str = ""
    # TW-484: owner SSO — comma-separated emails that bypass all plan gates.
    # The owner signs in once via Clerk; backend + frontend unlock everything.
    owner_emails: str = ""
    api_secret: str | None = None          # not currently used; remove weak default
    # Development/demo only: lets testers rerun free-tier audits without monthly gating.
    demo_allow_unlimited_audits: bool = False
    audit_rate_limit: str = "5/hour"
    # Comma-separated Clerk user IDs granted admin access, e.g. "user_abc,user_xyz"
    admin_user_ids: str = ""
    # Optional: Clerk JWT audience claim.  If set, token `aud` is verified against this value.
    # Find it in your Clerk dashboard → JWT Templates → your template → Audience field.
    # Leave empty to skip audience verification (safe for default Clerk JWT templates).
    clerk_jwt_audience: str | None = None
    # In production we fail fast unless JWT audience verification is configured explicitly.
    clerk_require_audience_in_production: bool = True

    # Email (Resend) — optional; emails are silently skipped if not set
    resend_api_key: str | None = None
    from_email: str = "LBT OS <noreply@lbt-os.com>"

    # SMS (Twilio) — optional; SMS is silently skipped if not set
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None  # e.g. +15555550100

    # Integration / OAuth
    integration_secret_key: str | None = None
    quickbooks_client_id: str | None = None
    quickbooks_client_secret: str | None = None
    quickbooks_redirect_uri: str | None = None
    hubspot_client_id: str | None = None
    hubspot_client_secret: str | None = None
    hubspot_redirect_uri: str | None = None

    @property
    def should_verify_clerk_audience(self) -> bool:
        return bool(self.clerk_jwt_audience)

    @property
    def should_bypass_audit_monthly_limit(self) -> bool:
        return self.app_env.lower() != "production" and self.demo_allow_unlimited_audits

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def parsed_cors_origins(self) -> list[str]:
        configured = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        origins = configured or [self.frontend_url]
        if not self.is_production:
            origins.extend([
                "http://localhost:5173",
                "http://localhost:5174",
                "http://localhost:5175",
                "http://localhost:5176",
                "http://localhost:5177",
                "http://127.0.0.1:5173",
                "http://127.0.0.1:5174",
                "http://127.0.0.1:5175",
                "http://127.0.0.1:5176",
                "http://127.0.0.1:5177",
            ])
        return list(dict.fromkeys(origins))

    @property
    def parsed_trusted_hosts(self) -> list[str]:
        configured = [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]
        if configured:
            return configured
        if self.is_production:
            raise ValueError("TRUSTED_HOSTS must be set when APP_ENV=production.")
        return ["localhost", "127.0.0.1", "*.localhost", "testserver"]

    def model_post_init(self, __context) -> None:
        if (
            self.app_env.lower() == "production"
            and self.clerk_require_audience_in_production
            and not self.clerk_jwt_audience
        ):
            raise ValueError(
                "CLERK_JWT_AUDIENCE must be set when APP_ENV=production. "
                "Set CLERK_REQUIRE_AUDIENCE_IN_PRODUCTION=false only if you have intentionally accepted that risk."
            )
        if self.app_env.lower() == "production":
            required_stripe = {
                "STRIPE_SECRET_KEY": self.stripe_secret_key,
                "STRIPE_WEBHOOK_SECRET": self.stripe_webhook_secret,
                "STRIPE_PRICE_TRADES_STARTER": self.stripe_price_trades_starter,
                "STRIPE_PRICE_TRADES_PRO": self.stripe_price_trades_pro,
            }
            missing = [name for name, value in required_stripe.items() if not value or "placeholder" in value.lower()]
            if missing:
                raise ValueError(f"Production Stripe configuration is incomplete: {', '.join(missing)}.")
            if not self.stripe_secret_key.startswith("sk_"):
                raise ValueError("STRIPE_SECRET_KEY must be a Stripe secret key in production.")
            if not self.stripe_webhook_secret.startswith("whsec_"):
                raise ValueError("STRIPE_WEBHOOK_SECRET must be a Stripe webhook signing secret in production.")
            bad_prices = [
                name for name in ("STRIPE_PRICE_TRADES_STARTER", "STRIPE_PRICE_TRADES_PRO")
                if not required_stripe[name].startswith("price_")
            ]
            if bad_prices:
                raise ValueError(f"Stripe price IDs must start with price_: {', '.join(bad_prices)}.")


settings = Settings()

# TW-550: flat per-shop pricing (the anti-ServiceTitan wedge). Legacy
# basic/pro/premium are grandfathered — existing subscriptions keep syncing
# via webhook metadata passthrough, but no new checkouts are sold on them.
PLAN_PRICE_MAP: dict[str, str] = {
    "trades_starter": settings.stripe_price_trades_starter,  # $299/shop/mo
    "trades_pro":     settings.stripe_price_trades_pro,      # $499/shop/mo
}

# Explicit technician limits. None = unlimited, declared — never an absent check.
PLAN_LIMITS: dict[str, dict] = {
    "trades_starter": {"max_techs": None},
    "trades_pro":     {"max_techs": None},
}

# Land motion (TW-549): 14-day trial, zero credit risk.
TRIAL_PERIOD_DAYS = 14

# Micro-toll rates in cents per event (TW-549 Twistor staple). The toll ledger
# owns this vocabulary; M2/M3 emit events, the ledger prices them.
# Percent-of-value events (e.g. booking.recovered at 10% of job value) are NOT
# in this map: the emitter computes the amount and passes amount_cents.
TOLL_RATES: dict[str, int] = {
    "missed_call.detected": 10,   # $0.10 per detected missed call
    "sms.auto_reply":         5,  # $0.05 per auto-SMS
    "call.ai_handled":       50,  # $0.50 per AI-handled call
}

# NOTE (TW-550): PLAN_FEATURES removed — it had no consumers and listed only
# legacy plans. Plan capabilities are expressed via PLAN_LIMITS (techs),
# PLAN_AUDIT_LIMITS (ai_audit), and require_plan() (route gates).
