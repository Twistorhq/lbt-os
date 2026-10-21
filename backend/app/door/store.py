"""Door Pass store: enrollments, door consents, scans (TW-671).

Prototype in-memory shape of the dps_ tables TW-657 designs. The
scan rows written here are the server side transaction evidence
records. No raw image, no biometric template, no name, and no
birthdate ever lands in a scan row, by construction of the service
that writes them. The match score, the threshold in force, and the
engine version DO land here, because TW-672 Section 4.3 item 5
makes the preserved record the defense package; they are server
side only and never cross to a venue facing surface, which
receives verdicts and reference hashes only (TW-657 Section 2.1).

The store also holds the venue agreement registry: the agreement
reference under which Twistor operates the system for a venue
(the seller agent agreement in North Carolina). A reliance
transaction binds to one registered agreement for its venue at
creation, and the binding is re-checked at finalization.

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
        self.venue_agreements: dict[str, dict] = {}

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

    def set_scan_staff_action(self, scan_id: str, action: str) -> dict | None:
        """Record the staff action taken on a result (TW-672
        Section 4.3 item 7) on the scan it belongs to."""
        for scan in self.scans:
            if scan["id"] == scan_id:
                scan["staff_action"] = action
                return scan
        return None

    # -- venue agreements ----------------------------------------------

    def register_venue_agreement(
        self, agreement_ref: str, *, venue_id: str, state_code: str
    ) -> dict:
        """Register the agreement under which Twistor operates the
        system for one venue (the seller agent agreement in NC)."""
        row = {
            "agreement_ref": agreement_ref,
            "venue_id": venue_id,
            "state_code": state_code,
            "status": "active",
        }
        self.venue_agreements[agreement_ref] = row
        return row

    def get_venue_agreement(self, agreement_ref: str) -> dict | None:
        return self.venue_agreements.get(agreement_ref)

    def revoke_venue_agreement(self, agreement_ref: str) -> None:
        agreement = self.venue_agreements.get(agreement_ref)
        if agreement is not None:
            agreement["status"] = "revoked"


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
