"""Document-stage math for the benchmark harness (TW-649).

Pure stdlib. Three jobs:

1. MRZ check digits (ICAO 9303): the near-deterministic cross-check
   that lets a passport's machine-readable zone validate the OCR read.
2. OCR field accuracy: exact-match per field against gold labels.
3. Front-to-barcode agreement: how often the OCR read of the printed
   front agrees with the PDF417 barcode payload (AAMVA) on the same
   document. Disagreement is a tamper and quality signal in the real
   engine; here it is a benchmark metric.
"""

from __future__ import annotations

from typing import Optional

_WEIGHTS = (7, 3, 1)


def _char_value(ch: str) -> int:
    if ch == "<":
        return 0
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    raise ValueError(f"character {ch!r} is not valid in an MRZ field")


def mrz_check_digit(field: str) -> int:
    """ICAO 9303 check digit for an MRZ field (weights 7, 3, 1 repeating)."""
    total = sum(
        _char_value(ch) * _WEIGHTS[i % 3] for i, ch in enumerate(field.upper())
    )
    return total % 10


def mrz_field_valid(field_with_digit: str) -> bool:
    """True when the trailing character is the field's check digit."""
    if len(field_with_digit) < 2:
        return False
    body, digit = field_with_digit[:-1], field_with_digit[-1]
    if not digit.isdigit():
        return False
    try:
        return mrz_check_digit(body) == int(digit)
    except ValueError:
        return False


def field_accuracy(
    predicted: dict[str, str], gold: dict[str, str]
) -> dict[str, bool]:
    """Exact-match accuracy per gold field. A missing prediction is wrong.

    Comparison is exact (case-sensitive): callers normalize case and
    whitespace before scoring, so the metric measures the pipeline as
    configured, not a forgiving variant of it.
    """
    return {name: predicted.get(name) == value for name, value in gold.items()}


def agreement_rate(
    front: list[dict[str, str]],
    barcode: list[dict[str, str]],
    fields: list[str],
) -> dict[str, Optional[float]]:
    """Per-field share of evidenced pairs whose front and barcode agree.

    Pairs are positional (the same document read two ways). A pair is
    evidence for a field only when at least one side carries it: a
    pair missing the field on both sides says nothing and does not
    count toward the rate. Agreement requires equal present values,
    so a value present on one side only is a disagreement. A field
    with no evidenced pairs is None (unmeasured), never 1.0 on no
    evidence.
    """
    pairs = list(zip(front, barcode))
    rates: dict[str, Optional[float]] = {}
    for name in fields:
        evidenced = 0
        agree = 0
        for f, b in pairs:
            front_value = f.get(name)
            barcode_value = b.get(name)
            if front_value is None and barcode_value is None:
                continue
            evidenced += 1
            if front_value == barcode_value:
                agree += 1
        rates[name] = (agree / evidenced) if evidenced else None
    return rates
