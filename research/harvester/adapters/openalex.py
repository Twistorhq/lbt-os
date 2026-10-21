"""OpenAlex adapter. Degrades gracefully on 429: the sandbox egress IP
shares OpenAlex's free daily budget, so a 429 here is expected, not fatal.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..extract import normalize_year
from ..models import RawRecord
from ._http import limiter_for, make_session
from .base import SourceAdapter

API = "https://api.openalex.org"


class OpenAlexAdapter(SourceAdapter):
    key = "openalex"

    def __init__(
        self,
        mailto: str = "holla@twistor.co",
        per_page: int = 100,
        rate_limit_s: float | None = None,
    ):
        self.mailto = mailto
        self.per_page = per_page
        self.session = make_session()
        self.limiter = limiter_for(urlparse(API).netloc, rate_limit_s or 3.0)

    @staticmethod
    def normalize(work: dict) -> RawRecord:
        authorships = work.get("authorships") or []
        authors = []
        institution = None
        for a in authorships:
            author = a.get("author") or {}
            if author.get("display_name"):
                authors.append(author["display_name"])
            if institution is None:
                insts = a.get("institutions") or []
                if insts and insts[0].get("display_name"):
                    institution = insts[0]["display_name"]
        loc = work.get("primary_location") or {}
        return RawRecord(
            external_id=work.get("id") or "",
            title=work.get("title") or "",
            authors=authors,
            institution=institution,
            year=normalize_year(work.get("publication_year")),
            doi=(work.get("doi") or "").replace("https://doi.org/", "") or None,
            doc_type=work.get("type"),
            abstract=None,  # OpenAlex ships inverted-index abstracts; skip
            source_url=work.get("doi"),
            fulltext_url=(loc.get("pdf_url") or None),
            license=(loc.get("license") or None),
            access_status="open" if loc.get("is_oa") else "unknown",
        )

    def discover(
        self, query: str, max_records: int = 100
    ) -> tuple[list[RawRecord], str | None]:
        host = urlparse(API).netloc
        self.limiter.wait_and_acquire(host)
        try:
            resp = self.session.get(
                f"{API}/works",
                params={
                    "search": query,
                    "mailto": self.mailto,
                    "per-page": min(self.per_page, 200),
                    "select": "id,title,authorships,publication_year,doi,type,primary_location",
                },
                timeout=30,
            )
            status = resp.status_code
        except Exception as exc:
            return [], f"openalex request failed: {exc}"
        if status == 429:
            return [], "openalex rate-limited (429): skipped, retry later"
        if status != 200:
            return [], f"openalex HTTP {status}: skipped"
        records: list[RawRecord] = []
        for work in resp.json().get("results", [])[:max_records]:
            try:
                records.append(self.normalize(work))
            except Exception:
                continue
        return records, None
