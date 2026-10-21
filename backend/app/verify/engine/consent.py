"""Consent writes for the adopted BIPA-standard flow (TW-658).

TW-654 Section 1.4 item 1, adopted as decision D1: consent is a
standalone moment before capture, separate from the terms of
service, recording an affirmative logged act against the version
of the text shown. vfy_consents is that record. This module is the
only writer: grant, require (the capture gate), and withdraw.

Withdrawal is effective immediately for standing: the consent is
revoked, the member's tier drops back to the ghost town, and a
destruction action for every live template is recorded into the
TW-650 retention state machine (trigger verification_revocation,
due immediately). The retention job executes the tombstoning; the
engine never hard-deletes a biometric row.
"""

from __future__ import annotations

from datetime import datetime

from .config import CONSENT_POLICY_VERSION, RETENTION_SCHEDULE_VERSION
from .errors import ConsentRequired
from .store import VerifyStore

_BIOMETRIC = "biometric_capture"
_SCHOOL = "school_id_biometric_capture"


def consent_type_for(document_type: str) -> str:
    """The consent class a document class rides on (mirrors the
    v1 schema's consent-type constraint triggers)."""
    return _SCHOOL if document_type == "school_id" else _BIOMETRIC


def grant_consent(
    store: VerifyStore, user_id: str, document_type: str, *, at: datetime | None = None
) -> dict:
    """Record the affirmative consent act under the current versions."""
    row = {
        "user_id": user_id,
        "consent_type": consent_type_for(document_type),
        "policy_version": CONSENT_POLICY_VERSION,
        "retention_schedule_version": RETENTION_SCHEDULE_VERSION,
    }
    if at is not None:
        row["granted_at"] = at
    return store.create_consent(row)


def active_consent(store: VerifyStore, user_id: str, document_type: str) -> dict | None:
    """The member's live consent of the required class and version."""
    wanted = consent_type_for(document_type)
    for consent in store.list_consents(user_id):
        if (
            consent["consent_type"] == wanted
            and consent["revoked_at"] is None
            and consent["policy_version"] == CONSENT_POLICY_VERSION
            and consent["retention_schedule_version"] == RETENTION_SCHEDULE_VERSION
        ):
            return consent
    return None


def require_consent(store: VerifyStore, user_id: str, document_type: str) -> dict:
    """The capture gate: no current consent, no capture."""
    consent = active_consent(store, user_id, document_type)
    if consent is None:
        raise ConsentRequired(
            f"no current {consent_type_for(document_type)} consent for user "
            f"{user_id} under policy {CONSENT_POLICY_VERSION}"
        )
    return consent


def withdraw_consent(store: VerifyStore, user_id: str, *, at: datetime) -> dict:
    """Revoke every live consent, drop standing, schedule destruction."""
    revoked = 0
    for consent in store.list_consents(user_id):
        if consent["revoked_at"] is None:
            store.revoke_consent(consent["id"], at)
            revoked += 1

    scheduled = 0
    for biometric in store.list_live_biometrics(user_id):
        store.add_retention_action(
            {
                "user_id": user_id,
                "biometric_id": biometric["id"],
                "document_id": None,
                "action": "destroy_biometric",
                "trigger": "verification_revocation",
                "determined_at": at,
                "due_at": at,
                "hold": False,
                "hold_reason": None,
            }
        )
        scheduled += 1

    state = store.get_verification_state(user_id)
    if state and state["current_tier"] > 0:
        store.set_verification_state(
            user_id, status="revoked", current_tier=0, dropped_at=at
        )

    return {"consents_revoked": revoked, "destructions_scheduled": scheduled}
