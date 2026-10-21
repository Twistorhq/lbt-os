"""SQLite corpus store: sources, harvest runs, documents, property records.

Dedupe: external_id is unique; DOI dedupes when present (NULL DOIs never
collapse). Every document carries provenance (source URL, retrieval time,
license, access status). `origin` defaults to 'harvested' so lab-contributed
rows can join later without migration.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    adapter TEXT NOT NULL,
    base_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harvest_runs (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    query TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    records_found INTEGER NOT NULL DEFAULT 0,
    records_stored INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    run_id INTEGER,
    external_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    authors_json TEXT NOT NULL DEFAULT '[]',
    institution TEXT,
    year INTEGER,
    doi TEXT,
    doc_type TEXT,
    abstract TEXT,
    source_url TEXT,
    fulltext_url TEXT,
    fulltext_path TEXT,
    license TEXT,
    access_status TEXT,
    origin TEXT NOT NULL DEFAULT 'harvested',
    retrieved_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_doi
    ON documents(doi) WHERE doi IS NOT NULL;
CREATE TABLE IF NOT EXISTS property_records (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    material TEXT,
    property TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    test_method TEXT,
    context TEXT,
    extraction_method TEXT NOT NULL,
    confidence REAL NOT NULL
);
-- Paywall hit-list (Justynn's standing rule, 2026-10-16): valuable sources
-- we cannot legally fetch come back to him for buy decisions. Never silently
-- skipped.
CREATE TABLE IF NOT EXISTS paywall_hits (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    run_id INTEGER,
    external_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    authors_json TEXT NOT NULL DEFAULT '[]',
    year INTEGER,
    doi TEXT,
    doc_type TEXT,
    source_url TEXT,
    what_it_contains TEXT,
    why_valuable TEXT,
    access_cost TEXT,
    detected_at TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class CorpusStore:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self._conn.close()

    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- documents ------------------------------------------------------
    @staticmethod
    def _norm_doi(doi: str | None) -> str | None:
        # N2: DOI dedupe is case-insensitive and whitespace-tolerant.
        if doi is None:
            return None
        doi = doi.strip().lower()
        return doi or None

    def insert_document(self, doc: dict) -> tuple[int, bool]:
        """Insert or return existing id (dedupe on external_id, then DOI).

        Returns (doc_id, is_new). Callers must only run extraction for new
        rows — re-running extraction on deduped docs duplicates properties.
        """
        row = self._conn.execute(
            "SELECT id FROM documents WHERE external_id = ?",
            (doc["external_id"],),
        ).fetchone()
        if row:
            return int(row["id"]), False
        doi = self._norm_doi(doc.get("doi"))
        if doi:
            row = self._conn.execute(
                "SELECT id FROM documents WHERE doi = ?", (doi,)
            ).fetchone()
            if row:
                return int(row["id"]), False
        cur = self._conn.execute(
            """INSERT INTO documents
               (source_key, run_id, external_id, title, authors_json,
                institution, year, doi, doc_type, abstract, source_url,
                fulltext_url, fulltext_path, license, access_status,
                origin, retrieved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doc.get("source_key"),
                doc.get("run_id"),
                doc["external_id"],
                doc.get("title", ""),
                json.dumps(doc.get("authors", [])),
                doc.get("institution"),
                doc.get("year"),
                doi,
                doc.get("doc_type"),
                doc.get("abstract"),
                doc.get("source_url"),
                doc.get("fulltext_url"),
                doc.get("fulltext_path"),
                doc.get("license"),
                doc.get("access_status"),
                doc.get("origin", "harvested"),
                _utcnow(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid), True

    def set_fulltext_path(self, doc_id: int, path: str) -> None:
        """Record where a fetched PDF was persisted (B1)."""
        self._conn.execute(
            "UPDATE documents SET fulltext_path = ? WHERE id = ?",
            (path, doc_id),
        )
        self._conn.commit()

    def get_document(self, doc_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["authors"] = json.loads(d["authors_json"])
        return d

    def count_documents(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        return int(row["n"])

    def insert_property(self, document_id: int, prop: dict) -> int:
        cur = self._conn.execute(
            """INSERT INTO property_records
               (document_id, material, property, value, unit, test_method,
                context, extraction_method, confidence)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                prop.get("material"),
                prop["property"],
                prop["value"],
                prop["unit"],
                prop.get("test_method"),
                prop.get("context"),
                prop["extraction_method"],
                prop["confidence"],
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def properties_for_document(self, document_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM property_records WHERE document_id = ?",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- paywall hit-list ------------------------------------------------
    def log_paywall_hit(self, hit: dict) -> int | None:
        """Record a valuable paywalled source. Dedupes on external_id.
        Returns the row id, or None if already recorded."""
        row = self._conn.execute(
            "SELECT id FROM paywall_hits WHERE external_id = ?",
            (hit["external_id"],),
        ).fetchone()
        if row:
            return None
        cur = self._conn.execute(
            """INSERT INTO paywall_hits
               (source_key, run_id, external_id, title, authors_json, year,
                doi, doc_type, source_url, what_it_contains, why_valuable,
                access_cost, detected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                hit.get("source_key"),
                hit.get("run_id"),
                hit["external_id"],
                hit.get("title", ""),
                json.dumps(hit.get("authors", [])),
                hit.get("year"),
                hit.get("doi"),
                hit.get("doc_type"),
                hit.get("source_url"),
                hit.get("what_it_contains"),
                hit.get("why_valuable"),
                hit.get("access_cost"),
                _utcnow(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list_paywall_hits(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM paywall_hits ORDER BY detected_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["authors"] = json.loads(d["authors_json"])
            out.append(d)
        return out

    # -- runs -----------------------------------------------------------
    def start_run(self, source_key: str, query: str) -> int:
        cur = self._conn.execute(
            """INSERT INTO harvest_runs
               (source_key, query, started_at, status)
               VALUES (?,?,?, 'running')""",
            (source_key, query, _utcnow()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        records_found: int = 0,
        records_stored: int = 0,
        notes: str | None = None,
    ) -> None:
        self._conn.execute(
            """UPDATE harvest_runs SET finished_at = ?, status = ?,
               records_found = ?, records_stored = ?, notes = ?
               WHERE id = ?""",
            (_utcnow(), status, records_found, records_stored, notes, run_id),
        )
        self._conn.commit()

    def get_run(self, run_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM harvest_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None
