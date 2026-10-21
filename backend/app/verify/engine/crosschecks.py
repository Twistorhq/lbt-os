"""Document cross-checks: engine stages 1 and 2 (TW-658, spec S5.2).

Three reads of one document (front OCR, MRZ, PDF417 barcode) are
normalized into per-source field sets, then reconciled. The barcode
is canonical on US licenses (it is the machine-read truth the state
printed); the MRZ is canonical on passports (its check digits are a
free integrity proof). Disagreement between reads is a tamper
signal, never a retry prompt and never silently resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.verify.benchmark.documents import agreement_rate

from .normalize import normalize_dob, normalize_doc_number, normalize_expiry

_FIELDS = ("doc_number", "dob", "expiry", "surname", "given_names")


def _normalize_fields(raw: dict, today: date) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in raw.items():
        if value is None or str(value).strip() == "":
            continue
        text = str(value)
        if name == "doc_number":
            out[name] = normalize_doc_number(text)
        elif name == "dob":
            normalized = normalize_dob(text, reference=today)
            if normalized:
                out[name] = normalized
        elif name == "expiry":
            normalized = normalize_expiry(text, reference=today)
            if normalized:
                out[name] = normalized
        elif name in ("surname", "given_names"):
            out[name] = text.strip().upper()
    return out


@dataclass(frozen=True)
class DocumentEvaluation:
    """The reconciled document read: canonical fields + signals."""

    document_type: str
    fields: dict[str, str]  # canonical, normalized (dob/expiry ISO)
    mrz_valid: bool | None  # None when the class carries no MRZ
    agreement: dict[str, bool]  # per-field front-vs-barcode agreement
    tamper_signals: list[str] = field(default_factory=list)

    @property
    def extracted(self) -> bool:
        """True when the canonical read carries the identity core."""
        return bool(self.fields.get("doc_number"))

    def expired(self, on: date) -> bool:
        expiry = self.fields.get("expiry")
        if not expiry:
            return False
        return date.fromisoformat(expiry) < on


def evaluate_document(
    *,
    document_type: str,
    ocr_fields: dict,
    mrz: dict | None,
    pdf417_fields: dict | None,
    today: date,
) -> DocumentEvaluation:
    """Reconcile the three reads of one document.

    `mrz` is the benchmark stage shape: {"parsed", "fields", "valid"}
    from stages.parse_mrz_lines. `pdf417_fields` is the AAMVA field
    dict from stages.parse_aamva. Any read may be absent; absence is
    recorded, never papered over.
    """
    ocr = _normalize_fields(ocr_fields or {}, today)
    barcode = _normalize_fields(pdf417_fields or {}, today)

    tamper: list[str] = []
    agreement: dict[str, bool] = {}

    # Front-to-barcode agreement (the harness's own metric shape):
    # evidenced pairs only, per documents.agreement_rate. Computed
    # only when a barcode was actually decoded: passports carry no
    # PDF417, and an absent read cannot disagree with anything.
    # Within a decoded barcode the harness semantics apply in full:
    # a field present on one side only is a disagreement.
    if barcode:
        rates = agreement_rate([ocr], [barcode], list(_FIELDS))
        for name, rate in rates.items():
            if rate is None:
                continue
            agreement[name] = rate == 1.0
            if rate != 1.0:
                tamper.append(f"front_barcode_disagreement:{name}")

    mrz_valid: bool | None = None
    mrz_fields: dict[str, str] = {}
    if mrz and mrz.get("parsed"):
        valid_map = mrz.get("valid") or {}
        mrz_valid = all(valid_map.values()) if valid_map else False
        if not mrz_valid:
            failed = [k for k, ok in valid_map.items() if not ok]
            tamper.append("mrz_check_failed:" + ",".join(sorted(failed)))
        mrz_fields = _normalize_fields(mrz.get("fields") or {}, today)

    # Canonical preference by class: MRZ on passports, barcode on US
    # licenses and state IDs, OCR as the fallback for every field.
    if document_type == "passport":
        preference = (mrz_fields, ocr, barcode)
    else:
        preference = (barcode, ocr, mrz_fields)
    canonical: dict[str, str] = {}
    for name in _FIELDS:
        for source in preference:
            if source.get(name):
                canonical[name] = source[name]
                break

    return DocumentEvaluation(
        document_type=document_type,
        fields=canonical,
        mrz_valid=mrz_valid,
        agreement=agreement,
        tamper_signals=tamper,
    )


def parse_ocr_fields(lines: list[str]) -> dict[str, str]:
    """Best-effort field extraction from raw OCR text lines.

    Labeled values win ('DOB 27 JUL 1990'); otherwise the shapes
    themselves are the labels: a document-number token, a date in a
    supported print format, and the first all-letters line as the
    name. Anything ambiguous is left out: a missing field routes to
    review, a wrong one can pass a stranger.
    """
    import re

    fields: dict[str, str] = {}
    name_candidate: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        date_match = re.search(r"\d{1,2}\s+[A-Z]{3}\s+\d{4}", upper)
        if date_match and "dob" not in fields:
            fields["dob"] = date_match.group(0)
            continue
        slash_match = re.search(r"\d{2}/\d{2}/\d{4}", upper)
        if slash_match and "dob" not in fields:
            fields["dob"] = slash_match.group(0)
            continue
        for token in upper.split():
            if re.fullmatch(r"[A-Z]\d{6,}[A-Z0-9]*", token):
                fields.setdefault("doc_number", token)
        if name_candidate is None and re.fullmatch(r"[A-Z][A-Z .'-]+", upper):
            words = upper.split()
            if len(words) >= 2:
                name_candidate = upper
    if name_candidate and "name" not in fields:
        fields["name"] = name_candidate
    return fields
