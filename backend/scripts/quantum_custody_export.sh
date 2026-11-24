#!/bin/bash
# TW-1329 quantum custody export: the Supabase exit insurance.
# Prepared by Twistor Holdings LLC.
#
# Supabase is the quantum store of record AND still a provider. This
# script is the custody mechanic for the store itself: a nightly
# pg_dump of the quantum custody + science core tables, landing in
# Twistor-controlled storage OUTSIDE Supabase:
#
#   $QUANTUM_EXPORT_ROOT (default below)
#     = ~/workspace/twistor-lab/data-layer/vault/exports/quantum/
#
# Each export is a directory export-<UTC timestamp>/ holding dump.sql
# and SHA256SUMS. A failed dump exits nonzero and writes an
# export_failure alert line to the alerts JSONL the collector pages
# from. The weekly verification (quantum_custody_verify_export.sh)
# re-hashes the newest export and compares row counts against the
# live store, so an exit from Supabase is a restore, not a negotiation.
#
# Requires: QUANTUM_CUSTODY_DATABASE_URL (the Supabase Postgres
# connection string) in the environment. Fails closed without it.
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
  echo "quantum_custody_export: FAIL: $1" >&2
  mkdir -p "$(dirname "$ALERTS_JSONL")"
  printf '{"channel":"twistor-family-board","owner":"Marcus","technical_responder":"Dre Coleman","provider":"supabase","kind":"export_failure","detail":{"error":"%s"},"raised_at":"%s"}\n' \
    "$(json_escape "$1")" "$(date -u +%FT%TZ)" >> "$ALERTS_JSONL"
  exit 1
}

[ -n "$URL" ] || fail "QUANTUM_CUSTODY_DATABASE_URL is not set (Supabase Postgres URL required)"
command -v pg_dump >/dev/null 2>&1 || fail "pg_dump not on PATH"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$EXPORT_ROOT/export-$STAMP"
mkdir -p "$DEST" || fail "cannot create $DEST"

# Quantum custody tables plus the science core tables they reference.
pg_dump "$URL" \
  --no-owner --no-privileges --format=plain \
  --table=sci_quantum_jobs \
  --table=sci_quantum_backend_snapshots \
  --table=sci_quantum_alerts \
  --table=sci_quantum_observations \
  --table=sci_quantum_spec_snapshots \
  --table=sci_bronze_artifacts \
  --file="$DEST/dump.sql" || fail "pg_dump failed"

[ -s "$DEST/dump.sql" ] || fail "pg_dump produced an empty file"
(cd "$DEST" && sha256sum dump.sql > SHA256SUMS) || fail "hashing failed"
echo "quantum_custody_export: OK $DEST ($(wc -c < "$DEST/dump.sql") bytes, sha256 in SHA256SUMS)"
