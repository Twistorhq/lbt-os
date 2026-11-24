#!/bin/bash
# TW-1329 quantum custody cycle runner. Prepared by Twistor Holdings LLC.
# Mirrors the TW-839 run-cycle.sh convention: batch invocations, no
# resident daemon, everything logged to the vault cycle log.
#
# Scheduler wiring (see the quantum custody runbook):
#   every 15 minutes:  this script with no argument
#                      -> reconciliation sweep for each live provider
#   daily:             this script with "daily"
#                      -> telemetry snapshots + provider-side diff +
#                         nightly Supabase export
#   weekly:            quantum_custody_verify_export.sh
#
# Exit codes propagate: 3 from the collector means an auth alert was
# paged (the alert is also a row in sci_quantum_alerts, a line in
# _alerts.jsonl, and a post on the Twistor family board; owner Marcus).
set -u
# Code path is the lbt-os main checkout once TW-1329 merges (the TW-839
# runner convention). Until the merge, set QUANTUM_BACKEND_DIR to the
# build worktree: ~/workspace/worktrees/dre-tw1329-custody/backend
BACKEND="${QUANTUM_BACKEND_DIR:-$HOME/workspace/repos/lbt-os/backend}"
PY="$BACKEND/.venv/bin/python"
ROOT="${QUANTUM_BRONZE_ROOT:-$HOME/workspace/twistor-lab/data-layer/vault}"
CUSTODY_DB="${QUANTUM_CUSTODY_DB:-$ROOT/quantum-custody.db}"
LOG="$ROOT/quantum-cycle.log"
MODE="${1:-sweep}"
cd "$BACKEND" || exit 1
{
  echo "=== custody cycle $(date -u +%FT%TZ) mode=$MODE ==="
  # Published-source telemetry snapshot runs in BOTH modes: it is the
  # credential-free collection start and costs nothing to refresh.
  PYTHONPATH="$BACKEND" "$PY" -m app.data_layer.science.quantum.cli \
    custody-snapshot --root "$ROOT" --custody-db "$CUSTODY_DB" \
    --provider published
  echo "snapshot rc=$?"
  if [ -n "${QUANTUM_JOB_PROVIDERS:-}" ]; then
    for provider in $QUANTUM_JOB_PROVIDERS; do
      PYTHONPATH="$BACKEND" "$PY" -m app.data_layer.science.quantum.cli \
        custody-reconcile --root "$ROOT" --custody-db "$CUSTODY_DB" \
        --provider "$provider"
      echo "reconcile $provider rc=$?"
      if [ "$MODE" = "daily" ]; then
        PYTHONPATH="$BACKEND" "$PY" -m app.data_layer.science.quantum.cli \
          custody-diff --root "$ROOT" --custody-db "$CUSTODY_DB" \
          --provider "$provider"
        echo "diff $provider rc=$?"
      fi
    done
  fi
  if [ "$MODE" = "daily" ] && [ -n "${QUANTUM_CUSTODY_DATABASE_URL:-}" ]; then
    "$BACKEND/scripts/quantum_custody_export.sh"
    echo "export rc=$?"
  fi
} >> "$LOG" 2>&1
