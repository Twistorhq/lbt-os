<!-- Prepared by Twistor Holdings LLC -->

# TW-651 Evidence: Twistor Social events data layer ingest

Author: Dre Coleman, Senior Backend Engineer and DBA
Branch: `dre/tw-651-events-ingest` off lbt-os main at `9707fc6`
Commits: RED `75af7e9` (tests only) then GREEN (this build)

Source contracts: TW-646 architecture spec Section 10; merged
OpenSpec package (tasks.md items 28 and 29); Diego's Denver events
source map (2026-10-20). API client and organizer OAuth import
(tasks.md item 29's remaining pipes) are not in this ticket's scope:
Eventbrite needs its approved developer app and organizer import is
the claim lane (item 31). Their source kinds exist in the schema and
their connectors slot into the same registry when approvals land.

## What was built

1. **evt_ schema** (`supabase/migration_events_v1.sql`, runs after
   migration_social_v1.sql): evt_sources, evt_raw_items (bronze),
   evt_venues, evt_listings, evt_listing_sources (silver),
   evt_claims, evt_ingest_runs, evt_dead_letters, evt_publish_log,
   evt_gold_listings (gold). RLS enabled on all 10; deny-all
   policies on 9; gold carries the spec 10.3 exception, one
   SELECT-only policy for the dedicated role evt_public_read.
2. **Generic ICS importer** (`events_index/ics.py`): unfolding,
   TZID and UTC and floating and all-day forms, DURATION, recurrence
   expansion on python-dateutil rrule inside a rolling 90-day window
   with EXDATE support and DTSTART always counted, STATUS:CANCELLED
   carried, GEO and LOCATION facts, per-VEVENT splitting so the
   pipeline isolates poison items.
3. **Localist connector** (`localist.py`): public JSON API shape,
   per-instance extraction, cancelled status carried, public filter
   dropping academic-deadline noise per the source map.
4. **Tribe Events connector** (`tribe.py`): published events only,
   UTC date fields preferred with site-timezone fallback, nested
   venue and organizer facts, HTML entities unescaped in titles.
5. **Fingerprint dedupe** (`normalize.py`): SHA-256 over normalized
   title, venue fingerprint (coordinates rounded to 4 decimal
   places, or the address fingerprint while coordinates are
   pending), and the start bucketed to 5 minutes. A collision merges
   provenance (one evt_listing_sources row per carrying source);
   display facts come from the most authoritative pipe present
   (organizer import over API over ICS).
6. **Freshness and cancellation** (`pipeline.py`): unchanged
   payload hash skips reprocessing but still records the sighting;
   explicit cancellations cancel in place and tombstone the
   fingerprint for 90 days (the listing row itself is the
   tombstone, fingerprint is UNIQUE); a full-sync item missing
   across two consecutive syncs cancels unless another source still
   carries the event; the publish step ages active listings to
   stale past the gate ceiling.
7. **Fail-closed provenance gate** (`gate.py`): the four spec
   conditions and nothing else; ratings are not a gate input
   (unrated fails closed for members under Section 5.9, not here).
   Every publish decision writes an evt_publish_log line with its
   reason. Never-store is enforced in three places: no such column
   exists in the DDL, the extraction shapes have no such field, and
   `redact.py` strips description, photo, image, excerpt, price,
   and contact keys from JSON items and DESCRIPTION and image
   properties from ICS items before hashing or bronze landing.
8. **Self-healing** on the platform's TW-208 primitives
   (`app.self_healing`): fetch retries with backoff, per-item fault
   isolation at both parse and merge, dead letters persisted to
   evt_dead_letters with the in-memory DLQ summary on the run row,
   and a per-source circuit breaker (3 consecutive failures opens
   the circuit for 30 minutes, persisted on evt_sources).
   Geocoding (`geocode.py`) is cache-first behind a provider
   interface with a rate-limited Nominatim implementation; failures
   never block a listing.

## Tests

81 new tests, all passing. Per file: migration contract 16,
normalize 12, ICS 13, connectors 8, gate 10, geocode 7, pipeline 15.

Full backend suite on this branch: 973 passed, 21 failed, 12
collection errors. Clean main at 9707fc6 measured the same day: 892
passed, 21 failed, 12 collection errors. The failed and error node
sets are identical between the two runs (verified by diff), so the
delta is exactly the 81 new tests and there are zero regressions.
The standing failure set is bi_whitelabel 8, missed_call 11,
pilot_blockers 1, usage_metering 1, plus the known xmlsec/lxml
collection defect. Ruff check and format are clean on every new
file.

RED honesty: the RED commit is tests only. Three test adjustments
landed with GREEN and are disclosed here: the write-policy test now
encodes the correct invariant (a FOR ALL USING (false) policy is a
deny, matching the TW-650 pattern; the original draft banned the
pattern itself); the tombstone test was rebuilt on a daily
recurrence because the first draft's event fell outside the
90-day window before its tombstone could expire; the ICS location
test caught a real parser bug (escaped commas are literal text in
ICS, so unescaping precedes the formatting split).

## Live execution

Both migrations in the dependency position were not re-run; the new
file was executed verbatim, fresh and a second time, against a
throwaway PostgreSQL 16.2 cluster (pgserver wheel in a scratch
venv, stub idn_users and rsn_groups as the only prerequisites).
Both runs clean, proving idempotency by execution. Live catalog:
10 evt_ tables, 10 RLS-enabled, 10 policies (9 deny-all with qual
false, 1 gold SELECT with qual true for evt_public_read), role
present. Behavioral probes, 10 of 10 as designed: duplicate
fingerprint rejected; half-coordinate venue rejected by CHECK;
bogus terms posture rejected; bogus age band rejected; the read
role sees the gold row; the read role sees 0 silver rows even with
a table grant (deny-all); the read role cannot insert into gold; a
granted non-owner role's silver insert is rejected by RLS; bronze
accepts a NULL payload and rejects a NULL payload hash. Harness
disclosure: the pgserver tree ships no pgcrypto, and the
migration's only pgcrypto-adjacent call is gen_random_uuid()
(server core since PostgreSQL 13), so a no-op pgcrypto extension
shim was registered in the scratch tree only. No repository file
was touched for the harness; Supabase ships real pgcrypto.
Harness scratch was deleted after the run.

Live smoke (optional, not in the suite): the LibCal ICS endpoint
pattern answers but reports an invalid calendar id for the generic
parameter, so the real per-calendar feed id remains an onboarding
step, exactly as the source map frames LibCal onboarding. The CU
Denver Localist API path returned 404 from this sandbox. Both
connectors are therefore fixture-verified only; the fixtures encode
the documented payload shapes.

## Design calls for Rosa

1. **Gold RLS shape.** Spec 10.3 names gold as the one public evt_
   surface served through a read-only role, so gold gets a
   SELECT-only policy for evt_public_read instead of the deny-all
   used everywhere else. The role is created NOLOGIN by the
   migration; ops grants login. No write policy exists anywhere.
2. **Ops tables beyond the spec's five.** evt_ingest_runs,
   evt_dead_letters, and evt_publish_log are additive: the spec's
   gate already requires a publish-log line per withheld listing,
   and the self-healing rule requires a persisted DLQ the standup
   can read. evt_claims ships now per tasks.md item 28; its flow is
   item 31.
3. **Fingerprint storage.** No separate fingerprint table: the
   fingerprint is UNIQUE on evt_listings and the cancelled row,
   held for its 90-day tombstone_until, is the tombstone record.
4. **Bronze redaction before landing.** Spec 10.3 calls bronze the
   raw payload exactly as fetched, retained where posture allows.
   The never-store list is the brighter line, so connectors strip
   expression fields before hashing and landing; bronze is the
   fetched item minus those fields. Payload hashes are computed
   over the redacted form, so change detection is unaffected.
5. **ICS organizer refs are never extracted.** An ICS ORGANIZER is
   an email address in practice; source_organizer_ref stays NULL
   for ICS and carries names and ids from API sources only.
6. **Source operational columns.** evt_sources carries feed_url,
   connector, config, and circuit state beyond the spec's listed
   fields; connectors are configuration, not code (source map:
   Tribe sites onboard one config entry at a time). Full-sync
   behavior is a per-source config flag because Localist queries a
   window while an ICS feed is the whole calendar.
7. **No sources seeded.** Launch source rows are operator
   configuration created at onboarding; seeding invented feed URLs
   into a migration would be worse than an empty table. DPL's
   per-calendar feed id (see smoke note) is the first onboarding
   input.
8. **Hand-rolled ICS surface, dateutil recurrence.** The repo has
   no icalendar dependency; parsing is a bounded module and
   recurrence rides python-dateutil, already a platform dependency.
   RECURRENCE-ID override blocks parse as standalone items and
   merge by fingerprint; full override semantics are a documented
   limitation, not silent.
9. **Fingerprint-change collision.** If an edited event's new
   fingerprint collides with a different listing, the fingerprint
   match wins and gains the provenance row; the vacated listing
   ages out through the gate. Deliberate, and noted here rather
   than hidden.
10. **SupabaseStore timestamp assembly.** The production store
    passes Supabase timestamp strings through the same dataclasses
    the pipeline compares; MemoryStore is the tested path and the
    Supabase store mirrors it method for method. First live wiring
    should parse timestamps at assembly (noted for the ingest
    runner ticket, not silently assumed here).

## Not in scope (by ticket)

Public index surface and gold read API (tasks.md item 30, Sofia's
TW-652 lane consumes gold), claim-a-listing flow (item 31),
Eventbrite API client (needs the approved developer app), organizer
OAuth import (item 31's pipe), the ingest scheduler wiring (the
pipeline is invoked per source with an injected fetcher; cron
wiring rides the launch phase), and age rating of indexed listings
(ratings arrive per Section 5.9 and at claim time).
