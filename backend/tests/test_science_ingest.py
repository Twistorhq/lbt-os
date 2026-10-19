# Prepared by Twistor Holdings LLC
"""TW-612 tests: license verdicts, bronze writer, lane adapters.

Tests first for data logic: constraints, edge cases, rollback behavior.
No network in unit tests: the BronzeWriter takes an injectable fetcher.
"""

import pytest

from app.data_layer.science.ingest import carbon, crops, s2s
from app.data_layer.science.ingest import licenses as L
from app.data_layer.science.ingest.bronze import (
    BronzeWriter,
    IngestItem,
    hash_pii_metadata,
    write_run_manifest,
)


# ---------------------------------------------------------------- licenses
class TestLicenseVerdicts:
    def test_green_promotes_to_gold(self):
        assert L.can_promote_to_gold(L.KNOWN_LICENSES["gefs-reforecast"]) is True
        assert L.can_promote_to_gold(L.KNOWN_LICENSES["g2f"]) is True
        assert L.can_promote_to_gold(L.KNOWN_LICENSES["ameriflux"]) is True

    def test_yellow_and_red_quarantined(self):
        assert L.can_promote_to_gold(L.KNOWN_LICENSES["wosis-restricted"]) is False
        assert L.can_promote_to_gold(L.KNOWN_LICENSES["ai-weather-quest"]) is False
        assert L.can_promote_to_gold(L.KNOWN_LICENSES["agront"]) is False
        assert L.can_promote_to_gold(L.KNOWN_LICENSES["registry-crosswalk"]) is False

    def test_assert_gold_eligible_raises_for_red(self):
        with pytest.raises(L.LicenseQuarantineError):
            L.assert_gold_eligible(L.KNOWN_LICENSES["s2s-db-ccbync"], context="test")

    def test_assert_gold_eligible_passes_for_green(self):
        L.assert_gold_eligible(L.KNOWN_LICENSES["ssurgo"])  # no raise

    def test_tag_requires_verdict_and_source(self):
        with pytest.raises(ValueError):
            L.LicenseTag(tag="X", verdict="maybe", source_url="https://x")
        with pytest.raises(ValueError):
            L.LicenseTag(tag="", verdict="green", source_url="https://x")
        with pytest.raises(ValueError):
            L.LicenseTag(tag="CC0-1.0", verdict="green", source_url="")

    def test_s2s_nc_centers_are_red(self):
        red = L.KNOWN_LICENSES["s2s-db-ccbync"]
        assert red.verdict == "red"
        assert "JMA" in red.notes

    def test_registry_has_all_wave1_sources(self):
        for key in (
            "gefs-reforecast",
            "20crv3",
            "era5-wb2",
            "s2s-db-ccby",
            "s2s-db-ccbync",
            "ai-weather-quest",
            "wb2-ifs-baseline",
            "g2f",
            "grin-genesys",
            "must-c",
            "hemp-missouri-ncstate",
            "hemp-uvm-osu-pdf",
            "agront",
            "wosis-public",
            "wosis-untiered",
            "wosis-restricted",
            "ameriflux",
            "gedi",
            "esa-cci-biomass",
            "ssurgo",
            "registry-crosswalk",
        ):
            assert key in L.KNOWN_LICENSES, f"missing license registration: {key}"


# ---------------------------------------------------------------- bronze writer (fake db + fake fetcher)
class FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self._filters = []
        self._row = None

    def select(self, *a):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def limit(self, n):
        return self

    def single(self):
        self._single = True
        return self

    def insert(self, row):
        row = dict(row)
        row.setdefault("id", f"artifact-{len(self.db.rows[self.name])}")
        self.db.rows[self.name].append(row)
        self._inserted = [row]
        return self

    def execute(self):
        if getattr(self, "_inserted", None) is not None:
            data, self._inserted = self._inserted, None
            return type("R", (), {"data": data})()
        rows = self.db.rows[self.name]
        for col, val in self._filters:
            rows = [r for r in rows if r.get(col) == val]
        if getattr(self, "_single", False):
            return type("R", (), {"data": rows[0] if rows else None})()
        return type("R", (), {"data": rows})()


