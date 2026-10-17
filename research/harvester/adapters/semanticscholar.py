"""Semantic Scholar adapter: enrichment lookups, low volume, no key.

Used for single-paper enrichment (open-access PDF links, citation counts),
not bulk harvesting — the unauthenticated tier is throttled.

Prepared by Twistor Holdings LLC.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from ..extract import normalize_authors, normalize_year
from ..models import RawRecord
from ._http import limiter_for, make_session
from .base import SourceAdapter

API = "https://api.semanticscholar.org"
FIELDS = "title,authors,year,abstract,url,openAccessPdf,externalIds"


class SemanticscholarAdapter(SourceAdapter):
    key = "semanticscholar"

    def __init__(self, limit: int = 100, fields: str = FIELDS):
        self.limit = limit
        self.fields = fields
        self.session = make_session()
        self.limiter = limiter_for(urlparse(API).netloc, 2.0)

    @staticmethod
    def normalize(paper: dict) -> RawRecord:
        ext = paper.get("externalIds") or {}
        doi = ext.get("DOI")
        oapdf = paper.get("openAccessPdf") or {}
        return RawRecord(
            external_id=paper.get("paperId") or "",
            title=paper.get("title") or "",
            authors=normalize_authors([(a.get("name") or "")
                                       for a in paper.get("authors") or []]),
            year=normalize_year(paper.get("year")),
            doi=doi,
            doc_type="paper",
            abstract=paper.get("abstract"),
            source_url=paper.get("url"),
            fulltext_url=oapdf.get("url"),
            access_status="open" if oapdf.get("url") else "unknown",
        )

    def discover(self, query: str, max_records: int = 100) -> tuple[list[RawRecord], str | None]:
        host = urlparse(API).netloc
        if not self.limiter.acquire(host):
            time.sleep(self.limiter.wait_s(host))
        try:
            resp = self.session.get(
                f"{API}/graph/v1/paper/search",
                params={"query": query, "limit": min(max_records, self.limit),
                        "fields": self.fields},
                timeout=30,
            )
        except Exception as exc:
            return [], f"semanticscholar request failed: {exc}"
        if resp.status_code == 429:
            return [], "semanticscholar rate-limited (429): skipped"
        if resp.status_code != 200:
            return [], f"semanticscholar HTTP {resp.status_code}: skipped"
        try:
            papers = resp.json().get("data", [])
        except Exception:
            return [], "semanticscholar: unparseable response"
        records: list[RawRecord] = []
        for p in papers[:max_records]:
            try:
                records.append(self.normalize(p))
            except Exception:
                continue
        return records, None
