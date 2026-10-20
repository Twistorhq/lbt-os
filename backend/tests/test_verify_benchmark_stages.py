"""TW-649: stage adapter unit tests for the pure-stdlib pieces.

The heavy model libraries (dlib, PaddleOCR, MediaPipe, ...) are not
installed in the test environment, and that is itself under test here:
probes must report unavailable honestly and adapters must raise
StageUnavailable, never degrade silently to a weaker check.
"""

from __future__ import annotations

import pytest

from app.verify.benchmark.documents import mrz_check_digit
from app.verify.benchmark.stages import (
    STAGES,
    FaceMatchStage,
    StageUnavailable,
    distance_to_similarity,
    parse_aamva,
    parse_mrz_lines,
    probe_stages,
)


def _td3_line2() -> str:
    doc = "P12345678"
    dob, expiry, personal = "520727", "301231", "A12345<<<<<<<<"
    line = (
        doc + str(mrz_check_digit(doc))
        + "UTO"
        + dob + str(mrz_check_digit(dob))
        + "M"
        + expiry + str(mrz_check_digit(expiry))
        + personal + str(mrz_check_digit(personal))
    )
    composite_src = line[0:10] + line[13:20] + line[21:28] + line[28:43]
    return line + str(mrz_check_digit(composite_src))


def test_distance_to_similarity_clamps():
    assert distance_to_similarity(0.0) == pytest.approx(1.0)
    assert distance_to_similarity(0.45) == pytest.approx(0.55)
    assert distance_to_similarity(1.7) == pytest.approx(0.0)


def test_parse_aamva_extracts_named_fields():
    payload = "DCSOKAFOR\nDACZEKE\nDAQX1234567\nDBB01011990\nZZZIGNOREME"
    fields = parse_aamva(payload)
    assert fields["surname"] == "OKAFOR"
    assert fields["given_names"] == "ZEKE"
    assert fields["doc_number"] == "X1234567"
    assert fields["dob"] == "01011990"
    assert "ZZZIGNOREME" not in fields.values()


def test_parse_mrz_lines_round_trip_and_corruption():
    line1 = "P<UTOOKAFOR<<ZEKE<<<<<<<<<<<<<<<<<<<<<<<<<<<<<"
    parsed = parse_mrz_lines([line1, _td3_line2()])
    assert parsed["parsed"] is True
    assert parsed["fields"]["doc_number"] == "P12345678"
    assert all(parsed["valid"].values())

    bad = _td3_line2()
    bad = bad[:15] + ("0" if bad[15] != "0" else "1") + bad[16:]
    reparsed = parse_mrz_lines([line1, bad])
    assert reparsed["valid"]["dob"] is False


def test_probe_stages_reports_every_stage_without_raising():
    statuses = probe_stages()
    assert {s.name for s in statuses} == {s.name for s in STAGES}
    for status in statuses:
        assert status.component and status.license


def test_unavailable_stage_raises_not_degrades():
    stage = FaceMatchStage()
    if stage.probe().available:
        pytest.skip("face_recognition installed in this environment")
    with pytest.raises(StageUnavailable):
        stage.similarity("a.jpg", "b.jpg")