class FakeDB:
    def __init__(self):
        self.rows = {"sci_bronze_artifacts": []}

    def table(self, name):
        return FakeTable(self, name)


def _item(**kw):
    base = dict(
        source_key="gefs-reforecast",
        remote_url="https://example.com/file.grib2",
        filename="gefs/test.grib2",
        license=L.KNOWN_LICENSES["gefs-reforecast"],
    )
    base.update(kw)
    return IngestItem(**base)


class TestBronzeWriter:
    def test_write_hashes_and_tags_license(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"fake-grib-bytes"),
        )
        res = w.write(_item())
        assert res.artifact_id is not None
        assert res.error is None
        row = db.rows["sci_bronze_artifacts"][0]
        assert row["license_tag"] == "US-PD"
        assert row["license_verdict"] == "green"
        assert len(row["content_hash"]) == 64

    def test_duplicate_content_hash_skipped(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"same-bytes"),
        )
        r1 = w.write(_item(filename="a.grib2"))
        r2 = w.write(_item(filename="b.grib2"))
        assert r2.skipped_duplicate is True
        assert r2.artifact_id == r1.artifact_id
        assert len(db.rows["sci_bronze_artifacts"]) == 1

    def test_item_failure_does_not_raise(self, tmp_path):
        db = FakeDB()

        def boom(url, dest):
            raise ConnectionError("network down")

        w = BronzeWriter(str(tmp_path), db, "lab-1", "subseasonal", fetcher=boom)
        res = w.write(_item())
        assert res.artifact_id is None
        assert "network down" in res.error

    def test_gold_gate_refuses_red_artifact(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "carbon",
            fetcher=lambda url, dest: open(dest, "wb").write(b"x"),
        )
        res = w.write(
            _item(
                source_key="wosis-restricted",
                license=L.KNOWN_LICENSES["wosis-restricted"],
            )
        )
        with pytest.raises(L.LicenseQuarantineError):
            w.gold_gate(res.artifact_id)

    def test_gold_gate_passes_green_artifact(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"x"),
        )
        res = w.write(_item())
        tag = w.gold_gate(res.artifact_id)
        assert tag.verdict == "green"

    def test_run_manifest(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"x"),
        )
        results = w.write_all([_item(filename="a"), _item(filename="b")])
        mp = str(tmp_path / "manifest.jsonl")
        write_run_manifest(mp, "subseasonal", results)
        import json

        line = json.loads(open(mp).readline())
        assert line["fetched"] == 1  # second is a duplicate
        assert line["skipped_duplicates"] == 1


# ---------------------------------------------------------------- S2S adapters
class TestS2SAdapters:
    def test_gefs_plan_shape(self):
        items = s2s.plan_gefs(["2000010100"])
        assert len(items) == 1 * 3 * 2  # 1 date x 3 vars x 2 day-windows
        it = items[0]
        assert "noaa-gefs-retrospective.s3.amazonaws.com" in it.remote_url
        assert it.remote_url.endswith(".grib2")
        assert it.license.verdict == "green"
        assert it.metadata["center"] == "NCEP"

    def test_20crv3_plan_shape(self):
        items = s2s.plan_20crv3([1950])
        assert len(items) == 3  # 3 vars
        assert items[0].remote_url.endswith("TMP_1950.tar")
        assert items[0].license.verdict == "green"

    def test_era5_wb2_manifest_plan(self):
        items = s2s.plan_era5_wb2()
        assert len(items) == 3
        assert "weatherbench2" in items[0].metadata["zarr_store"]
        assert items[0].metadata["truth_lag_days"] == 5
        assert any(
            "modelled, not assimilated" in c for c in items[0].metadata["caveats"]
        )

    def test_era5_zarr_url(self):
        assert s2s.era5_zarr_url().startswith("gs://weatherbench2/")
        assert "2m_temperature" in s2s.era5_zarr_url("2m_temperature")

    def test_s2s_nc_center_rejected(self):
        with pytest.raises(ValueError, match="RED"):
            s2s.plan_s2s_database(["JMA"])
        with pytest.raises(ValueError, match="RED"):
            s2s.plan_s2s_database(["ECMWF", "BoM"])

    def test_s2s_whitelist_ok(self):
        items = s2s.plan_s2s_database()
        assert len(items) == 7
        assert all(i.license.verdict == "green" for i in items)

    def test_s2s_unknown_center_rejected(self):
        with pytest.raises(ValueError, match="unknown"):
            s2s.plan_s2s_database(["NOAA-FAKE"])

    def test_yellow_checks_documented(self):
        assert s2s.check_ai_weather_quest()["status"] == "check_required"
        assert s2s.check_wb2_ifs_baselines()["status"] == "check_required"


