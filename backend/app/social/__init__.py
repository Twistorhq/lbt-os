"""TW-650 Twistor Social (RSN) — groups-first social product with a
verified-identity safety core (TW-646).

Phase 3 skeleton: fail-loud models, pure trust/geo/safety functions,
service stubs that raise until their build phase lands, and a router
whose endpoints answer an honest 501. Schema: supabase/
migration_verify_v1.sql + migration_social_v1.sql (44 tables, RLS
deny-all). Nothing in this package ever handles raw PII: identity
facts arrive as bands and booleans from the verify service.
"""
