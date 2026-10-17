"""OpenAIRE adapter: publications + dissertations, open API.

Prepared by Twistor Holdings LLC.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from ..extract import normalize_authors, normalize_year
from ..models import RawRecord
from ._http import limiter_for, make_session
from .base import SourceAdapter

API = "https://api.openaire.eu"


def _dig(d, *keys):
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and cur:
            cur = cur[0]
        else:
            return None
        if cur is None:
            return None
    return cur


class OpenaireAdapter(SourceAdapter):
    key = "openaire"

    def __init__(self, size: int = 100, format: str = "json"):
        self.size = size
        self.format = format
        self.session = make_session()
        self.limiter = limiter_for(urlparse(API).netloc, 2.0)

    @staticmethod
    def normalize(result: dict) -> RawRecord:
        md = result.get("metadata") or {}
        entity = _dig(md, "oaf:entity", "oaf:result") or {}
        title = entity.get("title") or ""
        if isinstance(title, dict):
            title = title.get("$", "")
        creators = entity.get("creator") or []
        if isinstance(creators, dict):
            creators = [creators]
        authors = [c.get("$", "") for c in creators if isinstance(c, dict)]
        date = entity.get("dateofacceptance") or entity.get("date")
        if isinstance(date, dict):
            date = date.get("$")
        instances = entity.get("instance") or []
        if isinstance(instances, dict):
            instances = [instances]
        web_url = license_ = None
        for inst in instances:
            wr = inst.get("webresource") or {}
            if wr.get("url") and not web_url:
                web_url = wr["url"]
            if inst.get("license") and not license_:
                license_ = inst["license"]
        collected = (entity.get("collectedfrom") or {})
        source_name = collected.get("@name") if isinstance(collected, dict) else None
        pid = entity.get("pid") or result.get("id") or ""
        return RawRecord(
            external_id=str(pid),
            title=str(title),
            authors=normalize_authors(authors),
            institution=source_name,
            year=normalize_year(date),
            doc_type=entity.get("instancetype") if isinstance(entity.get("instancetype"), str) else None,
            abstract=entity.get("description") if isinstance(entity.get("description"), str) else None,
            source_url=web_url,
            license=license_,
            access_status="open" if license_ else "unknown",
        )

    def discover(self, query: str, max_records: int = 100) -> tuple[list[RawRecord], str | None]:
        host = urlparse(API).netloc
        if not self.limiter.acquire(host):
            time.sleep(self.limiter.wait_s(host))
        try:
            resp = self.session.get(
                f"{API}/search/publications",
                params={"title": query, "size": min(max_records, self.size),
                        "format": self.format},
                timeout=30,
            )
        except Exception as exc:
            return [], f"openaire request failed: {exc}"
        if resp.status_code == 429:
            return [], "openaire rate-limited (429): skipped"
        if resp.status_code != 200:
            return [], f"openaire HTTP {resp.status_code}: skipped"
        try:
            results = resp.json()["response"]["results"]["result"]
        except Exception:
            return [], "openaire: unparseable response"
        if isinstance(results, dict):
            results = [results]
        records: list[RawRecord] = []
        for r in results[:max_records]:
            try:
                records.append(self.normalize(r))
            except Exception:
                continue
        return records, None
