"""arXiv adapter: open Atom API.

Prepared by Twistor Holdings LLC.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from ..extract import normalize_authors, normalize_year
from ..models import RawRecord
from ._http import limiter_for, make_session
from .base import SourceAdapter

API = "https://export.arxiv.org/api"
ATOM = "{http://www.w3.org/2005/Atom}"


class ArxivAdapter(SourceAdapter):
    key = "arxiv"

    def __init__(self, max_results: int = 100):
        self.max_results = max_results
        self.session = make_session()
        self.limiter = limiter_for(urlparse(API).netloc, 3.0)

    @staticmethod
    def normalize(entry: ET.Element) -> RawRecord:
        def text(tag: str) -> str:
            el = entry.find(f"{ATOM}{tag}")
            return (el.text or "").strip() if el is not None and el.text else ""

        arxiv_id = text("id").split("/abs/")[-1]
        authors = [a.findtext(f"{ATOM}name", default="").strip()
                   for a in entry.findall(f"{ATOM}author")]
        pdf_url = None
        for link in entry.findall(f"{ATOM}link"):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
        return RawRecord(
            external_id=f"arxiv:{arxiv_id}",
            title=" ".join(text("title").split()),
            authors=normalize_authors(authors),
            year=normalize_year(text("published")),
            doc_type="preprint",
            abstract=" ".join(text("summary").split()),
            source_url=text("id") or None,
            fulltext_url=pdf_url,
            access_status="open",
        )

    def discover(self, query: str, max_records: int = 100) -> tuple[list[RawRecord], str | None]:
        host = urlparse(API).netloc
        if not self.limiter.acquire(host):
            time.sleep(self.limiter.wait_s(host))
        try:
            resp = self.session.get(
                f"{API}/query",
                params={"search_query": f"all:{query}",
                        "max_results": min(max_records, self.max_results),
                        "sortBy": "relevance"},
                timeout=30,
            )
        except Exception as exc:
            return [], f"arxiv request failed: {exc}"
        if resp.status_code != 200:
            return [], f"arxiv HTTP {resp.status_code}: skipped"
        try:
            root = ET.fromstring(resp.text)
        except Exception:
            return [], "arxiv: unparseable response"
        records: list[RawRecord] = []
        for entry in root.findall(f"{ATOM}entry")[:max_records]:
            try:
                records.append(self.normalize(entry))
            except Exception:
                continue
        return records, None
