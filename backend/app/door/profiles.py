"""State profiles and the fail closed resolver (TW-671 prototype).

A prototype scale implementation of the TW-657 design: one
immutable profile version per state, a canonical hash over the
authored content only, a four record approval chain (Diego matrix
import, Angela sign off, Rosa review, Justynn enable) bound to
that hash, and one resolver every caller shares. Statuses that
TW-657 models as projections from approval and change event
records (Angela sign off expiry, authorization state) ride on the
records here; the resolver reads them from the records, never from
a writable status column on the profile row.

Fixture states below are test data shaped like the TW-657 ship
matrix (FULL AZ/CA/NC, PARTIAL CO/NV, KILL TX by score gate).
WA is a synthetic disabled fixture (incomplete approval chain);
it is not a matrix assignment and claims nothing about Washington.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

RELIANCE_BIOMETRIC = "reliance_biometric"
LANE_QR_PHOTO = "lane_qr_photo"

APPROVAL_KINDS = (
    "diego_matrix_import",
    "angela_signoff",
    "rosa_review",
    "justynn_enable",
)

LANE_CLAIM_KEYS = (
    "lane_priority_entry",
    "lane_not_identification",
    "lane_physical_id_required",
)

# Enrollment requirement handlers the TW-658 build actually has.
# A profile may name only handlers from this set; naming anything
# else disables the profile (the spec Section 3 rule: a profile
# that needs a handler the build does not have cannot activate).
# "secondary_identity_check" is deliberately absent from this
# default set: TW-678 built the stage (verify/engine/secondary.py,
# the D2 build item from Angela's TW-672 Finding 2), but no
# production data source is wired behind its port, because none
# exists at $0 (commercial per-use KBA and public records data;
# AAMVA DLDV charges setup plus per-transaction fees). The handler
# counts as built only through built_enrollment_handlers(), which
# adds it when a production-grade secondary adapter is actually
# configured. Until then, an Arizona profile naming it cannot
# activate, which is the honest state of Arizona.
BUILT_ENROLLMENT_HANDLERS = (
    "document_auth_electronic",
    "contemporaneous_biometric_link",
    "seller_agent_registration",
)

SECONDARY_IDENTITY_HANDLER = "secondary_identity_check"


def built_enrollment_handlers(secondary_port=None) -> tuple[str, ...]:
    """The handler set the build genuinely has, given its wiring.

    The secondary identity handler joins the set only when a
    secondary port is configured AND the adapter declares itself
    production grade (it queries a real records source). A test
    fixture, however well it checks its fixture data, declares
    False and opens nothing: the gate follows the wiring, never
    a claim.
    """
    handlers = list(BUILT_ENROLLMENT_HANDLERS)
    if secondary_port is not None and (
        getattr(secondary_port, "production_grade", False) is True
    ):
        handlers.append(SECONDARY_IDENTITY_HANDLER)
    return tuple(handlers)


@dataclass(frozen=True)
class StateProfile:
    """One immutable authored profile version for one state."""

    state_code: str
    version: int
    mode: str  # full | partial | kill | disabled (authored ceiling)
    diego_score: float
    entry_lane_enabled: bool
    entry_flow_mode: str | None
    entry_legal_effect: str  # none | house_rule | protected
    alcohol_service_reliance_enabled: bool
    alcohol_service_flow_mode: str | None
    minimum_age_band: str
    authorization_required: bool
    # Prototype stand-in for the TW-657 change event projection:
    # production reads this from authorization_change events.
    authorization_status: str  # not_required | obtained | pending | revoked
    liveness_required: bool
    claim_key_set: tuple[str, ...]
    contract_pack_key: str | None
    proof_bundle_key: str | None
    scan_retention_days: int
    # Whether the state's legal basis permits ENTRY reliance at
    # all. Arizona (4-241(W)) and California (25660(b)(1) reaching
    # 25665) permit it; North Carolina does not, because
    # G.S. 18B-302(d)(3) is a sales only defense (TW-657 legal
    # specifications Section 2.3, TW-672 Section 5). The resolver
    # refuses to activate a profile that authors entry reliance
    # this flag does not permit.
    entry_reliance_permitted: bool = False
    # The legal effect an alcohol service scan carries, kept
    # separate from the entry effect: North Carolina service scans
    # are protected even though its entry reliance does not exist.
    alcohol_service_legal_effect: str = "none"
    # Enrollment requirement handlers the profile names (TW-671
    # spec Section 3); every one must be in
    # BUILT_ENROLLMENT_HANDLERS for the profile to activate.
    enrollment_requirement_handlers: tuple[str, ...] = ()
    # Whether a reliance transaction at a venue requires a
    # registered venue agreement reference (the seller agent
    # agreement in NC; the venue agreement in AZ and CA, TW-672
    # Section 4.3 item 1), re-checked at finalization.
    venue_agreement_required: bool = False
    canonical_hash: str = ""

    def authored_payload(self) -> dict:
        """The authored content the canonical hash covers (TW-657
        Section 3.1): everything above except the hash itself."""
        return {
            "state_code": self.state_code,
            "version": self.version,
            "mode": self.mode,
            "diego_score": self.diego_score,
            "entry_lane_enabled": self.entry_lane_enabled,
            "entry_flow_mode": self.entry_flow_mode,
            "entry_legal_effect": self.entry_legal_effect,
            "alcohol_service_reliance_enabled": (self.alcohol_service_reliance_enabled),
            "alcohol_service_flow_mode": self.alcohol_service_flow_mode,
            "minimum_age_band": self.minimum_age_band,
            "authorization_required": self.authorization_required,
            "liveness_required": self.liveness_required,
            "claim_key_set": list(self.claim_key_set),
            "contract_pack_key": self.contract_pack_key,
            "proof_bundle_key": self.proof_bundle_key,
            "scan_retention_days": self.scan_retention_days,
            "entry_reliance_permitted": self.entry_reliance_permitted,
            "alcohol_service_legal_effect": self.alcohol_service_legal_effect,
            "enrollment_requirement_handlers": list(
                self.enrollment_requirement_handlers
            ),
            "venue_agreement_required": self.venue_agreement_required,
        }


def canonical_hash_for(profile: StateProfile) -> str:
    """SHA-256 over the canonical JSON of the authored content.

    Production uses RFC 8785 (JCS) canonicalization per TW-657;
    sorted key compact JSON is the prototype's deterministic
    stand-in and is never mixed with production hashes.
    """
    payload = json.dumps(
        profile.authored_payload(), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def author_profile(**fields) -> StateProfile:
    """Build a profile with its canonical hash computed at authoring."""
    draft = StateProfile(**fields)
    return StateProfile(**{**fields, "canonical_hash": canonical_hash_for(draft)})


@dataclass(frozen=True)
class ApprovalRecord:
    kind: str
    profile_hash: str
    decision: str = "affirmative"
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ProfileDecision:
    """The resolver's answer: effective mode and what it allows."""

    state_code: str
    mode: str  # full | partial | kill | disabled (effective)
    reason: str
    profile_version: int | None = None
    profile_hash: str = ""
    entry_flow_mode: str | None = None
    alcohol_service_flow_mode: str | None = None
    entry_legal_effect: str = "none"
    alcohol_service_legal_effect: str = "none"
    alcohol_service_reliance_enabled: bool = False
    venue_agreement_required: bool = False
    claim_key_set: tuple[str, ...] = ()
    contract_pack_key: str | None = None
    proof_bundle_key: str | None = None
    authorization_status: str = "unknown"
    liveness_required: bool = True

    def flow_for(self, purpose: str) -> str | None:
        if purpose == "alcohol_service":
            return (
                self.alcohol_service_flow_mode
                if self.alcohol_service_reliance_enabled
                else None
            )
        if purpose == "entry":
            return self.entry_flow_mode
        return None


