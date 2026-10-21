"""TW-650 Twistor Social (RSN): groups-first social product with a
verified-identity safety core (TW-646).

Phase 3 skeleton landed the fail-loud models, pure trust/geo/safety
functions, and a router whose endpoints answered an honest 501.
TW-660 implemented the Phase 4 core loop on top (groups, events,
series, RSVPs, location reveal, gold listing reads); check-ins and
reports still answer 501 until their phases land. Schema: supabase/
migration_verify_v1.sql + migration_social_v1.sql (44 tables, RLS
deny-all). Nothing in this package ever handles raw PII: identity
facts arrive as bands and booleans from the verify service.
"""
