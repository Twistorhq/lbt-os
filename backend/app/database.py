from supabase import Client, create_client

from .config import settings

# Single shared Supabase client (service role — bypasses RLS, safe on server)
# All queries MUST manually filter by org_id for tenant isolation.
_supabase: Client | None = None


def get_db() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(settings.supabase_url, settings.supabase_service_key)
    return _supabase


def is_unique_violation(exc: Exception) -> bool:
    """True for a Postgres UNIQUE violation (SQLSTATE 23505).

    PostgREST surfaces 23505 on the exception's ``code`` attribute
    and/or in its text as ``duplicate key``; check both. Same rule the
    persistence/telemetry layers match on (TW-952, TW-1323, TW-1327).
    """
    if getattr(exc, "code", None) == "23505":
        return True
    text = str(exc).lower()
    return (
        "23505" in text
        or "duplicate key" in text
        or "unique constraint" in text
        or "unique violation" in text
    )
