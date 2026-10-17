"""Metadata normalization and heuristic material-property extraction.

Heuristic output is always labeled extraction_method='regex-heuristic' with
a confidence score — it is a triage signal, never ground truth.

Prepared by Twistor Holdings LLC.
"""
from __future__ import annotations

import re

HEURISTIC_METHOD = "regex-heuristic"
HEURISTIC_CONFIDENCE = 0.6


def normalize_authors(people) -> list[str]:
    if not people:
        return []
    if isinstance(people, str):
        return [p.strip() for p in people.split(";") if p.strip()]
    out: list[str] = []
    for p in people:
        if isinstance(p, str):
            name = p.strip()
        elif isinstance(p, dict):
            given = (p.get("given") or "").strip()
            family = (p.get("family") or "").strip()
            name = f"{given} {family}".strip()
        else:
            continue
        if name:
            out.append(name)
    return out


def normalize_year(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1500 <= value <= 2100 else None
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        if isinstance(first, (list, tuple)) and first:
            first = first[0]
        return normalize_year(first)
    if isinstance(value, str):
        m = re.search(r"\b(1[5-9]\d{2}|20\d{2}|2100)\b", value)
        if m:
            return int(m.group(1))
    return None


# (property_name, label_pattern, accepted_units)
_PROPERTY_PATTERNS: list[tuple[str, str, tuple[str, ...]]] = [
    ("tensile_strength", r"tensile\s+strength", ("MPa", "GPa")),
    ("tensile_modulus", r"tensile\s+modulus", ("MPa", "GPa")),
    ("flexural_strength", r"flexural\s+strength", ("MPa", "GPa")),
    ("flexural_modulus", r"flexural\s+modulus", ("MPa", "GPa")),
    ("impact_strength", r"impact\s+strength", ("kJ/m2", "kJ/m²", "J/m")),
    ("elongation_at_break", r"elongation(?:\s+at\s+break)?", ("%",)),
    ("density", r"density", ("g/cm3", "g/cm³", "kg/m3")),
    ("fiber_fraction", r"(?:fiber|fibre)(?:\s+content|\s+fraction|\s+loading)?", ("wt%", "vol%", "%")),
]

_UNIT_RE = r"(GPa|MPa|kJ/m2|kJ/m²|J/m|g/cm3|g/cm³|kg/m3|wt%|vol%|%)"


def _normalize_unit(value: float, unit: str) -> tuple[float, str]:
    if unit == "GPa":
        return value * 1000.0, "MPa"
    return value, unit


def extract_properties(text: str | None) -> list[dict]:
    """Heuristically extract material-property values from free text."""
    if not text:
        return []
    records: list[dict] = []
    for prop, label, _units in _PROPERTY_PATTERNS:
        pattern = re.compile(
            rf"{label}[^.\n]{{0,60}}?(\d+(?:\.\d+)?)\s*{_UNIT_RE}",
            re.IGNORECASE,
        )
        for m in pattern.finditer(text):
            raw_value = float(m.group(1))
            raw_unit = m.group(2)
            value, unit = _normalize_unit(raw_value, raw_unit)
            start = max(0, m.start() - 40)
            records.append({
                "property": prop,
                "value": value,
                "unit": unit,
                "test_method": None,
                "context": text[start:m.end()].strip(),
                "extraction_method": HEURISTIC_METHOD,
                "confidence": HEURISTIC_CONFIDENCE,
            })
    # Dedupe identical (property, value, unit) hits from overlapping patterns.
    seen: set[tuple[str, float, str]] = set()
    unique: list[dict] = []
    for r in records:
        key = (r["property"], r["value"], r["unit"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique
