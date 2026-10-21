"""Field normalization for the verification engine (TW-658).

One canonical shape per field, produced before hashing or
comparison, so the OCR read, the MRZ, and the PDF417 payload of the
same document meet in the middle. A value that cannot be normalized
returns None: the engine never guesses a date or repairs a name.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def normalize_name(value: str) -> str:
    """Uppercase, collapse whitespace. 'SERWAA, AMA' becomes 'AMA SERWAA'."""
    cleaned = re.sub(r"\s+", " ", value.replace(",", " ")).strip().upper()
    parts = cleaned.split(" ")
    if "," in value and len(parts) >= 2:
        # AAMVA prints surname first; the canonical order is given-first.
        return " ".join(parts[1:] + parts[:1])
    return cleaned


def normalize_doc_number(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _mrz_date(value: str, reference: date, *, future_ok: bool) -> date | None:
    """YYMMDD with an explicit century rule.

    Birthdates are never in the future: pick the latest candidate
    not after the reference. Expiry dates may be: pick the earliest
    candidate within a generation before the reference or later.
    """
    if not re.fullmatch(r"\d{6}", value):
        return None
    yy, mm, dd = int(value[0:2]), int(value[2:4]), int(value[4:6])
    candidates = [
        d
        for d in (_safe_date(1900 + yy, mm, dd), _safe_date(2000 + yy, mm, dd))
        if d is not None
    ]
    if not candidates:
        return None
    if future_ok:
        floor = date(reference.year - 15, 1, 1)
        ahead = [d for d in candidates if d >= floor]
        return min(ahead) if ahead else max(candidates)
    past = [d for d in candidates if d <= reference]
    return max(past) if past else min(candidates)


def normalize_dob(value: str, reference: date | None = None) -> str | None:
    """Any supported birthdate shape to ISO YYYY-MM-DD, or None.

    Accepts ISO, MM/DD/YYYY, AAMVA MMDDYYYY, MRZ YYMMDD (century
    rule against `reference`, default today), and 'DD MON YYYY' as
    printed on many document fronts.
    """
    if value is None:
        return None
    text = value.strip().upper()
    if not text:
        return None
    ref = reference or date.today()
    parsed: date | None = None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        y, m, d = (int(p) for p in text.split("-"))
        parsed = _safe_date(y, m, d)
    elif re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        m, d, y = (int(p) for p in text.split("/"))
        parsed = _safe_date(y, m, d)
    elif re.fullmatch(r"\d{8}", text):
        parsed = _safe_date(int(text[4:8]), int(text[0:2]), int(text[2:4]))
    elif re.fullmatch(r"\d{6}", text):
        parsed = _mrz_date(text, ref, future_ok=False)
    else:
        match = re.fullmatch(r"(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", text)
        if match and match.group(2) in _MONTHS:
            parsed = _safe_date(
                int(match.group(3)), _MONTHS[match.group(2)], int(match.group(1))
            )
    return parsed.isoformat() if parsed else None


def normalize_expiry(value: str, reference: date | None = None) -> str | None:
    """Expiry dates share the DOB shapes but may sit in the future."""
    if value is None:
        return None
    text = value.strip().upper()
    ref = reference or date.today()
    if re.fullmatch(r"\d{6}", text):
        parsed = _mrz_date(text, ref, future_ok=True)
        return parsed.isoformat() if parsed else None
    return normalize_dob(text, reference=ref)


def _age_on(dob: date, on: date) -> int:
    years = on.year - dob.year
    if (on.month, on.day) < (dob.month, dob.day):
        years -= 1
    return years


def age_band_for(dob: date, on: date) -> str:
    """The band is the only age fact that leaves the engine (S5.8)."""
    age = _age_on(dob, on)
    if age < 16:
        return "under_16"
    if age < 18:
        return "age_16_17"
    if age < 21:
        return "age_18_20"
    return "age_21_plus"


def next_band_date_for(dob: date, on: date) -> date | None:
    """The birthday the band graduates on, or None at the top band."""
    age = _age_on(dob, on)
    for target in (16, 18, 21):
        if age < target:
            try:
                return date(dob.year + target, dob.month, dob.day)
            except ValueError:
                # Feb 29 birthdays graduate on Mar 1 in common years.
                return date(dob.year + target, 3, 1)
    return None
