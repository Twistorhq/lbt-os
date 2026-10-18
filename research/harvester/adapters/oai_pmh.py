"""Generic OAI-PMH adapter for university DSpace repositories.

Pages ListRecords with resumption tokens; filters client-side on query
terms since OAI-PMH has no search. Skips deleted records.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from ..extract import normalize_authors, normalize_year
from ..models import RawRecord
from ._http import fetch_pdf_bytes, limiter_for, make_session
from .base import SourceAdapter

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_DOC_TYPE_MAP = {
    "dissertation": "dissertation",
    "doctoral thesis": "dissertation",
    "thesis": "dissertation",
    "master thesis": "dissertation",
    "article": "journal-article",
    "journal article": "journal-article",
}


def _text(el: ET.Element | None) -> str | None:
    if el is not None and el.text:
        t = el.text.strip()
        return t or None
    return None


def _dc_text(md: ET.Element, tag: str) -> list[str]:
    return [t for t in (_text(e) for e in md.findall(f"dc:{tag}", NS)) if t]


class OaiPmhAdapter(SourceAdapter):
    key = "oai_pmh"

    def __init__(
        self,
        base_url: str,
        metadataPrefix: str = "oai_dc",
        match_terms: list[str] | None = None,
        rate_limit_s: float | None = None,
        **_kw,
    ):
        self.base_url = base_url.rstrip("/")
        self.metadataPrefix = metadataPrefix
        # AND-semantics filter terms. Defaults to the query's own terms.
        # sources.yaml should pin the distinctive term (e.g. ["hemp"]) so
        # generic words like "fiber" cannot admit irrelevant records.
        self.match_terms = [t.lower() for t in match_terms] if match_terms else None
        self.session = make_session()
        self.limiter = limiter_for(urlparse(self.base_url).netloc, rate_limit_s or 3.0)

    @staticmethod
    def matches(text: str, terms: list[str]) -> bool:
        haystack = text.lower()
        return all(t.lower() in haystack for t in terms)

    @staticmethod
    def parse_records(xml_text: str) -> tuple[list[RawRecord], str | None]:
        """Parse a ListRecords response. Returns (records, resumptionToken)."""
        root = ET.fromstring(xml_text)
        records: list[RawRecord] = []
        for rec_el in root.findall(".//oai:record", NS):
            header = rec_el.find("oai:header", NS)
            if header is None:
                continue
            if header.get("status") == "deleted":
                continue
            ident = _text(header.find("oai:identifier", NS)) or ""
            md = rec_el.find("oai:metadata", NS)
            if md is None:
                continue
            dc = md.find("{http://www.openarchives.org/OAI/2.0/oai_dc/}dc")
            if dc is None:  # tolerate missing oai_dc wrapper
                dc = md
            titles = _dc_text(dc, "title")
            creators = _dc_text(dc, "creator")
            dates = _dc_text(dc, "date")
            types = _dc_text(dc, "type")
            descs = _dc_text(dc, "description")
            idents = _dc_text(dc, "identifier")
            rights = _dc_text(dc, "rights")
            raw_type = (types[0] if types else "").lower()
            doc_type = _DOC_TYPE_MAP.get(raw_type, raw_type or None)
            source_url = next((i for i in idents if i.startswith("http")), None)
            # M2: OAI-PMH oai_dc rarely exposes bitstreams, but many records
            # carry a direct PDF identifier — use it so fetch_fulltext is live.
            fulltext_url = next((i for i in idents if i.lower().endswith(".pdf")), None)
            domain = ""
            if ident.startswith("oai:"):
                domain = ident.split(":")[1] if ":" in ident else ""
            records.append(
                RawRecord(
                    external_id=ident,
                    title=titles[0] if titles else "",
                    authors=normalize_authors(creators),
                    institution=domain or None,
                    year=normalize_year(dates[0] if dates else None),
                    doc_type=doc_type,
                    abstract=descs[0] if descs else None,
                    source_url=source_url,
                    fulltext_url=fulltext_url,
                    license=rights[0] if rights else None,
                    access_status="open" if rights else "unknown",
                )
            )
        token_el = root.find(".//oai:resumptionToken", NS)
        token = _text(token_el)
        return records, token

    def _list_records(
        self, resumption_token: str | None = None
    ) -> tuple[list[RawRecord], str | None]:
        host = urlparse(self.base_url).netloc
        self.limiter.wait_and_acquire(host)
        params = (
            {"verb": "ListRecords", "resumptionToken": resumption_token}
            if resumption_token
            else {"verb": "ListRecords", "metadataPrefix": self.metadataPrefix}
        )
        resp = self.session.get(self.base_url, params=params, timeout=60)
        resp.raise_for_status()
        return self.parse_records(resp.text)

    def discover(
        self, query: str, max_records: int = 100
    ) -> tuple[list[RawRecord], str | None]:
        terms = self.match_terms or [t.lower() for t in query.split() if len(t) > 2]
        records: list[RawRecord] = []
        token: str | None = None
        note = None
        pages = 0
        while len(records) < max_records and pages < 50:
            pages += 1
            try:
                batch, token = self._list_records(token)
            except Exception as exc:
                note = f"oai-pmh error after {len(records)} records: {exc}"
                break
            for rec in batch:
                if not self.matches(f"{rec.title} {rec.abstract or ''}", terms):
                    continue
                records.append(rec)
                if len(records) >= max_records:
                    break
            if not token:
                break
        return records, note

    def fetch_fulltext(self, record: RawRecord) -> bytes | None:
        return fetch_pdf_bytes(record.fulltext_url or "", self.session)