# ---------------------------------------------------------------- crops adapters
class TestCropsAdapters:
    def test_g2f_figshare_no_auth(self):
        items = crops.plan_g2f_figshare()
        assert len(items) == 1
        assert items[0].license.tag == "CC0-1.0"

    def test_g2f_cyverse_marks_account_required(self):
        items = crops.plan_g2f_cyverse([2022])
        assert "CyVerse account required" in items[0].metadata["note"]

    def test_agront_excluded_from_gold(self):
        assert crops.agront_excluded()["status"] == "excluded_from_gold"
        assert not L.can_promote_to_gold(L.KNOWN_LICENSES["agront"])

    def test_hemp_tables_are_green(self):
        for it in crops.plan_hemp_tables():
            assert it.license.verdict == "green"

    def test_hemp_pdfs_are_green(self):
        for it in crops.plan_hemp_pdfs():
            assert it.license.verdict == "green"


# ---------------------------------------------------------------- carbon adapters
class TestCarbonAdapters:
    def test_wosis_query_builder(self):
        q = carbon.wosis_profiles_query(first=5)
        assert "wosisLatestProfiles" in q["query"]
        assert q["variables"] == {"first": 5}

    def test_wosis_plan_marks_license_check(self):
        items = carbon.plan_wosis(pages=1)
        assert "license_check" in items[0].metadata

    def test_wosis_plan_tags_yellow_not_green(self):
        # Rosa TW-612 Blocker 1: pages must not be pre-tagged GREEN; the tier
        # is per-profile and resolved by retier_wosis_artifact().
        for it in carbon.plan_wosis(pages=2):
            assert it.license.verdict == "yellow"
            assert it.source_key == "wosis-untiered"
            assert not L.can_promote_to_gold(it.license)

    def test_wosis_retier_splits_public_restricted(self):
        profiles = [
            {"profileId": 1, "datasetCode": "AF-AfSIS-I"},
            {"profileId": 2, "datasetCode": "XX-RESTRICTED"},
            {"profileId": 3},  # no datasetCode: fail-closed -> restricted
        ]
        public, restricted = carbon.retier_wosis_artifact(profiles)
        assert [p["profileId"] for p in public] == [1]
        assert [p["profileId"] for p in restricted] == [2, 3]

    def test_ameriflux_marks_account_required(self):
        items = carbon.plan_ameriflux(["US-Ne1"])
        assert "FLUXDATA account required" in items[0].metadata["note"]
        assert items[0].license.verdict == "green"

    def test_gedi_carries_misalignment_tag(self):
        items = carbon.plan_gedi(["tile1"])
        assert "misalignment_tag" in items[0].metadata

    def test_registry_crosswalk_is_yellow(self):
        for it in carbon.plan_registry_crosswalk():
            assert it.license.verdict == "yellow"
            assert not L.can_promote_to_gold(it.license)


# ---------------------------------------------------------------- Rosa TW-612 round-1 fixes


