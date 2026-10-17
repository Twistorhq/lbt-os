"""Crossref adapter: published-paper metadata, license-aware.

Prepared by Twistor Holdings LLC.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..extract import normalize_authors, normalize_year
from ..models import RawRecord
from ._http import fetch_pdf_bytes, limiter_for, make_session
from .base import SourceAdapter

API = "https://api.crossref.org"


def _license_label(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"creativecommons\.org/licenses/([a-z\-]+)/", url)
    if m:
        variant = m.group(1)
        return f"cc-{variant}" if variant.startswith("by") else variant
    if "open-access" in url or "creativecommons.org/publicdomain" in url:
        return "open"
    return "other"


class CrossrefAdapter(SourceAdapter):
    key = "crossref"

    def __init__(self, mailto: str = "holla@twistor.co", rows: int = 100):
        self.mailto = mailto
        self.rows = rows
        self.session = make_session()
        self.limiter = limiter_for(urlparse(API).netloc, 2.0)

    @staticmethod
    def normalize(work: dict) -> RawRecord:
        doi = (work.get("DOI") or "").strip() or None
        titles = work.get("title") or []
        title = titles[0] if titles else ""
        authors = normalize_authors(work.get("author") or [])
        year = None
        pub = work.get("published") or work.get("created") or {}
        if isinstance(pub, dict):
            year = normalize_year(pub.get("date-parts"))
        licenses = work.get("license") or []
        license_label = _license_label(licenses[0].get("URL")) if licenses else None
        fulltext_url = None
        for link in work.get("link") or []:
            if "pdf" in (link.get("content-type") or "").lower():
                fulltext_url = link.get("URL")
                break
        institution = None
        first_author = (work.get("author") or [{}])[0]
        affils = first_author.get("affiliation") or []
        if affils:
            institution = affils[0].get("name")
        source_url = work.get("URL") or (f"https://doi.org/{doi}" if doi else None)
        return RawRecord(
            external_id=doi or source_url or title,
            title=title,
            authors=authors,
            institution=institution,
            year=year,
            doi=doi,
            doc_type=work.get("type"),
            abstract=work.get("abstract"),
            source_url=source_url,
            fulltext_url=fulltext_url,
            license=license_label,
            access_status="open" if license_label and license_label != "other" else "unknown",
        )

    def discover(self, query: str, max_records: int = 100) -> tuple[list[RawRecord], str | None]:
        records: list[RawRecord] = []
        rows = min(self.rows, 100)
        offset = 0
        note = None
        while len(records) < max_records:
            if not self.limiter.acquire(urlparse(API).netloc):
                import time
                time.sleep(self.limiter.wait_s(urlparse(API).netloc))
            try:
                resp = self.session.get(
                    f"{API}/works",
                    params={
                        "query": query,
                        "rows": rows,
                        "offset": offset,
                        "mailto": self.mailto,
                        "select": "DOI,title,author,published,URL,license,link,abstract,type",
                    },
                    timeout=30,
                )
            except Exception as exc:
                note = f"crossref request failed: {exc}"
                break
            if resp.status_code == 429:
                note = "crossref rate-limited (429): partial results"
                break
            if resp.status_code != 200:
                note = f"crossref HTTP {resp.status_code}: partial results"
                break
            try:
                items = resp.json()["message"]["items"]
            except Exception:
                note = "crossref: unparseable response"
                break
            if not items:
                break
            for work in items:
                try:
                    records.append(self.normalize(work))
                except Exception:
                    continue  # one bad record never kills the run
                if len(records) >= max_records:
                    break
            offset += rows
        return records, note

    def fetch_fulltext(self, record: RawRecord) -> bytes | None:
        return fetch_pdf_bytes(record.fulltext_url or "", self.session)