class ProfileResolver:
    """One function owns the decision (TW-657 Section 4.1)."""

    def __init__(
        self,
        profiles: dict[str, tuple[StateProfile, list[ApprovalRecord]]],
        *,
        available_handlers: tuple[str, ...] | None = None,
    ):
        self._profiles = profiles
        # The handlers the caller's build genuinely has (TW-678):
        # the default is the shipped set, in which the secondary
        # identity handler is absent because no production records
        # source is wired. A caller whose engine wiring configures
        # a production-grade secondary adapter passes
        # built_enrollment_handlers(port) here.
        self._available_handlers = frozenset(
            available_handlers
            if available_handlers is not None
            else BUILT_ENROLLMENT_HANDLERS
        )

    def resolve(self, state_code: str, at_time: datetime) -> ProfileDecision:
        entry = self._profiles.get(state_code)
        if entry is None:
            return ProfileDecision(state_code, "disabled", "no_profile")
        profile, approvals = entry

        if canonical_hash_for(profile) != profile.canonical_hash:
            return ProfileDecision(
                state_code,
                "disabled",
                "profile_integrity",
                profile_version=profile.version,
            )

        chain_error = self._check_chain(profile, approvals, at_time)
        if chain_error is not None:
            return ProfileDecision(
                state_code,
                "disabled",
                chain_error,
                profile_version=profile.version,
                profile_hash=profile.canonical_hash,
            )

        if profile.diego_score < 70:
            return ProfileDecision(
                state_code,
                "kill",
                "score_gate",
                profile_version=profile.version,
                profile_hash=profile.canonical_hash,
            )
        if profile.mode == "kill":
            return ProfileDecision(
                state_code,
                "kill",
                "authored_kill",
                profile_version=profile.version,
                profile_hash=profile.canonical_hash,
            )

        if (
            profile.authorization_required
            and profile.authorization_status != "obtained"
        ):
            return ProfileDecision(
                state_code,
                "disabled",
                "authorization_not_obtained",
                profile_version=profile.version,
                profile_hash=profile.canonical_hash,
            )

        missing_handlers = (
            set(profile.enrollment_requirement_handlers) - self._available_handlers
        )
        if missing_handlers:
            # The spec Section 3 rule, enforced: a profile that
            # needs an enrollment handler the build does not have
            # cannot activate. For the secondary identity handler
            # (TW-672 Finding 2, TW-678), "has" means a production
            # records source is genuinely wired, not merely that
            # stage code exists.
            return ProfileDecision(
                state_code,
                "disabled",
                "enrollment_handler_unavailable",
                profile_version=profile.version,
                profile_hash=profile.canonical_hash,
            )

        if profile.alcohol_service_reliance_enabled and (
            profile.mode != "full"
            or profile.alcohol_service_flow_mode != RELIANCE_BIOMETRIC
        ):
            return ProfileDecision(
                state_code,
                "disabled",
                "profile_incoherent",
                profile_version=profile.version,
                profile_hash=profile.canonical_hash,
            )
        if not profile.entry_reliance_permitted and (
            profile.entry_flow_mode == RELIANCE_BIOMETRIC
            or profile.entry_legal_effect == "protected"
        ):
            # A sales only legal basis (North Carolina) cannot
            # author entry reliance, in flow or in effect. Authoring
            # it anyway is a profile defect, not a runtime choice:
            # the profile disables rather than letting a venue be
            # told it holds entry protection the law does not give.
            return ProfileDecision(
                state_code,
                "disabled",
                "profile_incoherent",
                profile_version=profile.version,
                profile_hash=profile.canonical_hash,
            )
        if profile.entry_lane_enabled and profile.mode == "partial":
            missing = set(LANE_CLAIM_KEYS) - set(profile.claim_key_set)
            if missing:
                return ProfileDecision(
                    state_code,
                    "disabled",
                    "lane_claim_keys_missing",
                    profile_version=profile.version,
                    profile_hash=profile.canonical_hash,
                )

        return ProfileDecision(
            state_code=state_code,
            mode=profile.mode,
            reason="active",
            profile_version=profile.version,
            profile_hash=profile.canonical_hash,
            entry_flow_mode=(
                profile.entry_flow_mode if profile.entry_lane_enabled else None
            ),
            alcohol_service_flow_mode=(
                profile.alcohol_service_flow_mode
                if profile.alcohol_service_reliance_enabled
                else None
            ),
            entry_legal_effect=profile.entry_legal_effect,
            alcohol_service_legal_effect=profile.alcohol_service_legal_effect,
            alcohol_service_reliance_enabled=(profile.alcohol_service_reliance_enabled),
            venue_agreement_required=profile.venue_agreement_required,
            claim_key_set=profile.claim_key_set,
            contract_pack_key=profile.contract_pack_key,
            proof_bundle_key=profile.proof_bundle_key,
            authorization_status=profile.authorization_status,
            liveness_required=profile.liveness_required,
        )

    @staticmethod
    def _check_chain(
        profile: StateProfile,
        approvals: list[ApprovalRecord],
        at_time: datetime,
    ) -> str | None:
        """The complete chain of TW-657 Section 3.3, or the reason
        it is incomplete: four kinds, affirmative, in workflow
        order, every record bound to the canonical profile hash,
        Angela's sign off unexpired at resolution time."""
        positions: list[int] = []
        for kind in APPROVAL_KINDS:
            record = next(
                (
                    r
                    for r in approvals
                    if r.kind == kind and r.decision == "affirmative"
                ),
                None,
            )
            if record is None or record.profile_hash != profile.canonical_hash:
                return "approval_chain_incomplete"
            positions.append(approvals.index(record))
            if kind == "angela_signoff":
                if record.expires_at is not None and record.expires_at <= at_time:
                    return "angela_signoff_expired"
        if positions != sorted(positions):
            return "approval_chain_incomplete"
        return None


