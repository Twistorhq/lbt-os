"""Verify store: the engine's persistence boundary (TW-658).

One protocol, two implementations. InMemoryVerifyStore is a full
fidelity double shaped exactly like the vfy_ tables (TW-650, plus
the TW-658 v3 column below): the pipeline's persistence effects are
unit-tested against it, and the Supabase implementation is driven
through a fake client in the store tests so the double is not the
only witness. SupabaseVerifyStore is the production implementation
over the service-role client; every vfy_ table is RLS deny-all to
anything else. The store never accepts a raw image: documents
arrive as hashes plus an encrypted payload, biometrics as
ciphertext.

The session's document class is a vfy_sessions column
(migration_verify_v3.sql): both implementations write it at
creation and return it on the session row, so it survives a
PostgREST round trip. The durable per-document record of the class
remains vfy_documents.document_type, written at submission. (The
original build echoed the class into the created dict only; Rosa's
TW-658 M3 showed the echo could never survive get_session, and
the fix round replaced design call 3 with the column.)
"""

from __future__ import annotations

import uuid
from typing import Protocol

from .velocity import AttemptRecord


class VerifyStore(Protocol):
    def ensure_engine_version(
        self, version: str, pipeline_config: dict, model_refs: dict
    ) -> str: ...

    def create_session(
        self,
        *,
        user_id: str,
        document_type: str,
        tier_requested: int,
        engine_version_id: str,
    ) -> dict: ...

    def get_session(self, session_id: str) -> dict | None: ...

    def update_session(self, session_id: str, **fields) -> None: ...

    def create_consent(self, row: dict) -> dict: ...

    def list_consents(self, user_id: str) -> list[dict]: ...

    def revoke_consent(self, consent_id: str, at) -> None: ...

    def save_document(self, row: dict) -> dict: ...

    def get_document_for_session(self, session_id: str) -> dict | None: ...

    def update_document(self, document_id: str, **fields) -> None: ...

    def save_biometric(self, row: dict) -> dict: ...

    def list_live_biometrics(self, user_id: str | None = None) -> list[dict]: ...

    def add_signal(self, row: dict) -> None: ...

    def get_anchor(self, anchor_hash: str) -> dict | None: ...

    def upsert_anchor(
        self, anchor_hash: str, *, user_id: str, is_banned: bool = False
    ) -> dict: ...

    def save_age_attestation(self, row: dict) -> None: ...

    def get_age_attestation(self, anchor_hash: str) -> dict | None: ...

    def set_verification_state(self, user_id: str, **fields) -> None: ...

    def get_verification_state(self, user_id: str) -> dict | None: ...

    def add_retention_action(self, row: dict) -> dict: ...

    def record_attempt(self, session_id: str, record: AttemptRecord) -> None: ...

    def list_attempts(self) -> list[AttemptRecord]: ...


class InMemoryVerifyStore:
    """Full-fidelity in-memory vfy_ store for tests and staging."""

    def __init__(self):
        self.engine_versions: list[dict] = []
        self.sessions: dict[str, dict] = {}
        self.consents: list[dict] = []
        self.documents: list[dict] = []
        self.biometrics: list[dict] = []
        self.signals: list[dict] = []
        self.anchors: dict[str, dict] = {}
        self.age_attestations: list[dict] = []
        self.verification_states: dict[str, dict] = {}
        self.retention_actions: list[dict] = []
        self.attempts: list[AttemptRecord] = []

    # -- engine versions -------------------------------------------------
    def ensure_engine_version(self, version, pipeline_config, model_refs):
        for row in self.engine_versions:
            if row["version"] == version:
                return row["id"]
        row = {
            "id": uuid.uuid4().hex,
            "version": version,
            "pipeline_config": pipeline_config,
            "model_refs": model_refs,
            "is_active": True,
        }
        for other in self.engine_versions:
            other["is_active"] = False
        self.engine_versions.append(row)
        return row["id"]

    # -- sessions ----------------------------------------------------------
    def create_session(
        self, *, user_id, document_type, tier_requested, engine_version_id
    ):
        row = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "document_type": document_type,
            "tier_requested": tier_requested,
            "status": "started",
            "engine_version_id": engine_version_id,
            "decision_by": None,
            "confidence_band": None,
            "decided_at": None,
        }
        self.sessions[row["id"]] = row
        return row

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def update_session(self, session_id, **fields):
        self.sessions[session_id].update(fields)

    # -- consents ----------------------------------------------------------
    def create_consent(self, row):
        row = {"id": uuid.uuid4().hex, "revoked_at": None, **row}
        self.consents.append(row)
        return row

    def list_consents(self, user_id):
        return [c for c in self.consents if c["user_id"] == user_id]

    def revoke_consent(self, consent_id, at):
        for consent in self.consents:
            if consent["id"] == consent_id:
                consent["revoked_at"] = at

    # -- documents -----------------------------------------------------------
    def save_document(self, row):
        row = {"id": uuid.uuid4().hex, **row}
        self.documents.append(row)
        return row

    def get_document_for_session(self, session_id):
        for doc in self.documents:
            if doc["session_id"] == session_id:
                return doc
        return None

    def update_document(self, document_id, **fields):
        for doc in self.documents:
            if doc["id"] == document_id:
                doc.update(fields)

    # -- biometrics ----------------------------------------------------------
    def save_biometric(self, row):
        row = {"id": uuid.uuid4().hex, "destroyed_at": None, **row}
        self.biometrics.append(row)
        return row

    def list_live_biometrics(self, user_id=None):
        return [
            b
            for b in self.biometrics
            if b["destroyed_at"] is None
            and (user_id is None or b["user_id"] == user_id)
        ]

    # -- signals -------------------------------------------------------------
    def add_signal(self, row):
        self.signals.append({"id": uuid.uuid4().hex, **row})

    # -- anchors ---------------------------------------------------------------
    def get_anchor(self, anchor_hash):
        return self.anchors.get(anchor_hash)

    def upsert_anchor(self, anchor_hash, *, user_id, is_banned=False):
        existing = self.anchors.get(anchor_hash)
        if existing:
            return existing
        row = {
            "anchor_hash": anchor_hash,
            "user_id": user_id,
            "is_banned": is_banned,
        }
        self.anchors[anchor_hash] = row
        return row

    # -- attestations + state --------------------------------------------------
    def save_age_attestation(self, row):
        self.age_attestations.append(row)

    def get_age_attestation(self, anchor_hash):
        for row in self.age_attestations:
            if row["identity_anchor_hash"] == anchor_hash:
                return row
        return None

    def set_verification_state(self, user_id, **fields):
        row = self.verification_states.setdefault(
            user_id,
            {
                "user_id": user_id,
                "status": "unverified",
                "current_tier": 0,
                "identity_anchor_hash": None,
            },
        )
        row.update(fields)

    def get_verification_state(self, user_id):
        return self.verification_states.get(user_id)

    # -- retention ---------------------------------------------------------------
    def add_retention_action(self, row):
        row = {"id": uuid.uuid4().hex, "executed_at": None, **row}
        self.retention_actions.append(row)
        return row

    # -- velocity ------------------------------------------------------------------
    def record_attempt(self, session_id, record):
        self.attempts.append(record)
        # Mirror the production persistence shape: attempts are
        # vfy_signals rows under their own signal type, never the
        # decision summary's "velocity" (Rosa's M4 collision).
        self.signals.append(
            {
                "id": uuid.uuid4().hex,
                "session_id": session_id,
                "signal_type": "velocity_attempt",
                "score": None,
                "detail": {
                    "kind": record.kind,
                    "key": record.key,
                    "at": record.at.isoformat(),
                },
            }
        )

    def list_attempts(self):
        return list(self.attempts)


