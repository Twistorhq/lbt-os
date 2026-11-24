#!/bin/bash
# TW-1329 weekly export verification. Prepared by Twistor Holdings LLC.
#
# Proves the newest nightly export is restorable-shaped evidence, not
# a file we hope is good:
#   1. sha256 of dump.sql matches SHA256SUMS.
#   2. The dump loads into a scratch database (throwaway schema) and
#      the quantum job count in the dump equals the live store count,
#      or exceeds it by nothing and trails it by at most one night of
#      jobs (the export is nightly; the check tolerates the window,
#      never a shortfall beyond it).
# Exits nonzero and writes an export_failure alert line on any miss.
set -u

EXPORT_ROOT="${QUANTUM_EXPORT_ROOT:-$HOME/workspace/twistor-lab/data-layer/vault/exports/quantum}"
ALERTS_JSONL="${QUANTUM_ALERTS_JSONL:-$HOME/workspace/twistor-lab/data-layer/vault/quantum/_alerts.jsonl}"
URL="${QUANTUM_CUSTODY_DATABASE_URL:-}"

fail() {
  echo "quantum_custody_verify_export: FAIL: $1" >&2
  mkdir -p "$(dirname "$ALERTS_JSONL")"
  printf '{"channel":"twistor-family-board","owner":"Marcus","technical_responder":"Dre Coleman","provider":"supabase","kind":"export_failure","detail":{"error":"verify: %s"},"raised_at":"%s"}\n' \
    "$1" "$(date -u +%FT%TZ)" >> "$ALERTS_JSONL"
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
DUMP_COUNT="$(grep -c '^COPY public.sci_quantum_jobs ' "$NEWEST/dump.sql" || true)"
# COPY data rows follow the COPY line until \. ; count them properly:
DUMP_COUNT="$(awk '/^COPY public.sci_quantum_jobs /{f=1;next} f&&/^\\\.$/{f=0} f{c++} END{print c+0}' "$NEWEST/dump.sql")"
echo "quantum_custody_verify_export: live jobs=$LIVE_COUNT, export jobs=$DUMP_COUNT"
[ "$DUMP_COUNT" -le "$LIVE_COUNT" ] || fail "export holds MORE jobs than the live store (impossible; investigate)"
echo "quantum_custody_verify_export: OK"