def _chain(profile: StateProfile, now: datetime, *, skip: tuple = ()):
    return [
        ApprovalRecord(
            kind,
            profile.canonical_hash,
            expires_at=(
                now + timedelta(days=365) if kind == "angela_signoff" else None
            ),
        )
        for kind in APPROVAL_KINDS
        if kind not in skip
    ]


def build_fixture_profiles(
    now: datetime,
) -> dict[str, tuple[StateProfile, list[ApprovalRecord]]]:
    """Fixture profiles for tests and the prototype harness."""
    full_claims = ("reliance_age_result", "protected_entry")
    profiles: dict[str, tuple[StateProfile, list[ApprovalRecord]]] = {}

    # Arizona models its legally current state, not the end state:
    # DLLC authorization has not been granted (status pending), and
    # the profile names the secondary identity proofing handler the
    # 4-101 device definition requires, whose production data
    # source the engine does not have wired yet (TW-672 Finding 2,
    # TW-678). Either ground disables it today.
    az = author_profile(
        state_code="AZ",
        version=1,
        mode="full",
        diego_score=88,
        entry_lane_enabled=True,
        entry_flow_mode=RELIANCE_BIOMETRIC,
        entry_legal_effect="protected",
        entry_reliance_permitted=True,
        alcohol_service_reliance_enabled=True,
        alcohol_service_flow_mode=RELIANCE_BIOMETRIC,
        alcohol_service_legal_effect="protected",
        minimum_age_band="age_21_plus",
        authorization_required=True,
        authorization_status="pending",
        liveness_required=True,
        enrollment_requirement_handlers=(
            "document_auth_electronic",
            "secondary_identity_check",
            "contemporaneous_biometric_link",
        ),
        venue_agreement_required=True,
        claim_key_set=full_claims,
        contract_pack_key="az_reliance_v1",
        proof_bundle_key="az_transaction_proof_v1",
        scan_retention_days=365,
    )
    profiles["AZ"] = (az, _chain(az, now))

    ca = author_profile(
        state_code="CA",
        version=1,
        mode="full",
        diego_score=87,
        entry_lane_enabled=True,
        entry_flow_mode=RELIANCE_BIOMETRIC,
        entry_legal_effect="protected",
        entry_reliance_permitted=True,
        alcohol_service_reliance_enabled=True,
        alcohol_service_flow_mode=RELIANCE_BIOMETRIC,
        alcohol_service_legal_effect="protected",
        minimum_age_band="age_21_plus",
        authorization_required=False,
        authorization_status="not_required",
        liveness_required=True,
        enrollment_requirement_handlers=(
            "document_auth_electronic",
            "contemporaneous_biometric_link",
        ),
        venue_agreement_required=True,
        claim_key_set=full_claims,
        contract_pack_key="ca_reliance_v1",
        proof_bundle_key="ca_evidence_record_v1",
        scan_retention_days=365,
    )
    profiles["CA"] = (ca, _chain(ca, now))

    # North Carolina authors NO entry reliance: G.S. 18B-302(d)(3)
    # is a defense to subsection (a) sales only (TW-657 legal
    # Section 2.3, TW-672 Section 5, spec Section 8.3). Service
    # reliance is protected; entry is not authored at all.
    nc = author_profile(
        state_code="NC",
        version=1,
        mode="full",
        diego_score=77,
        entry_lane_enabled=False,
        entry_flow_mode=None,
        entry_legal_effect="none",
        entry_reliance_permitted=False,
        alcohol_service_reliance_enabled=True,
        alcohol_service_flow_mode=RELIANCE_BIOMETRIC,
        alcohol_service_legal_effect="protected",
        minimum_age_band="age_21_plus",
        authorization_required=False,
        authorization_status="not_required",
        liveness_required=True,
        enrollment_requirement_handlers=(
            "document_auth_electronic",
            "contemporaneous_biometric_link",
            "seller_agent_registration",
        ),
        venue_agreement_required=True,
        claim_key_set=("reliance_age_result",),
        contract_pack_key="nc_seller_agent_v1",
        proof_bundle_key="nc_two_prong_proof_v1",
        scan_retention_days=365,
    )
    profiles["NC"] = (nc, _chain(nc, now))

    for state, score in (("CO", 70), ("NV", 74)):
        lane = author_profile(
            state_code=state,
            version=1,
            mode="partial",
            diego_score=score,
            entry_lane_enabled=True,
            entry_flow_mode=LANE_QR_PHOTO,
            entry_legal_effect="house_rule",
            alcohol_service_reliance_enabled=False,
            alcohol_service_flow_mode=None,
            minimum_age_band="age_21_plus",
            authorization_required=False,
            authorization_status="not_required",
            liveness_required=False,
            claim_key_set=LANE_CLAIM_KEYS,
            contract_pack_key=f"{state.lower()}_lane_no_reliance_v1",
            proof_bundle_key="lane_non_reliance_log_v1",
            # 365, not the 13 month member visible figure TW-654
            # flagged as an unsettled planning number; spec Section
            # 7 does not treat that figure as settled anywhere.
            scan_retention_days=365,
        )
        profiles[state] = (lane, _chain(lane, now))

    tx = author_profile(
        state_code="TX",
        version=1,
        mode="kill",
        diego_score=52,
        entry_lane_enabled=False,
        entry_flow_mode=None,
        entry_legal_effect="none",
        alcohol_service_reliance_enabled=False,
        alcohol_service_flow_mode=None,
        minimum_age_band="age_21_plus",
        authorization_required=False,
        authorization_status="not_required",
        liveness_required=False,
        claim_key_set=(),
        contract_pack_key=None,
        proof_bundle_key=None,
        scan_retention_days=0,
    )
    profiles["TX"] = (tx, _chain(tx, now))

    # Synthetic disabled fixture: a complete looking full profile
    # whose chain stops before Justynn's enable word.
    wa = author_profile(
        state_code="WA",
        version=1,
        mode="full",
        diego_score=75,
        entry_lane_enabled=True,
        entry_flow_mode=RELIANCE_BIOMETRIC,
        entry_legal_effect="protected",
        entry_reliance_permitted=True,
        alcohol_service_reliance_enabled=True,
        alcohol_service_flow_mode=RELIANCE_BIOMETRIC,
        alcohol_service_legal_effect="protected",
        minimum_age_band="age_21_plus",
        authorization_required=False,
        authorization_status="not_required",
        liveness_required=True,
        enrollment_requirement_handlers=(
            "document_auth_electronic",
            "contemporaneous_biometric_link",
        ),
        venue_agreement_required=True,
        claim_key_set=full_claims,
        contract_pack_key="wa_reliance_v1",
        proof_bundle_key="wa_transaction_proof_v1",
        scan_retention_days=365,
    )
    profiles["WA"] = (wa, _chain(wa, now, skip=("justynn_enable",)))

    return profiles


__all__ = [
    "APPROVAL_KINDS",
    "BUILT_ENROLLMENT_HANDLERS",
    "SECONDARY_IDENTITY_HANDLER",
    "built_enrollment_handlers",
    "LANE_CLAIM_KEYS",
    "LANE_QR_PHOTO",
    "RELIANCE_BIOMETRIC",
    "ApprovalRecord",
    "ProfileDecision",
    "ProfileResolver",
    "StateProfile",
    "author_profile",
    "build_fixture_profiles",
    "canonical_hash_for",
]
