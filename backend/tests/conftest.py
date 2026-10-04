"""Pytest bootstrap for the lbt-os backend suite (TW-301).

Obviously-dummy environment values so test modules can import app code that
transitively instantiates app.config.Settings() at import time (TW-178
pattern). setdefault: real ambient secrets (CI/production) are never
clobbered. These tests never touch the network or a real service; the values
only need to satisfy pydantic validation.

Test-only. Zero production code affected.
"""

import os

_DUMMY_ENV_VARS = {
    "SUPABASE_URL": "https://test-only.invalid",
    "SUPABASE_SERVICE_KEY": "test-only-dummy",
    "CLERK_SECRET_KEY": "test-only-dummy",
    "CLERK_PUBLISHABLE_KEY": "test-only-dummy",
    "CLERK_WEBHOOK_SECRET": "test-only-dummy",
    "STRIPE_SECRET_KEY": "test-only-dummy",
    "STRIPE_WEBHOOK_SECRET": "test-only-dummy",
    "STRIPE_PRICE_BASIC": "test-only-dummy",
    "STRIPE_PRICE_PRO": "test-only-dummy",
    "STRIPE_PRICE_PREMIUM": "test-only-dummy",
}

for _key, _value in _DUMMY_ENV_VARS.items():
    os.environ.setdefault(_key, _value)
