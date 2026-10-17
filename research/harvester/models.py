"""Shared record models for the harvester.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RawRecord:
    """One normalized metadata record from any source adapter."""

    external_id: str
    title: str = ""
    authors: list = field(default_factory=list)
    institution: str | None = None
    year: int | None = None
    doi: str | None = None
    doc_type: str | None = None
    abstract: str | None = None
    source_url: str | None = None
    fulltext_url: str | None = None
    license: str | None = None
    access_status: str | None = None
