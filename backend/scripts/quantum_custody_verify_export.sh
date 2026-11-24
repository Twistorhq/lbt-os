#!/bin/bash
# TW-1329 weekly export verification. Prepared by Twistor Holdings LLC.
#
# Proves the newest nightly export is restorable-shaped evidence, not
# a file we hope is good:
#   1. sha256 of dump.sql matches SHA256SUMS.
#   2. The quantum job count in the dump is bracketed by two counts
#      read from the live store:
#        lower bound: jobs with created_at <= the export stamp.
#          pg_dump writes one consistent snapshot, taken seconds
#          after the export directory is stamped, and created_at is
#          stamped by the store at insert, so every job that existed
#          at export time must be inside the dump. A dump below this
#          bound is SHORT: rows are missing, it is not a restore
#          point, and this check exits nonzero.
#        upper bound: the live count now. The dump cannot hold jobs
#          the store does not.
#      Exact equality with the current live count is NOT the
#      invariant and would false-fail every week: the store keeps
#      growing after the nightly export, so a healthy dump trails
#      today's count by however many jobs landed since. What it may
#      never do is fall short of the count as of its own export.
# Exits nonzero and writes an export_failure alert line on any miss.
set -u

EXPORT_ROOT="${QUANTUM_EXPORT_ROOT:-$HOME/workspace/twistor-lab/data-layer/vault/exports/quantum}"
ALERTS_JSONL="${QUANTUM_ALERTS_JSONL:-$HOME/workspace/twistor-lab/data-layer/vault/quantum/_alerts.jsonl}"
URL="${QUANTUM_CUSTODY_DATABASE_URL:-}"

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

fail() {
  echo "quantum_custody_verify_export: FAIL: $1" >&2
  mkdir -p "$(dirname "$ALERTS_JSONL")"
  printf '{"channel":"twistor-family-board","owner":"Marcus","technical_responder":"Dre Coleman","provider":"supabase","kind":"export_failure","detail":{"error":"verify: %s"},"raised_at":"%s"}\n' \
    "$(json_escape "$1")" "$(date -u +%FT%TZ)" >> "$ALERTS_JSONL"
  exit 1
}

[ -n "$URL" ] || fail "QUANTUM_CUSTODY_DATABASE_URL is not set"
NEWEST="$(ls -d "$EXPORT_ROOT"/export-* 2>/dev/null | sort | tail -1)"
[ -n "$NEWEST" ] || fail "no exports found under $EXPORT_ROOT"
[ -s "$NEWEST/dump.sql" ] || fail "newest export dump is empty: $NEWEST"

(cd "$NEWEST" && sha256sum -c SHA256SUMS >/dev/null) || fail "sha256 mismatch in $NEWEST"
echo "quantum_custody_verify_export: hash OK ($NEWEST)"

LIVE_COUNT="$(psql "$URL" -Atc "SELECT count(*) FROM sci_quantum_jobs" 2>/dev/null)" \
  || fail "cannot read live store"
# Lower bound: jobs that already existed when this export was taken.
# The export directory stamp is set seconds before pg_dump starts, so
# every job with created_at <= the stamp was in the dump's snapshot.
STAMP_RAW="$(basename "$NEWEST")"
STAMP_ISO="$(printf '%s' "$STAMP_RAW" | sed -E 's/^export-([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z$/\1-\2-\3T\4:\5:\6Z/')"
[ "$STAMP_ISO" != "$STAMP_RAW" ] || fail "cannot parse export timestamp from $STAMP_RAW"
AS_OF_COUNT="$(psql "$URL" -Atc "SELECT count(*) FROM sci_quantum_jobs WHERE created_at <= '$STAMP_ISO'" 2>/dev/null)" \
  || fail "cannot read live store (as-of count)"
# COPY data rows follow the COPY line until \. ; count them:
DUMP_COUNT="$(awk '/^COPY public.sci_quantum_jobs /{f=1;next} f&&/^\\\.$/{f=0} f{c++} END{print c+0}' "$NEWEST/dump.sql")"
echo "quantum_custody_verify_export: live jobs=$LIVE_COUNT, jobs at export time=$AS_OF_COUNT, export jobs=$DUMP_COUNT"
[ "$DUMP_COUNT" -ge "$AS_OF_COUNT" ] || fail "export is SHORT: holds $DUMP_COUNT jobs but $AS_OF_COUNT existed at export time ($STAMP_ISO); the dump is not a restore point"
[ "$DUMP_COUNT" -le "$LIVE_COUNT" ] || fail "export holds MORE jobs than the live store (impossible; investigate)"
echo "quantum_custody_verify_export: OK"