class TestLicenseValidation:
    def test_write_refuses_license_mismatch(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"x"),
        )
        bad = _item(license=L.KNOWN_LICENSES["agront"])  # RED tag on a GREEN source_key
        res = w.write(bad)
        assert res.artifact_id is None
        assert "mismatch" in res.error

    def test_write_refuses_custom_green_without_justification(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"x"),
        )
        bad = _item(source_key="custom", license=L.KNOWN_LICENSES["gefs-reforecast"])
        res = w.write(bad)
        assert res.artifact_id is None
        assert "justification" in res.error

    def test_write_accepts_custom_green_with_justification(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"x"),
        )
        ok = _item(
            source_key="custom",
            license=L.KNOWN_LICENSES["gefs-reforecast"],
            metadata={
                "license_justification": "reviewed ToU 2026-10-18, redistribution allowed"
            },
        )
        res = w.write(ok)
        assert res.artifact_id is not None

    def test_write_refuses_unknown_source_key(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"x"),
        )
        bad = _item(source_key="nope-not-real")
        res = w.write(bad)
        assert res.artifact_id is None
        assert "unknown source_key" in res.error


class TestGoldGateNullFailClosed:
    def test_null_verdict_raises(self, tmp_path):
        # Pre-license-tagging bronze rows (NULL verdict) fail closed.
        class NullVerdictDB(FakeDB):
            def table(self, name):
                t = super().table(name)
                orig_execute = t.execute

                def execute():
                    r = orig_execute()
                    if name == "sci_bronze_artifacts" and getattr(t, "_single", False):
                        r.data["license_verdict"] = None
                    return r

                t.execute = execute
                return t

        db = NullVerdictDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"x"),
        )
        res = w.write(_item())
        with pytest.raises((L.LicenseQuarantineError, ValueError)):
            w.gold_gate(res.artifact_id)


class TestPiiHashing:
    def test_contact_values_hashed(self):
        out = hash_pii_metadata(
            {
                "contact_email": "person@example.com",
                "site": "US-Ne1",
                "phone": "555-0100",
            }
        )
        assert out["contact_email"].startswith("sha256:")
        assert out["phone"].startswith("sha256:")
        assert "person@example.com" not in out["contact_email"]
        assert out["site"] == "US-Ne1"

    def test_nested_contact_values_hashed(self):
        # Rosa TW-612 round 2: nested structures must not slip through raw.
        out = hash_pii_metadata(
            {
                "contact": {"email": "real@person.com", "role": "pi"},
                "maintainers": [{"name": "Jane Doe"}, {"org": "NOAA"}],
                "site": "US-Ne1",
            }
        )
        assert out["contact"]["email"].startswith("sha256:")
        assert "real@person.com" not in out["contact"]["email"]
        assert out["contact"]["role"] == "pi"
        assert out["maintainers"][0]["name"].startswith("sha256:")
        assert "Jane Doe" not in out["maintainers"][0]["name"]
        assert out["maintainers"][1]["org"] == "NOAA"
        assert out["site"] == "US-Ne1"

    def test_contact_key_string_lists_hashed(self):
        # Rosa TW-612 round 3: a list of plain strings under a contact-shaped
        # key must not pass through raw.
        out = hash_pii_metadata(
            {
                "emails": ["a@x.com", "b@x.com"],
                "contacts": [{"email": "c@x.com", "role": "tech"}],
                "site": "US-Ne1",
            }
        )
        assert all(e.startswith("sha256:") for e in out["emails"])
        assert "a@x.com" not in out["emails"][0]
        assert out["contacts"][0]["email"].startswith("sha256:")
        assert out["contacts"][0]["role"] == "tech"
        assert out["site"] == "US-Ne1"

    def test_write_hashes_contact_metadata(self, tmp_path):
        db = FakeDB()
        w = BronzeWriter(
            str(tmp_path),
            db,
            "lab-1",
            "subseasonal",
            fetcher=lambda url, dest: open(dest, "wb").write(b"x"),
        )
        w.write(_item(metadata={"contact_email": "a@b.c", "note": "plain"}))
        row = db.rows["sci_bronze_artifacts"][0]
        assert row["source_metadata"]["contact_email"].startswith("sha256:")
        assert row["source_metadata"]["note"] == "plain"
