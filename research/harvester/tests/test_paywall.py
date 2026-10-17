"""Paywall hit-list: valuable closed sources are recorded, never skipped."""

import pytest

from research.harvester.cli import is_paywall_candidate, paywall_hit_from
from research.harvester.models import RawRecord
from research.harvester.store import CorpusStore


def rec(**kw):
    base = dict(
        external_id="10.1/closed1",
        title="Hemp composites under load",
        authors=["Ada Okafor"],
        year=2023,
        doi="10.1/closed1",
        doc_type="journal-article",
        abstract="Tensile strength 55 MPa.",
        source_url="https://doi.org/10.1/closed1",
        fulltext_url=None,
        license=None,
        access_status="unknown",
    )
    base.update(kw)
    return RawRecord(**base)


@pytest.fixture()
def store(tmp_path):
    s = CorpusStore(str(tmp_path / "corpus.db"))
    s.init_schema()
    yield s
    s.close()


class TestPaywallCandidate:
    def test_closed_valuable_is_candidate(self):
        assert is_paywall_candidate(rec(), "hemp fiber composite") is True

    def test_open_fulltext_not_candidate(self):
        assert (
            is_paywall_candidate(rec(fulltext_url="https://x/y.pdf"), "hemp") is False
        )

    def test_open_license_not_candidate(self):
        assert (
            is_paywall_candidate(rec(access_status="open", license="cc-by"), "hemp")
            is False
        )

    def test_wrong_doc_type_not_candidate(self):
        assert is_paywall_candidate(rec(doc_type="blog-post"), "hemp") is False

    def test_openalex_article_type_is_candidate(self):
        # B3: OpenAlex labels valuable closed records "article".
        assert is_paywall_candidate(rec(doc_type="article"), "hemp") is True

    def test_openaire_publication_type_is_candidate(self):
        # B3: OpenAIRE uses "publication".
        assert is_paywall_candidate(rec(doc_type="publication"), "hemp") is True

    def test_no_abstract_or_title_not_candidate(self):
        assert is_paywall_candidate(rec(abstract=None, title=""), "hemp") is False


class TestPaywallHitList:
    def test_log_and_list(self, store):
        hit_id = store.log_paywall_hit(
            paywall_hit_from(rec(), "crossref", 1, "hemp fiber composite")
        )
        assert hit_id is not None
        hits = store.list_paywall_hits()
        assert len(hits) == 1
        assert hits[0]["title"] == "Hemp composites under load"
        assert "no open fulltext" in hits[0]["why_valuable"]
        assert hits[0]["what_it_contains"] == "Tensile strength 55 MPa."

    def test_dedupe(self, store):
        hit = paywall_hit_from(rec(), "crossref", 1, "hemp")
        assert store.log_paywall_hit(hit) is not None
        assert store.log_paywall_hit(hit) is None
        assert len(store.list_paywall_hits()) == 1
