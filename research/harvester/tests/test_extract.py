"""RED: metadata normalization and heuristic property extraction."""
import pytest

from research.harvester.extract import (
    extract_properties,
    normalize_authors,
    normalize_year,
)


class TestNormalizeAuthors:
    def test_crossref_style_list(self):
        people = [{"given": "Ada", "family": "Okafor"}, {"given": "B.", "family": "Reyes"}]
        assert normalize_authors(people) == ["Ada Okafor", "B. Reyes"]

    def test_plain_string_list(self):
        assert normalize_authors(["Ada Okafor", "B. Reyes"]) == ["Ada Okafor", "B. Reyes"]

    def test_single_string_splits_on_semicolon(self):
        assert normalize_authors("Ada Okafor; B. Reyes") == ["Ada Okafor", "B. Reyes"]

    def test_none_and_empty(self):
        assert normalize_authors(None) == []
        assert normalize_authors([]) == []

    def test_missing_given(self):
        assert normalize_authors([{"family": "Okafor"}]) == ["Okafor"]


class TestNormalizeYear:
    def test_int(self):
        assert normalize_year(2021) == 2021

    def test_date_string(self):
        assert normalize_year("2019-06-15") == 2019

    def test_crossref_date_parts(self):
        assert normalize_year([[2020, 3, 1]]) == 2020

    def test_garbage(self):
        assert normalize_year("n.d.") is None
        assert normalize_year(None) is None


SAMPLE_ABSTRACT = (
    "Hemp fiber reinforced epoxy composites were prepared with 30 wt% fiber. "
    "The tensile strength reached 45.2 MPa and tensile modulus 2.1 GPa. "
    "Flexural strength was 78 MPa with a flexural modulus of 3.4 GPa. "
    "Impact strength measured 12.5 kJ/m2 and elongation at break 3.2%."
)


class TestExtractProperties:
    def test_tensile_strength_mpa(self):
        recs = extract_properties(SAMPLE_ABSTRACT)
        hit = [r for r in recs if r["property"] == "tensile_strength"]
        assert hit and hit[0]["value"] == pytest.approx(45.2)
        assert hit[0]["unit"] == "MPa"

    def test_gpa_normalized_to_mpa(self):
        recs = extract_properties(SAMPLE_ABSTRACT)
        hit = [r for r in recs if r["property"] == "tensile_modulus"]
        assert hit and hit[0]["value"] == pytest.approx(2100.0)
        assert hit[0]["unit"] == "MPa"

    def test_flexural_properties(self):
        recs = extract_properties(SAMPLE_ABSTRACT)
        props = {r["property"] for r in recs}
        assert "flexural_strength" in props
        assert "flexural_modulus" in props

    def test_impact_and_elongation(self):
        recs = extract_properties(SAMPLE_ABSTRACT)
        by_prop = {r["property"]: r for r in recs}
        assert by_prop["impact_strength"]["value"] == pytest.approx(12.5)
        assert by_prop["elongation_at_break"]["value"] == pytest.approx(3.2)
        assert by_prop["elongation_at_break"]["unit"] == "%"

    def test_extraction_method_labeled(self):
        recs = extract_properties(SAMPLE_ABSTRACT)
        assert recs
        for r in recs:
            assert r["extraction_method"] == "regex-heuristic"
            assert 0 < r["confidence"] <= 1.0

    def test_no_properties_empty(self):
        assert extract_properties("This paper discusses policy implications.") == []

    def test_none_safe(self):
        assert extract_properties(None) == []
