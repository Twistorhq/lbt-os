"""sources.yaml loader and validation.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

import os

import yaml

REQUIRED_FIELDS = ("key", "name", "adapter", "enabled")


def load_sources(path: str | None = None) -> list[dict]:
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "sources.yaml")
    if not os.path.exists(path):
        raise ValueError(f"sources config not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("sources.yaml: 'sources' must be a list")
    seen: set[str] = set()
    for s in sources:
        for field in REQUIRED_FIELDS:
            if field not in s:
                raise ValueError(f"sources.yaml: entry missing '{field}': {s}")
        if s["key"] in seen:
            raise ValueError(f"sources.yaml: duplicate key '{s['key']}'")
        seen.add(s["key"])
    return sources


def enabled_sources(path: str | None = None) -> list[dict]:
    return [s for s in load_sources(path) if s.get("enabled")]