class SupabaseVerifyStore:
    """Production store over the Supabase service-role client.

    Mirrors InMemoryVerifyStore method for method. Integration
    surface: exercised against a live database at deployment, not
    in the unit suite (the repo pattern: services take the client
    from app.database.get_db).
    """

    def __init__(self, db):
        self._db = db

    def _one(self, table: str, row: dict) -> dict:
        result = self._db.table(table).insert(row).execute()
        return result.data[0]

    def ensure_engine_version(self, version, pipeline_config, model_refs):
        found = (
            self._db.table("vfy_engine_versions")
            .select("id")
            .eq("version", version)
            .execute()
        )
        if found.data:
            return found.data[0]["id"]
        self._db.table("vfy_engine_versions").update({"is_active": False}).eq(
            "is_active", True
        ).execute()
        row = self._one(
            "vfy_engine_versions",
            {
                "version": version,
                "pipeline_config": pipeline_config,
                "model_refs": model_refs,
                "is_active": True,
            },
        )
        return row["id"]

    def create_session(
        self, *, user_id, document_type, tier_requested, engine_version_id
    ):
        # document_type is a vfy_sessions column since
        # migration_verify_v3.sql; the row PostgREST returns
        # carries it, exactly as get_session will return it.
        return self._one(
            "vfy_sessions",
            {
                "user_id": user_id,
                "document_type": document_type,
                "tier_requested": tier_requested,
                "engine_version_id": engine_version_id,
            },
        )

    def get_session(self, session_id):
        found = (
            self._db.table("vfy_sessions").select("*").eq("id", session_id).execute()
        )
        return found.data[0] if found.data else None

    def update_session(self, session_id, **fields):
        self._db.table("vfy_sessions").update(fields).eq("id", session_id).execute()

    def create_consent(self, row):
        return self._one("vfy_consents", row)

    def list_consents(self, user_id):
        found = (
            self._db.table("vfy_consents").select("*").eq("user_id", user_id).execute()
        )
        return found.data

    def revoke_consent(self, consent_id, at):
        self._db.table("vfy_consents").update({"revoked_at": at.isoformat()}).eq(
            "id", consent_id
        ).execute()

    def save_document(self, row):
        return self._one("vfy_documents", row)

    def get_document_for_session(self, session_id):
        found = (
            self._db.table("vfy_documents")
            .select("*")
            .eq("session_id", session_id)
            .execute()
        )
        return found.data[0] if found.data else None

    def update_document(self, document_id, **fields):
        self._db.table("vfy_documents").update(fields).eq("id", document_id).execute()

    def save_biometric(self, row):
        row = {k: v for k, v in row.items() if k != "anchor_hash"}
        return self._one("vfy_biometrics", row)

    def list_live_biometrics(self, user_id=None):
        query = self._db.table("vfy_biometrics").select("*").is_("destroyed_at", "null")
        if user_id is not None:
            query = query.eq("user_id", user_id)
        rows = query.execute().data
        # Join the anchor + ban flag through the member's anchor row:
        # the duplicate corpus is keyed by identity anchor, and the
        # ban lives on the anchor.
        anchors = self._db.table("vfy_identity_anchors").select("*").execute().data
        by_user = {a["user_id"]: a for a in anchors if a.get("user_id")}
        out = []
        for row in rows:
            anchor = by_user.get(row["user_id"])
            out.append(
                {
                    **row,
                    "anchor_hash": anchor["anchor_hash"] if anchor else None,
                    "is_banned": bool(anchor and anchor["is_banned"]),
                }
            )
        return out

    def add_signal(self, row):
        self._db.table("vfy_signals").insert(row).execute()

    def get_anchor(self, anchor_hash):
        found = (
            self._db.table("vfy_identity_anchors")
            .select("*")
            .eq("anchor_hash", anchor_hash)
            .execute()
        )
        return found.data[0] if found.data else None

    def upsert_anchor(self, anchor_hash, *, user_id, is_banned=False):
        existing = self.get_anchor(anchor_hash)
        if existing:
            return existing
        return self._one(
            "vfy_identity_anchors",
            {
                "anchor_hash": anchor_hash,
                "user_id": user_id,
                "is_banned": is_banned,
            },
        )

    def save_age_attestation(self, row):
        self._db.table("vfy_age_attestations").upsert(
            row, on_conflict="identity_anchor_hash"
        ).execute()

    def get_age_attestation(self, anchor_hash):
        found = (
            self._db.table("vfy_age_attestations")
            .select("*")
            .eq("identity_anchor_hash", anchor_hash)
            .execute()
        )
        return found.data[0] if found.data else None

    def set_verification_state(self, user_id, **fields):
        anchor_hash = fields.pop("identity_anchor_hash", None)
        existing = self.get_verification_state(user_id)
        if existing:
            self._db.table("vfy_verification_states").update(fields).eq(
                "user_id", user_id
            ).execute()
            return
        if anchor_hash is None:
            raise ValueError("first verification state requires an anchor")
        self._one(
            "vfy_verification_states",
            {
                "identity_anchor_hash": anchor_hash,
                "user_id": user_id,
                **fields,
            },
        )

    def get_verification_state(self, user_id):
        found = (
            self._db.table("vfy_verification_states")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )
        return found.data[0] if found.data else None

    def add_retention_action(self, row):
        return self._one("vfy_retention_actions", row)

    def record_attempt(self, session_id, record):
        # Attempts live under their own signal type. The decision
        # summary (the pipeline's "velocity" signal, detail shaped
        # {exceeded, document, device, network}) shares the table,
        # and parsing it as an attempt crashed every later velocity
        # read (Rosa's M4).
        self.add_signal(
            {
                "session_id": session_id,
                "signal_type": "velocity_attempt",
                "score": None,
                "detail": {
                    "kind": record.kind,
                    "key": record.key,
                    "at": record.at.isoformat(),
                },
            }
        )

    def list_attempts(self):
        from datetime import datetime

        found = (
            self._db.table("vfy_signals")
            .select("detail")
            .eq("signal_type", "velocity_attempt")
            .execute()
        )
        return [
            AttemptRecord(
                kind=row["detail"]["kind"],
                key=row["detail"]["key"],
                at=datetime.fromisoformat(row["detail"]["at"]),
            )
            for row in found.data
        ]
