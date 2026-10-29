"""TW-768 Twistor Trades agent permission ledger (TW-764 Spec 2).

Per-org consent and permission ledger for the shop's agent: grants
and denials per action class, an execution-time check that fails
closed, and revocation (class / channel / all) that halts queued
work, fences in-flight durable turns, cuts access tokens, and
schedules retained working copies for purge — with a
system-generated receipt in the audit feed.
"""
