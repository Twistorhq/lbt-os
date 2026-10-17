"""RED: SQLite corpus store — schema, dedupe, run logging."""
import os

import pytest

from research.harvester.store import CorpusStore


@pytest.fixture()
def store(tmp_path):
    db = str(tmp_path / "corpus.db")
    s = CorpusStore(db)
    s.init_schema()
    yield s
    s.close()


def doc(external_id="x1", **kw):
    d = dict(
        source_key="crossref",
        external_id=external_id,
        title="Hemp composites",
        authors=["Ada Okafor"],
        institution=None,
        year=2021,
        doi="10.1234/hemp1",
        doc_type="journal-article",
        abstract="abstract",
        source_url="https://example.com/x1",
        fulltext_url=None,
        license="cc-by",
        access_status="open",
        origin="harvested",
    )
    d.update(kw)
    return d


class TestStore:
    def test_insert_and_get(self, store):
        doc_id = store.insert_document(doc())
        got = store.get_document(doc_id)
        assert got["title"] == "Hemp composites"
        assert got["authors"] == ["Ada Okafor"]
        assert got["origin"] == "harvested"

    def test_dedupe_on_external_id(self, store):
        first = store.insert_document(doc(external_id="dup"))
        second = store.insert_document(doc(external_id="dup", title="Changed"))
        assert first == second
        assert store.count_documents() == 1

    def test_dedupe_on_doi(self, store):
        store.insert_document(doc(external_id="a", doi="10.1234/same"))
        store.insert_document(doc(external_id="b", doi="10.1234/same"))
        assert store.count_documents() == 1

    def test_distinct_dois_both_stored(self, store):
        store.insert_document(doc(external_id="a", doi="10.1234/one"))
        store.insert_document(doc(external_id="b", doi="10.1234/two"))
        assert store.count_documents() == 2

    def test_null_doi_does_not_collapse(self, store):
        store.insert_document(doc(external_id="a", doi=None))
        store.insert_document(doc(external_id="b", doi=None))
        assert store.count_documents() == 2

    def test_property_records(self, store):
        doc_id = store.insert_document(doc())
        store.insert_property(doc_id, dict(
            material="hemp/epoxy", property="tensile_strength", value=45.2,
            unit="MPa", test_method="ASTM D638", context="30 wt% fiber",
            extraction_method="regex-heuristic", confidence=0.6))
        props = store.properties_for_document(doc_id)
        assert len(props) == 1
        assert props[0]["value"] == 45.2

    def test_harvest_run_logging(self, store):
        run_id = store.start_run("crossref", "hemp fiber composite")
        store.finish_run(run_id, status="ok", records_found=10,
                         records_stored=8, notes="seed")
        run = store.get_run(run_id)
        assert run["status"] == "ok"
        assert run["records_stored"] == 8

    def test_schema_is_idempotent(self, store, tmp_path):
        # init twice must not blow up
        store.init_schema()
        assert store.count_documents() == 0
