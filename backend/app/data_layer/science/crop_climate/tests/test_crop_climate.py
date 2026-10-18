"""TW-596 crop-climate lane tests (TW-593 Track 2).

Contract under test:
- schema constraints (split values, trial_design, yield, year, FKs)
- provenance chain walk: gold -> silver -> bronze -> lab -> calibration
- version immutability: gold rows are append-only snapshots
- correction supersession: new version, old version untouched
- toll emission attempted (mocked) with idempotency_key = f"sci:{request_id}"
- split-tag integrity: test_future rows never leak into train queries;
  test_future yields are masked (withheld answers, hashed at release)
"""

import pytest

from app.data_layer.science.crop_climate import models, router, schemas, seed


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def store():
    """In-memory store seeded with one lab, one artifact, one calibration."""
    from app.data_layer.science.auth import hash_key

    s = models.CropClimateStore()
    lab = s.register_lab(
        name="Twistor Synthetic Trials",
        kind="independent",
        verification_status="verified",
    )
    s.api_keys = [
        {
            "id": "key-1",
            "key_hash": hash_key("test-key-crop"),
            "key_prefix": "test-key",
            "label": "test key",
            "lab_id": None,
            "scopes": ["science:crop_climate:write"],
            "rate_limit_per_min": 60,
            "is_active": True,
        }
    ]
    artifact = s.register_artifact(
        lab_id=lab.id,
        storage_path="s3://twistor-sci-bronze/crop_climate/seed_v1.csv",
        content_hash="sha256:seed-fixture",
        collector_signature="sig:seed-fixture",
        schema_version=1,
    )
    calibration = s.register_calibration(
        lab_id=lab.id,
        instrument_id="WS-01",
        instrument_kind="weather_station",
        calibrated_at="2024-01-15T00:00:00+00:00",
        standard_ref="NIST-traceable barometer/thermometer set",
    )
    s.fixtures = {"lab": lab, "artifact": artifact, "calibration": calibration}
    return s


@pytest.fixture()
def seeded_store(store):
    """Store loaded with the 40-record synthetic seed + v1.0.0 gold version."""
    for record in seed.seed_records():
        store.add_measurement(
            artifact_id=store.fixtures["artifact"].id,
            lab_id=store.fixtures["lab"].id,
            # field trials: some rows carry no instrument calibration
            calibration_id=store.fixtures["calibration"].id,
            **record,
        )
    version = store.publish_version(
        dataset_name="twistor-crop-shift",
        semver="1.0.0",
        release_notes="V1 synthetic seed: 40 trials, 2035-like rows withheld",
        benchmark_pin="crop-shift-bench_v1",
    )
    store.fixtures["version"] = version
    return store


