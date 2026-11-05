"""TW-951 (TW-948-B2) bounded curated memory with receipts.

The Section 8.1 memory contract for customer Toris, built on
the B1 persistence foundation (backend/app/persistence/):

- two bounded collections (business facts; owner preferences
  and mandate notes) scoped (org, agent), hard limits enforced
  on the batch result of a write,
- writes staged through the B1 agt_staged_writes gate with the
  target entry pinned by keyed hash; approval refuses when the
  live entry changed underneath the pin,
- MemoryDefense-pattern screening (redact inline, block
  whole-secret content, block injection-shaped text) plus the
  B1 TW-552 masking pipeline, applied BEFORE staging and
  re-applied at approval as defense in depth,
- a snapshot assembled once at session start and frozen for
  the session,
- a keyed receipt id for every approved write, recorded in an
  append-only audit trail; a memory claim without a receipt
  does not count as saved,
- the approval gate relaxes per owner per Tori as a setting
  the owner controls; the agent has no path to the setting.

Named to echo the Twistor Core prototype (workforce memory
store, learning_memory MemoryDefense, approval_gates). It
imports none of it: patterns only, rebuilt on the lbt-os
persistence tables.
"""
