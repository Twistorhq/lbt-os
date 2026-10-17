"""RED: adapter normalization — Crossref JSON and OAI-PMH XML fixtures."""

import json
import os

from research.harvester.adapters.crossref import CrossrefAdapter
from research.harvester.adapters.oai_pmh import OaiPmhAdapter
from research.harvester.adapters.openalex import OpenAlexAdapter

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class TestCrossrefAdapter:
    def test_normalize_work(self):
        work = json.loads(load("crossref_work.json"))
        rec = CrossrefAdapter.normalize(work)
        assert rec.external_id == "10.1016/j.compositesa.2021.106543"
        assert rec.title == "Hemp fibre reinforced composites: a review"
        assert rec.authors == ["Adaeze Okafor", "B. Reyes"]
        assert rec.year == 2021
        assert rec.doi == "10.1016/j.compositesa.2021.106543"
        assert rec.license == "cc-by"
        assert rec.source_url.startswith("https://doi.org/")

    def test_missing_fields_do_not_crash(self):
        rec = CrossrefAdapter.normalize({"DOI": "10.1/x", "title": []})
        assert rec.title == ""
        assert rec.authors == []
        assert rec.year is None


class TestOaiPmhAdapter:
    def test_parse_list_records(self):
        xml = load("oai_listrecords.xml")
        records, token = OaiPmhAdapter.parse_records(xml)
        assert len(records) == 1  # the deleted record is skipped
        first = records[0]
        assert first.external_id == "oai:krex.k-state.edu:123456789/42"
        assert "hemp" in first.title.lower()
        assert first.doc_type == "dissertation"
        assert token == "resumption-abc-123"

    def test_deleted_record_skipped(self):
        xml = load("oai_listrecords.xml")
        records, _ = OaiPmhAdapter.parse_records(xml)
        ids = [r.external_id for r in records]
        assert not any("deleted" in i for i in ids)

    def test_pdf_identifier_becomes_fulltext_url(self):
        # M2: a .pdf dc:identifier populates fulltext_url so fetch_fulltext
        # is a live path, not a dead one.
        xml = load("oai_listrecords.xml").replace(
            "<dc:identifier>https://krex.k-state.edu/handle/123456789/42</dc:identifier>",
            "<dc:identifier>https://krex.k-state.edu/handle/123456789/42</dc:identifier>"
            "<dc:identifier>https://krex.k-state.edu/bitstream/123456789/42/1/thesis.pdf</dc:identifier>",
        )
        records, _ = OaiPmhAdapter.parse_records(xml)
        assert (
            records[0].fulltext_url
            == "https://krex.k-state.edu/bitstream/123456789/42/1/thesis.pdf"
        )


class TestOaiMatchTerms:
    def test_and_semantics(self):
        assert (
            OaiPmhAdapter.matches("Hemp fiber biocomposites", ["hemp", "fiber"]) is True
        )
        assert (
            OaiPmhAdapter.matches("Hemp fiber biocomposites", ["hemp", "titanium"])
            is False
        )

    def test_case_insensitive(self):
        assert OaiPmhAdapter.matches("HEMP Fiber", ["hemp"]) is True

    def test_distinctive_term_only(self):
        # Generic words must not admit irrelevant records.
        assert OaiPmhAdapter.matches("Carbon nanotube composite", ["hemp"]) is False


class TestOpenAlexDegrade:
    def test_429_returns_empty_with_note(self, monkeypatch):
        # N3: no test seam in the production signature — monkeypatch instead.
        class FakeResp:
            status_code = 429

        adapter = OpenAlexAdapter()
        monkeypatch.setattr(adapter.session, "get", lambda *a, **k: FakeResp())
        monkeypatch.setattr(adapter.limiter, "wait_and_acquire", lambda host: None)
        records, note = adapter.discover("hemp composite")
        assert records == []
        assert "rate" in note.lower() or "429" in note
