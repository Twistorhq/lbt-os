"""Secondary identity verification: engine enrollment stage (TW-678).

Arizona's biometric identity verification device definition
(A.R.S. 4-101, condition (b); Angela's TW-672 Finding 2) requires,
on top of the document authentication the engine already runs, a
SECONDARY verification of the record holder's identity through
either a secondary electronic authentication process using
commercially available data (the statute's examples: a public
records query, a knowledge based authentication quiz) or a state
or federal government system of records. This module is that
stage, behind the same port discipline as the model boundary.

The contract is hash-only, per the medallion PII doctrine
(TW-547): the request that crosses the port carries the HMAC
field hashes and the identity anchor the document stage already
computed, never plaintext, and the result that comes back is a
verdict plus reference hashes. Nothing a secondary source returns
is persisted beyond the signal trail: status, source kind, the
source's record reference hash, which fields matched, and reason
codes.

The FOSS survey verdict (docs/evidence/TW-678-az-secondary-auth
.tdd.md) is that NO production data source exists at $0 today:
knowledge based authentication and public records data are
commercial per-use products, and AAMVA DLDV, the government
records route for driver licenses, charges a setup fee plus a
per-transaction fee and is reached directly or through paid
gateway partners. So no production adapter ships in this build.
The factory in adapters.py configures no port, this stage reports
unavailable, an enrollment that requires the stage fails closed
into review, and the door resolver counts the
``secondary_identity_check`` handler as built only when a
production-grade adapter is genuinely configured (see
app/door/profiles.built_enrollment_handlers). The spend decision
that would change this is Justynn's, flagged in the evidence doc.

The fixture adapter below is a test double over a records
registry keyed by hashes: it performs a real membership and
field-equality check, and it declares ``production_grade =
False`` so no gate anywhere mistakes it for a wired source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.verify.benchmark.stages import StageUnavailable

# The handler name the TW-671 door profiles name for this stage
# (app/door/profiles.py). Single-sourced here; the door gate
# imports the name rather than restating the string.
SECONDARY_IDENTITY_HANDLER = "secondary_identity_check"

# The two statutory routes of A.R.S. 4-101 condition (b), plus the
# knowledge based form of the commercial route.
SOURCE_KINDS = ("public_records", "knowledge_based", "government_records")

# A pass must evidence at least the identity core: the records
# source matched this person, not merely a document number.
_REQUIRED_MATCHED_FIELDS = frozenset({"name", "dob"})


@dataclass(frozen=True)
class SecondaryCheckRequest:
    """Hashes only. Nothing in this object can identify a person
    without the HMAC key, which never crosses the port."""

    anchor_hash: str
    name_hash: str
    dob_hash: str
    doc_number_hash: str
    document_type: str


@dataclass(frozen=True)
class SecondaryCheckResult:
    status: str  # passed | failed | unavailable
    source_kind: str | None  # one of SOURCE_KINDS
    source_ref_hash: str | None
    matched_fields: tuple[str, ...] = ()
    reasons: tuple[str, ...] = field(default_factory=tuple)


class SecondaryIdentityPort(Protocol):
    """One secondary records source.

    ``production_grade`` is the adapter's own declaration that it
    queries a real records source (a licensed commercial data
    product or a government system of records). The door handler
    gate reads it: a test fixture declares False and opens
    nothing.
    """

    production_grade: bool

    def check(self, request: SecondaryCheckRequest) -> SecondaryCheckResult: ...


class FixtureRecordsSecondaryAdapter:
    """Test adapter: a records registry keyed by identity anchor.

    ``records`` maps anchor_hash -> {name_hash, dob_hash,
    doc_number_hash, record_ref_hash}. A check passes only when
    the anchor is registered AND every field hash equals the
    registered record: a real membership and equality check over
    fixture data, not an unconditional pass. NOT a production
    source, and it says so.
    """

    production_grade = False
    source_kind = "public_records"

    def __init__(self, records: dict[str, dict]):
        self._records = records

    def check(self, request: SecondaryCheckRequest) -> SecondaryCheckResult:
        record = self._records.get(request.anchor_hash)
        if record is None:
            return SecondaryCheckResult(
                status="failed",
                source_kind=self.source_kind,
                source_ref_hash=None,
                matched_fields=(),
                reasons=("record_not_found",),
            )
        expected = {
            "name": record["name_hash"],
            "dob": record["dob_hash"],
            "doc_number": record["doc_number_hash"],
        }
        actual = {
            "name": request.name_hash,
            "dob": request.dob_hash,
            "doc_number": request.doc_number_hash,
        }
        matched = tuple(name for name, want in expected.items() if actual[name] == want)
        if len(matched) == len(expected):
            return SecondaryCheckResult(
                status="passed",
                source_kind=self.source_kind,
                source_ref_hash=record["record_ref_hash"],
                matched_fields=matched,
                reasons=(),
            )
        return SecondaryCheckResult(
            status="failed",
            source_kind=self.source_kind,
            source_ref_hash=record["record_ref_hash"],
            matched_fields=matched,
            reasons=("record_field_mismatch",),
        )


def evaluate_secondary_check(
    port: SecondaryIdentityPort | None,
    request: SecondaryCheckRequest,
) -> SecondaryCheckResult:
    """Run the stage and hold the result to the evidence bar.

    Fail closed at every seam: no port configured is unavailable;
    a port that cannot answer is unavailable; a result in an
    unknown status is failed; and a claimed pass that carries no
    source kind, no source record reference, or no match on the
    identity core is downgraded to failed, because an adapter
    that asserts without evidence has verified nothing.
    """
    if port is None:
        return SecondaryCheckResult(
            status="unavailable",
            source_kind=None,
            source_ref_hash=None,
            matched_fields=(),
            reasons=("secondary_source_not_configured",),
        )
    try:
        result = port.check(request)
    except StageUnavailable:
        return SecondaryCheckResult(
            status="unavailable",
            source_kind=None,
            source_ref_hash=None,
            matched_fields=(),
            reasons=("secondary_source_unavailable",),
        )
    if result.status not in ("passed", "failed", "unavailable"):
        return SecondaryCheckResult(
            status="failed",
            source_kind=result.source_kind,
            source_ref_hash=result.source_ref_hash,
            matched_fields=result.matched_fields,
            reasons=tuple(result.reasons) + ("adapter_result_unknown_status",),
        )
    if result.status == "passed":
        evidenced = (
            result.source_kind in SOURCE_KINDS
            and bool(result.source_ref_hash)
            and _REQUIRED_MATCHED_FIELDS <= set(result.matched_fields)
        )
        if not evidenced:
            return SecondaryCheckResult(
                status="failed",
                source_kind=result.source_kind,
                source_ref_hash=result.source_ref_hash,
                matched_fields=result.matched_fields,
                reasons=tuple(result.reasons) + ("adapter_result_missing_evidence",),
            )
    return result


__all__ = [
    "SECONDARY_IDENTITY_HANDLER",
    "SOURCE_KINDS",
    "FixtureRecordsSecondaryAdapter",
    "SecondaryCheckRequest",
    "SecondaryCheckResult",
    "SecondaryIdentityPort",
    "evaluate_secondary_check",
]
