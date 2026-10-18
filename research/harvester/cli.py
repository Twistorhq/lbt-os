"""Harvester CLI: init-db, run.

One bad record never kills a run (per-item fault isolation); every run is
logged with counts and notes. Public data only.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .adapters.arxiv import ArxivAdapter
from .adapters.base import SourceAdapter
from .adapters.crossref import CrossrefAdapter
from .adapters.doaj import DoajAdapter
from .adapters.oai_pmh import OaiPmhAdapter
from .adapters.openaire import OpenaireAdapter
from .adapters.openalex import OpenAlexAdapter
from .adapters.semanticscholar import SemanticscholarAdapter
from .config import enabled_sources
from .extract import extract_properties
from .store import CorpusStore

ADAPTERS: dict[str, type[SourceAdapter]] = {
    "crossref": CrossrefAdapter,
    "openalex": OpenAlexAdapter,
    "openaire": OpenaireAdapter,
    "doaj": DoajAdapter,
    "arxiv": ArxivAdapter,
    "semanticscholar": SemanticscholarAdapter,
    "oai_pmh": OaiPmhAdapter,
}

# Doc types worth flagging when paywalled (Justynn's paywall rule).
# B3: includes the type labels used by OpenAlex ("article") and OpenAIRE
# ("article"/"publication") so those sources are not silently skipped.
_CANDIDATE_DOC_TYPES = {
    "journal-article",
    "article",
    "publication",
    "dissertation",
    "paper",
    "preprint",
    "report",
    "conference-paper",
}


def is_paywall_candidate(rec, query: str) -> bool:
    """A record is a paywall candidate when it looks valuable (on-topic doc
    type with an abstract) but offers no open fulltext and no open license.
    Such hits are never silently skipped — they go to the hit-list."""
    if rec.fulltext_url:
        return False
    if rec.access_status == "open" or (
        rec.license and rec.license not in ("other", "unknown")
    ):
        return False
    if (rec.doc_type or "").lower() not in _CANDIDATE_DOC_TYPES:
        return False
    return bool((rec.abstract or "").strip() or (rec.title or "").strip())


def paywall_hit_from(rec, source_key: str, run_id: int, query: str) -> dict:
    abstract = (rec.abstract or "").strip()
    return {
        "source_key": source_key,
        "run_id": run_id,
        "external_id": rec.external_id,
        "title": rec.title,
        "authors": rec.authors,
        "year": rec.year,
        "doi": rec.doi,
        "doc_type": rec.doc_type,
        "source_url": rec.source_url,
        "what_it_contains": abstract[:500] if abstract else rec.title,
        "why_valuable": (
            f"On-topic {rec.doc_type} matching '{query}' with no open "
            f"fulltext and no open license"
        ),
        "access_cost": None,  # check the publisher page at buy-decision time
    }


def build_adapter(entry: dict) -> SourceAdapter:
    cls = ADAPTERS.get(entry["adapter"])
    if cls is None:
        raise ValueError(f"unknown adapter '{entry['adapter']}'")
    params = dict(entry.get("params") or {})
    if entry["adapter"] == "oai_pmh":
        params["base_url"] = entry["base_url"]
    # N5: yaml rate_limit_s is honored, not drifted.
    if entry.get("rate_limit_s") is not None:
        params["rate_limit_s"] = entry["rate_limit_s"]
    return cls(**params)


def cmd_init_db(args) -> int:
    store = CorpusStore(args.db)
    store.init_schema()
    for entry in enabled_sources(args.config):
        store._conn.execute(
            "INSERT OR IGNORE INTO sources (key, name, adapter, base_url, enabled, added_at)"
            " VALUES (?,?,?,?,?,datetime('now'))",
            (
                entry["key"],
                entry["name"],
                entry["adapter"],
                entry.get("base_url"),
                1 if entry["enabled"] else 0,
            ),
        )
    store._conn.commit()
    store.close()
    print(json.dumps({"ok": True, "db": args.db}))
    return 0


def cmd_run(args) -> int:
    store = CorpusStore(args.db)
    store.init_schema()
    entries = enabled_sources(args.config)
    if args.sources:
        wanted = {s.strip() for s in args.sources.split(",")}
        entries = [e for e in entries if e["key"] in wanted]
    summary: dict = {"query": args.query, "sources": {}}
    pdf_dir = os.path.join(os.path.dirname(os.path.abspath(args.db)), "pdfs")
    for entry in entries:
        key = entry["key"]
        run_id = store.start_run(key, args.query)
        found = stored = 0
        paywall_added = 0
        notes: list[str] = []
        try:
            adapter = build_adapter(entry)
        except Exception as exc:
            store.finish_run(run_id, "adapter-error", 0, 0, str(exc))
            summary["sources"][key] = {"status": "adapter-error", "note": str(exc)}
            continue
        try:
            records, note = adapter.discover(args.query, max_records=args.max_records)
        except Exception as exc:
            store.finish_run(run_id, "discover-error", 0, 0, str(exc))
            summary["sources"][key] = {"status": "discover-error", "note": str(exc)}
            continue
        if note:
            notes.append(note)
        found = len(records)
        for rec in records:
            try:
                # B2: insert_document reports whether the row was new;
                # extraction runs only for new rows so re-harvests cannot
                # duplicate property_records.
                doc_id, is_new = store.insert_document(
                    {
                        "source_key": key,
                        "run_id": run_id,
                        "external_id": rec.external_id,
                        "title": rec.title,
                        "authors": rec.authors,
                        "institution": rec.institution,
                        "year": rec.year,
                        "doi": rec.doi,
                        "doc_type": rec.doc_type,
                        "abstract": rec.abstract,
                        "source_url": rec.source_url,
                        "fulltext_url": rec.fulltext_url,
                        "license": rec.license,
                        "access_status": rec.access_status,
                        "origin": "harvested",
                    }
                )
                if not is_new:
                    continue
                stored += 1
                # B1: --fetch-pdfs actually fetches now.
                if args.fetch_pdfs:
                    try:
                        pdf_bytes = adapter.fetch_fulltext(rec)
                    except Exception:
                        pdf_bytes = None
                    if pdf_bytes:
                        os.makedirs(pdf_dir, exist_ok=True)
                        pdf_path = os.path.join(pdf_dir, f"{key}_{doc_id}.pdf")
                        with open(pdf_path, "wb") as f:
                            f.write(pdf_bytes)
                        store.set_fulltext_path(doc_id, pdf_path)
                text = f"{rec.title or ''}\n{rec.abstract or ''}"
                for prop in extract_properties(text):
                    try:
                        store.insert_property(doc_id, prop)
                    except Exception:
                        continue
                # Paywall rule: valuable but closed -> hit-list, never skipped.
                try:
                    if is_paywall_candidate(rec, args.query):
                        if store.log_paywall_hit(
                            paywall_hit_from(rec, key, run_id, args.query)
                        ):
                            paywall_added += 1
                except Exception:
                    continue
            except Exception:
                continue  # per-item fault isolation
        store.finish_run(run_id, "ok", found, stored, "; ".join(notes) or None)
        summary["sources"][key] = {
            "status": "ok",
            "found": found,
            "stored": stored,
            "paywall_hits": paywall_added,  # N1: per-run, not cumulative
            "note": "; ".join(notes) or None,
        }
    store.close()
    print(json.dumps(summary, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="harvester", description="Public university data harvester"
    )
    parser.add_argument("--db", required=True, help="SQLite corpus path")
    parser.add_argument("--config", default=None, help="sources.yaml path")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    run_p = sub.add_parser("run")
    run_p.add_argument("--query", required=True)
    run_p.add_argument(
        "--sources",
        default=None,
        help="comma-separated source keys (default: all enabled)",
    )
    run_p.add_argument("--max-records", type=int, default=100)
    run_p.add_argument(
        "--fetch-pdfs",
        action="store_true",
        help="also fetch open PDFs (robots.txt honored)",
    )
    args = parser.parse_args(argv)
    if args.cmd == "init-db":
        return cmd_init_db(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
