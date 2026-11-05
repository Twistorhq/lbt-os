"""TW-949 (TW-948-B1) Tori persistence foundation.

Thin scoped read and write modules over the agt_ persistence
tables (supabase/migration_persistence_tw949.sql): sessions and
messages with lineage, job definitions, durable fires,
continuity records, and staged writes. Every function is scoped
by (org, agent); the workspace key rides on every row.

Patterns only, no runtime imports: Core workforce 001 DDL shapes
and Core docsearch full-text design informed the schema; the
durable-turns journal discipline (write before answering) is the
flush contract callers follow. Nothing here imports Twistor Core.
"""
