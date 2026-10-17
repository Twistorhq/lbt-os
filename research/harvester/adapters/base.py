"""Source adapter interface.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import RawRecord


class SourceAdapter(ABC):
    """One adapter per upstream source. All network I/O goes through the
    adapter's session so politeness (rate limiting, User-Agent) is uniform."""

    key: str = ""

    @abstractmethod
    def discover(
        self, query: str, max_records: int = 100
    ) -> tuple[list[RawRecord], str | None]:
        """Return (records, note). `note` carries degradation info
        (e.g. rate-limited) or None when the run was clean."""

    def fetch_fulltext(self, record: RawRecord) -> bytes | None:
        """Best-effort PDF fetch. Returns None when unavailable."""
        return None
