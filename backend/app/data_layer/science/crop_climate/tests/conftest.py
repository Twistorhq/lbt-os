"""Pytest bootstrap for the TW-593 crop-climate lane tests (TW-596).

Dummy env values so importing app code that instantiates
app.config.Settings() at import time works in tests. setdefault: real
ambient secrets are never clobbered. Lane tests never touch the network
or a real database.
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
    "STRIPE_PRICE_TRADES_STARTER": "price_test_starter",
    "STRIPE_PRICE_TRADES_PRO": "price_test_pro",
}

for _key, _value in _DUMMY_ENV_VARS.items():
    os.environ.setdefault(_key, _value)
