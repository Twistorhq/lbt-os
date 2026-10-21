"""Door Pass store: enrollments, door consents, scans (TW-671).

Prototype in-memory shape of the dps_ tables TW-657 designs. The
scan rows written here are the transaction evidence records: they
carry references and hashes only. No raw image, no biometric
template, no match score, no name, no birthdate ever lands in a
scan row, by construction of the service that writes them.

Door consent is its own instrument. The onboarding biometric
consent (vfy_consents, consent type biometric_capture) authorizes
the verification capture; the door capture rides a separate
explicit consent (TW-647 Section 6.3, gated by TW-654 D1), recorded
here under the door_biometric_capture type against the same
adopted policy and retention versions as the engine.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.verify.engine.config import (
    CONSENT_POLICY_VERSION,
    RETENTION_SCHEDULE_VERSION,
)

DOOR_CONSENT_TYPE = "door_biometric_capture"


class InMemoryDoorStore:
    """In-memory dps_ shape for the prototype and its tests."""

    def __init__(self):
        self.enrollments: dict[str, dict] = {}
        self.consents: list[dict] = []
        self.transactions: dict[str, dict] = {}
        self.scans: list[dict] = []

    # -- enrollments ---------------------------------------------------

    def register_enrollment(self, user_id: str, *, fixture: bool) -> dict:
        """Link a member's passed TW-658 verification to the door.

        `fixture` marks consented test and benchmark enrollments.
        The prototype transacts on fixtures only; see service.py.
        """
        row = {"user_id": user_id, "fixture": fixture}
        self.enrollments[user_id] = row
        return row

    def get_enrollment(self, user_id: str) -> dict | None:
        return self.enrollments.get(user_id)

    # -- door consents ---------------------------------------------------

    def create_consent(self, row: dict) -> dict:
        row = {"id": uuid.uuid4().hex, "revoked_at": None, **row}
        self.consents.append(row)
        return row

    def list_consents(self, user_id: str) -> list[dict]:
        return [c for c in self.consents if c["user_id"] == user_id]

    def revoke_door_consents(self, user_id: str, *, at: datetime) -> int:
        revoked = 0
        for consent in self.consents:
            if (
                consent["user_id"] == user_id
                and consent["consent_type"] == DOOR_CONSENT_TYPE
                and consent["revoked_at"] is None
            ):
                consent["revoked_at"] = at
                revoked += 1
        return revoked

    # -- transactions and scans ------------------------------------------

    def create_transaction(self, row: dict) -> dict:
        row = {"id": uuid.uuid4().hex, **row}
        self.transactions[row["id"]] = row
        return row

    def get_transaction(self, transaction_id: str) -> dict | None:
        return self.transactions.get(transaction_id)

    def update_transaction(self, transaction_id: str, **fields) -> None:
        self.transactions[transaction_id].update(fields)

    def add_scan(self, row: dict) -> dict:
        row = {"id": uuid.uuid4().hex, **row}
        self.scans.append(row)
        return row


def grant_door_consent(
    store: InMemoryDoorStore, user_id: str, *, at: datetime | None = None
) -> dict:
    """Record the separate explicit door capture consent act."""
    row = {
        "user_id": user_id,
        "consent_type": DOOR_CONSENT_TYPE,
        "policy_version": CONSENT_POLICY_VERSION,
        "retention_schedule_version": RETENTION_SCHEDULE_VERSION,
    }
    if at is not None:
        row["granted_at"] = at
    return store.create_consent(row)


def active_door_consent(store: InMemoryDoorStore, user_id: str) -> dict | None:
    """The member's live door consent under the current versions."""
    for consent in store.list_consents(user_id):
        if (
            consent["consent_type"] == DOOR_CONSENT_TYPE
            and consent["revoked_at"] is None
            and consent["policy_version"] == CONSENT_POLICY_VERSION
            and consent["retention_schedule_version"] == RETENTION_SCHEDULE_VERSION
        ):
            return consent
    return None
