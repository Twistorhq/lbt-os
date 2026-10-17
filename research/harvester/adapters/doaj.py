"""DOAJ adapter: open-access journal articles, open search API.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

from urllib.parse import quote, urlparse

from ..extract import normalize_authors, normalize_year
from ..models import RawRecord
from ._http import fetch_pdf_bytes, limiter_for, make_session
from .base import SourceAdapter

API = "https://doaj.org/api"


class DoajAdapter(SourceAdapter):
    key = "doaj"

    def __init__(self, pageSize: int = 100, rate_limit_s: float | None = None):
        self.pageSize = pageSize
        self.session = make_session()
        self.limiter = limiter_for(urlparse(API).netloc, rate_limit_s or 2.0)

    @staticmethod
    def normalize(hit: dict) -> RawRecord:
        bib = hit.get("bibjson") or {}
        authors = [(a.get("name") or "") for a in bib.get("author") or []]
        fulltext = None
        for link in bib.get("link") or []:
            if link.get("content_type") == "fulltext" and link.get("url"):
                fulltext = link["url"]
                break
        licenses = bib.get("license") or []
        license_type = licenses[0].get("type") if licenses else None
        dois = [
            i.get("id") for i in bib.get("identifier") or [] if i.get("type") == "doi"
        ]
        return RawRecord(
            external_id=hit.get("id") or (dois[0] if dois else ""),
            title=bib.get("title") or "",
            authors=normalize_authors(authors),
            institution=None,
            year=normalize_year(bib.get("year")),
            doi=dois[0] if dois else None,
            doc_type="journal-article",
            abstract=bib.get("abstract"),
            source_url=fulltext,
            fulltext_url=fulltext if fulltext and fulltext.endswith(".pdf") else None,
            license=license_type,
            access_status="open",
        )

    def discover(
        self, query: str, max_records: int = 100
    ) -> tuple[list[RawRecord], str | None]:
        host = urlparse(API).netloc
        self.limiter.wait_and_acquire(host)
        try:
            resp = self.session.get(
                f"{API}/search/articles/{quote(query)}",
                params={"pageSize": min(max_records, self.pageSize)},
                timeout=30,
            )
        except Exception as exc:
            return [], f"doaj request failed: {exc}"
        if resp.status_code == 429:
            return [], "doaj rate-limited (429): skipped"
        if resp.status_code != 200:
            return [], f"doaj HTTP {resp.status_code}: skipped"
        try:
            hits = resp.json().get("results", [])
        except Exception:
            return [], "doaj: unparseable response"
        records: list[RawRecord] = []
        for hit in hits[:max_records]:
            try:
                records.append(self.normalize(hit))
            except Exception:
                continue
        return records, None

    def fetch_fulltext(self, record: RawRecord) -> bytes | None:
        return fetch_pdf_bytes(record.fulltext_url or "", self.session)