def _base_record(**overrides):
    record = {
        "conditions": {"trial_design": "rcbd", "replicates": 4},
        "genotype": "DKC-62-98",
        "location": "Greeley, CO",
        "year": 2024,
        "season": "summer",
        "weather": {"season_precip_mm": 310, "temp_anomaly_c": 0.8},
        "management": {"irrigation": "irrigated", "nitrogen_kg_ha": 180},
        "yield_t_ha": 11.2,
        "synthetic": True,
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------- seed
def test_seed_is_forty_fully_synthetic_records():
    records = seed.seed_records()
    assert len(records) == 40
    assert all(r["synthetic"] is True for r in records)
    assert all("trial_design" in r["conditions"] for r in records)
    assert all(r["weather"] and r["management"] for r in records)
    # spread across genotypes x locations x years 2018-2026
    assert len({r["genotype"] for r in records}) >= 4
    assert len({r["location"] for r in records}) >= 4
    assert min(r["year"] for r in records) >= 2018
    assert max(r["year"] for r in records) <= 2026
    # the benchmark's hidden split is non-vacuous: future-like rows exist
    future_like = [r for r in records if models.default_split(r) == "test_future"]
    train_like = [r for r in records if models.default_split(r) == "train"]
    assert future_like, "seed must contain withheld future-like rows"
    assert train_like, "seed must contain train rows"


# ---------------------------------------------------------------- constraints
def test_conditions_require_trial_design(store):
    fx = store.fixtures
    with pytest.raises(models.StoreError):
        store.add_measurement(
            artifact_id=fx["artifact"].id,
            lab_id=fx["lab"].id,
            calibration_id=None,
            **_base_record(conditions={"replicates": 4}),  # no trial_design
        )


def test_negative_yield_rejected(store):
    fx = store.fixtures
    with pytest.raises(models.StoreError):
        store.add_measurement(
            artifact_id=fx["artifact"].id,
            lab_id=fx["lab"].id,
            calibration_id=None,
            **_base_record(yield_t_ha=-1.0),
        )


def test_year_out_of_range_rejected(store):
    fx = store.fixtures
    with pytest.raises(models.StoreError):
        store.add_measurement(
            artifact_id=fx["artifact"].id,
            lab_id=fx["lab"].id,
            calibration_id=None,
            **_base_record(year=2200),
        )


def test_unknown_artifact_fk_rejected(store):
    fx = store.fixtures
    with pytest.raises(models.StoreError):
        store.add_measurement(
            artifact_id="00000000-0000-0000-0000-000000000000",
            lab_id=fx["lab"].id,
            calibration_id=None,
            **_base_record(),
        )


def test_field_trial_allows_null_calibration(store):
    """Field trials run no calibrated instrument: calibration_id is NULL-able."""
    fx = store.fixtures
    m = store.add_measurement(
        artifact_id=fx["artifact"].id,
        lab_id=fx["lab"].id,
        calibration_id=None,
        **_base_record(),
    )
    assert m.calibration_id is None


def test_gold_split_values_constrained(seeded_store):
    with pytest.raises(models.StoreError):
        seeded_store._insert_gold_row(
            seeded_store.fixtures["version"].id,
            silver_id="dummy",
            split="validation",  # not train | test_future
            genotype="x",
            location="y",
            year=2024,
            yield_t_ha=1.0,
            synthetic=True,
        )


# ---------------------------------------------------------------- provenance
def test_provenance_chain_walk(seeded_store):
    gold_row = seeded_store.query_gold(split="train")[0]
    chain = seeded_store.provenance_chain(gold_row["id"])
    assert chain["gold"]["id"] == gold_row["id"]
    assert chain["silver"]["genotype"] == gold_row["genotype"]
    # bronze: content hash + lab signature present
    assert chain["bronze"]["content_hash"].startswith("sha256:")
    assert chain["bronze"]["collector_signature"]
    # lab identity: verifiable source, not a string
    assert chain["lab"]["name"] == "Twistor Synthetic Trials"
    assert chain["lab"]["verification_status"] == "verified"
    # calibration event -> standard
    assert chain["calibration"]["instrument_id"] == "WS-01"
    assert "NIST" in chain["calibration"]["standard_ref"]


def test_provenance_chain_field_trial_without_calibration(store):
    fx = store.fixtures
    m = store.add_measurement(
        artifact_id=fx["artifact"].id,
        lab_id=fx["lab"].id,
        calibration_id=None,
        **_base_record(),
    )
    store.publish_version("twistor-crop-shift", "1.0.0", "t")
    gold_row = next(
        r for r in store.query_gold(split="train") if r["silver_id"] == m.id
    )
    chain = store.provenance_chain(gold_row["id"])
    assert chain["calibration"] is None  # honest: no instrument, no claim


# ---------------------------------------------------------------- immutability
def test_gold_update_fails(seeded_store):
    gold_row = seeded_store.query_gold(split="train")[0]
    with pytest.raises(models.ImmutableVersionError):
        seeded_store.update_gold(gold_row["id"], {"yield_t_ha": 99.9})


def test_gold_delete_fails(seeded_store):
    gold_row = seeded_store.query_gold(split="train")[0]
    with pytest.raises(models.ImmutableVersionError):
        seeded_store.delete_gold(gold_row["id"])


def test_version_metadata_is_immutable_snapshot(seeded_store):
    version = seeded_store.fixtures["version"]
    assert version.content_root_hash  # merkle root pinned at release
    assert version.row_count == 40
    assert version.supersedes_id is None
    with pytest.raises(models.ImmutableVersionError):
        seeded_store.update_version(version.id, {"release_notes": "rewritten"})


# ---------------------------------------------------------------- corrections
def test_correction_supersedes_without_mutating_history(seeded_store):
    train_rows = seeded_store.query_gold(split="train")
    target = train_rows[0]
    old_version = seeded_store.fixtures["version"]
    old_yield = target["yield_t_ha"]

    new_version = seeded_store.correct_measurement(
        silver_id=target["silver_id"],
        corrected_values={"yield_t_ha": round(old_yield + 0.5, 2)},
        reason="plot-scale recalibration, lab erratum 2026-10-18",
        corrected_by=seeded_store.fixtures["lab"].id,
    )

    # new version chains the old one
    assert new_version.supersedes_id == old_version.id
    assert new_version.semver == "1.0.1"
    # old version rows unchanged — science keeps the historical record
    old_rows = seeded_store.query_gold(version=old_version.semver, split="train")
    old_target = next(r for r in old_rows if r["silver_id"] == target["silver_id"])
    assert old_target["yield_t_ha"] == old_yield
    # new version carries the corrected value
    new_rows = seeded_store.query_gold(version=new_version.semver, split="train")
    new_target = next(r for r in new_rows if r["genotype"] == target["genotype"]
                      and r["location"] == target["location"]
                      and r["year"] == target["year"])
    assert new_target["yield_t_ha"] == round(old_yield + 0.5, 2)
    # correction is recorded on the feed
    feed = seeded_store.list_corrections(dataset_name="twistor-crop-shift")
    assert any(c["reason"].startswith("plot-scale") for c in feed)


# ---------------------------------------------------------------- tolls
def test_toll_emission_attempted_with_idempotency_key(monkeypatch):
    calls = []

    def fake_record(db, org_id, event_type, quantity=1, idempotency_key=None,
                    source_type=None, source_id=None, amount_cents=None):
        calls.append({
            "org_id": org_id,
            "event_type": event_type,
            "quantity": quantity,
            "idempotency_key": idempotency_key,
            "source_type": source_type,
            "source_id": source_id,
        })
        return {"id": "toll-1"}

    monkeypatch.setattr(
        "app.services.toll_service.record_toll_event", fake_record
    )
    request_id = "req-abc-123"
    for event_type in (
        "data.science.query",
        "data.science.bulk",
        "data.science.ingest",
        "data.science.benchmark",
    ):
        router._toll_best_effort(request_id, event_type)
    assert len(calls) == 4
    assert {c["event_type"] for c in calls} == {
        "data.science.query",
        "data.science.bulk",
        "data.science.ingest",
        "data.science.benchmark",
    }
    assert all(c["idempotency_key"] == f"sci:{request_id}" for c in calls)


def test_toll_unknown_event_type_does_not_break_read(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise ValueError("Unknown toll event type: data.science.nope")

    monkeypatch.setattr("app.services.toll_service.record_toll_event", boom)
    # must log and continue, never raise (TW-208: tolls never break reads)
    assert router._toll_best_effort("req-x", "data.science.nope") is None
    assert "toll" in caplog.text.lower()


# ---------------------------------------------------------------- split tags
def test_train_queries_never_leak_test_future_rows(seeded_store):
    train_rows = seeded_store.query_gold(split="train")
    assert train_rows
    assert all(r["split"] == "train" for r in train_rows)
    assert all(r["yield_t_ha"] is not None for r in train_rows)  # full rows


def test_test_future_yields_are_masked(seeded_store):
    future_rows = seeded_store.query_gold(split="test_future")
    assert future_rows
    assert all(r["split"] == "test_future" for r in future_rows)
    # withheld answers: served masked, never the raw yield
    assert all(r["yield_t_ha"] is None for r in future_rows)
    assert all(r["yield_withheld"] is True for r in future_rows)


def test_default_query_split_is_train(seeded_store):
    rows = seeded_store.query_gold()
    assert rows
    assert all(r["split"] == "train" for r in rows)


def test_hidden_split_hash_matches_withheld_answers(seeded_store):
    version = seeded_store.fixtures["version"]
    benchmark = seeded_store.get_benchmark("crop-shift-bench_v1")
    assert benchmark["hidden_split_hash"]
    # recompute over the true withheld answers: must match the pinned hash
    assert benchmark["hidden_split_hash"] == seeded_store._hidden_split_hash(version.id)


def test_query_filters_narrow_results(seeded_store):
    by_genotype = seeded_store.query_gold(genotype="DKC-62-98")
    assert by_genotype
    assert all(r["genotype"] == "DKC-62-98" for r in by_genotype)
    by_year = seeded_store.query_gold(year=2024)
    assert all(r["year"] == 2024 for r in by_year)
    combo = seeded_store.query_gold(
        genotype="AG-38X9", location="Ames, IA", year=2026
    )
    for r in combo:
        assert (r["genotype"], r["location"], r["year"]) == ("AG-38X9", "Ames, IA", 2026)


# ---------------------------------------------------------------- schemas
def test_measurement_create_validates_conditions():
    with pytest.raises(Exception):
        schemas.MeasurementCreate(
            artifact_id="a",
            lab_id="b",
            conditions={"replicates": 4},  # missing trial_design
            genotype="g",
            location="l",
            year=2024,
            weather={},
            management={},
            yield_t_ha=5.0,
        )


def test_response_envelope_carries_provenance_and_synthetic(seeded_store):
    version = seeded_store.fixtures["version"]
    envelope = router._dataset_envelope(seeded_store, version, data=[])
    assert envelope["as_of"]["semver"] == "1.0.0"
    assert envelope["provenance"]["lab_count"] >= 1
    assert "attestation_summary" in envelope["provenance"]
    assert envelope["synthetic"] is True  # V1 seed is synthetic, labeled


# ---------------------------------------------------------------- router API
def _test_client(seeded_store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[router.get_store] = lambda: seeded_store
    return TestClient(app)


def test_api_query_defaults_to_train_no_leak(seeded_store):
    client = _test_client(seeded_store)
    resp = client.get("/data/v1/science/crop_climate/query")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert rows
    assert all(r["split"] == "train" for r in rows)
    assert all(r["yield_t_ha"] is not None for r in rows)


def test_api_test_future_masks_yields(seeded_store):
    client = _test_client(seeded_store)
    resp = client.get(
        "/data/v1/science/crop_climate/query", params={"split": "test_future"}
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert rows
    assert all(r["yield_t_ha"] is None and r["yield_withheld"] for r in rows)


def test_api_benchmarks_carry_hidden_split_hash(seeded_store):
    client = _test_client(seeded_store)
    resp = client.get("/data/v1/science/crop_climate/benchmarks")
    assert resp.status_code == 200
    benchmarks = resp.json()["data"]
    bench = next(b for b in benchmarks if b["name"] == "crop-shift-bench_v1")
    assert bench["hidden_split_hash"]
    assert bench["status"] == "open"
    assert bench["metric"] == "yield_mae_t_ha"


def test_api_ingest_quarantines_bad_records(seeded_store):
    """Self-healing (TW-208): one bad record -> dead_letters, never kills the run."""
    client = _test_client(seeded_store)
    fx = seeded_store.fixtures
    payload = {
        "lab_id": fx["lab"].id,
        "collector_signature": "sig:test",
        "records": [
            {
                "artifact_id": fx["artifact"].id,
                "lab_id": fx["lab"].id,
                "conditions": {"trial_design": "rcbd", "replicates": 4},
                "genotype": "TEST-1",
                "location": "Test, CO",
                "year": 2025,
                "weather": {},
                "management": {},
                "yield_t_ha": 8.0,
            },
            {
                "artifact_id": fx["artifact"].id,
                "lab_id": fx["lab"].id,
                "conditions": {"replicates": 4},  # bad: no trial_design
                "genotype": "TEST-2",
                "location": "Test, CO",
                "year": 2025,
                "weather": {},
                "management": {},
                "yield_t_ha": 8.0,
            },
        ],
    }
    resp = client.post("/data/v1/science/crop_climate/ingest", json=payload,
                       headers={"X-API-Key": "test-key-crop"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert len(body["dead_letters"]) == 1


def test_api_ingest_requires_api_key(seeded_store):
    client = _test_client(seeded_store)
    fx = seeded_store.fixtures
    resp = client.post("/data/v1/science/crop_climate/ingest",
                       json={"lab_id": fx["lab"].id,
                             "collector_signature": "sig:test",
                             "records": []})
    assert resp.status_code == 401
