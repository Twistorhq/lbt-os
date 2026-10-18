"""TW-593 Track 2 · crop-climate lane — Supabase-backed store.

Prepared by Twistor Holdings LLC.

Production backend for the crop-climate lane (Rosa Major 2). Implements the
same router-facing contract as models.CropClimateStore, but every read and
write goes through Postgres — so the trust machinery applies: FK constraints,
the sci_crop_gold immutability trigger, RLS policies, and real persistence.

The in-memory CropClimateStore stays as the test double (tests override
get_store); this class is what get_store() returns in production.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from . import models
from .models import (
    BronzeArtifact,
    Calibration,
    DatasetVersion,
    GoldRow,
    Lab,
    Measurement,
    StoreError,
    _bump_patch,
    _canonical,
    _new_id,
    _sha256,
    default_split,
)

log = logging.getLogger(__name__)

LANE = models.LANE


def _row(cls, data: dict) -> Any:
    """Materialize a dataclass from a Supabase row, ignoring extra keys."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in fields})


def _rows(db, table: str, **filters) -> list[dict]:
    q = db.table(table).select("*")
    for col, val in filters.items():
        q = q.eq(col, val)
    res = q.execute()
    return list(getattr(res, "data", []) or [])


class SupabaseCropClimateStore(models.CropClimateStore):
    """Postgres-backed implementation of the crop-climate store contract.

    Subclasses CropClimateStore for interface honesty; every persistence
    method is overridden to go through Postgres. The in-memory parent remains
    the test double.
    """

    def __init__(self, db: Any) -> None:
        # NOTE: intentionally not calling super().__init__() — no in-memory
        # dicts here; all state lives in Postgres.
        self._db = db
        self.dead_letters: list[dict] = []  # operational; logged, not persisted

    def table(self, name: str):
        """Passthrough for cross-cutting queries (e.g. sci_api_keys auth)."""
        return self._db.table(name)

    # ------------------------------------------------------------ properties
    @property
    def labs(self) -> dict[str, Lab]:
        return {r["id"]: _row(Lab, r) for r in _rows(self._db, "sci_labs")}

    @property
    def gold(self) -> dict[str, GoldRow]:
        return {r["id"]: _row(GoldRow, r) for r in _rows(self._db, "sci_crop_gold")}

    @property
    def measurements(self) -> dict[str, Measurement]:
        return {
            r["id"]: _row(Measurement, r)
            for r in _rows(self._db, "sci_crop_measurements")
        }

    @property
    def versions(self) -> dict[str, DatasetVersion]:
        return {
            r["id"]: _row(DatasetVersion, r)
            for r in _rows(self._db, "sci_dataset_versions", lane=LANE)
        }

    # ----------------------------------------------------------------- labs
    def register_lab(self, name: str, kind: str, verification_status: str) -> Lab:
        res = (
            self._db.table("sci_labs")
            .insert({"name": name, "kind": kind, "verification_status": verification_status})
            .execute()
        )
        return _row(Lab, (res.data or [{}])[0])

    def register_artifact(
        self,
        lab_id: str,
        storage_path: str,
        content_hash: str,
        collector_signature: str | None,
        schema_version: int,
    ) -> BronzeArtifact:
        # FKs enforced by Postgres; surface friendly errors pre-insert.
        if not _rows(self._db, "sci_labs", id=lab_id):
            raise StoreError(f"unknown lab_id {lab_id}")
        res = (
            self._db.table("sci_bronze_artifacts")
            .insert(
                {
                    "lane": LANE,
                    "lab_id": lab_id,
                    "storage_path": storage_path,
                    "content_hash": content_hash,
                    "collector_signature": collector_signature,
                    "collected_at": models._now(),
                    "schema_version": schema_version,
                }
            )
            .execute()
        )
        return _row(BronzeArtifact, (res.data or [{}])[0])

    def register_calibration(
        self,
        lab_id: str,
        instrument_id: str,
        instrument_kind: str,
        calibrated_at: str,
        standard_ref: str,
        valid_until: str | None = None,
    ) -> Calibration:
        if not _rows(self._db, "sci_labs", id=lab_id):
            raise StoreError(f"unknown lab_id {lab_id}")
        res = (
            self._db.table("sci_calibrations")
            .insert(
                {
                    "lab_id": lab_id,
                    "instrument_id": instrument_id,
                    "instrument_kind": instrument_kind,
                    "calibrated_at": calibrated_at,
                    "standard_ref": standard_ref,
                    "valid_until": valid_until,
                }
            )
            .execute()
        )
        return _row(Calibration, (res.data or [{}])[0])

    # ----------------------------------------------------------- measurements
    def add_measurement(
        self,
        artifact_id: str,
        lab_id: str,
        calibration_id: str | None,
        conditions: dict,
        genotype: str,
        location: str,
        year: int,
        weather: dict,
        management: dict,
        yield_t_ha: float,
        synthetic: bool = False,
        season: str = "summer",
    ) -> Measurement:
        # Same validation as the in-memory store (mirrors the migration CHECKs).
        if not _rows(self._db, "sci_bronze_artifacts", id=artifact_id):
            raise StoreError(f"unknown artifact_id {artifact_id}")
        if not _rows(self._db, "sci_labs", id=lab_id):
            raise StoreError(f"unknown lab_id {lab_id}")
        if calibration_id is not None and not _rows(
            self._db, "sci_calibrations", id=calibration_id
        ):
            raise StoreError(f"unknown calibration_id {calibration_id}")
        if not isinstance(conditions, dict) or "trial_design" not in conditions:
            raise StoreError("conditions must carry trial_design")
        if not (1900 <= year <= 2100):
            raise StoreError(f"year {year} out of range")
        if season not in models.SEASONS:
            raise StoreError(f"season {season!r} not in {models.SEASONS}")
        if not genotype or not location:
            raise StoreError("genotype and location are required")
        try:
            y = float(yield_t_ha)
        except (TypeError, ValueError) as exc:
            raise StoreError(f"yield_t_ha not numeric: {yield_t_ha}") from exc
        if not (0 <= y <= 60):
            raise StoreError(f"yield_t_ha {y} outside sane range 0..60")
        res = (
            self._db.table("sci_crop_measurements")
            .insert(
                {
                    "artifact_id": artifact_id,
                    "lab_id": lab_id,
                    "calibration_id": calibration_id,
                    "conditions": conditions,
                    "genotype": genotype,
                    "location": location,
                    "year": year,
                    "season": season,
                    "weather": weather or {},
                    "management": management or {},
                    "yield_t_ha": y,
                    "synthetic": bool(synthetic),
                }
            )
            .execute()
        )
        return _row(Measurement, (res.data or [{}])[0])

    # --------------------------------------------------------------- versions
    def _resolve_version(self, version: str | None) -> DatasetVersion:
        versions = self.versions
        if version is None:
            if not versions:
                raise StoreError("no dataset versions published")
            return max(versions.values(), key=lambda v: str(v.released_at))
        for v in versions.values():
            if v.semver == version or v.id == version:
                return v
        raise StoreError(f"unknown dataset version {version!r}")

    def get_version(self, version: str) -> DatasetVersion:
        return self._resolve_version(version)

    def list_versions(self) -> list[DatasetVersion]:
        return sorted(self.versions.values(), key=lambda v: str(v.released_at))

    def publish_version(
        self,
        dataset_name: str,
        semver: str,
        release_notes: str,
        split_fn=default_split,
        benchmark_pin: str | None = None,
        doi: str | None = None,
        supersedes_id: str | None = None,
        carry_splits: dict[str, str] | None = None,
        silver_ids: list[str] | None = None,
    ) -> DatasetVersion:
        if any(
            v.dataset_name == dataset_name and v.semver == semver
            for v in self.versions.values()
        ):
            raise StoreError(f"version {dataset_name} {semver} already exists")
        measurements = self.measurements
        ids = silver_ids if silver_ids is not None else list(measurements)
        split_map: dict[str, str] = {}
        row_hashes: list[str] = []
        gold_rows: list[dict] = []
        for sid in ids:
            m = measurements.get(sid)
            if m is None:
                raise StoreError(f"unknown silver_id {sid}")
            split = (carry_splits or {}).get(sid) or split_fn(asdict(m))
            if split not in models.VALID_SPLITS:
                raise StoreError(f"split_fn returned invalid split {split!r}")
            split_map[sid] = split
            gold_rows.append(
                {
                    "dataset_version_id": "__pending__",
                    "silver_id": sid,
                    "split": split,
                    "genotype": m.genotype,
                    "location": m.location,
                    "year": m.year,
                    "yield_t_ha": float(m.yield_t_ha),
                    "synthetic": bool(m.synthetic),
                    "attestation_summary": {},
                    "row_hash": _sha256(
                        _canonical(
                            {
                                "silver_id": sid,
                                "split": split,
                                "genotype": m.genotype,
                                "location": m.location,
                                "year": m.year,
                                "yield_t_ha": m.yield_t_ha,
                                "synthetic": m.synthetic,
                            }
                        )
                    ),
                }
            )
            row_hashes.append(gold_rows[-1]["row_hash"])
        row_hashes.sort()
        version_id = _new_id()
        res = (
            self._db.table("sci_dataset_versions")
            .insert(
                {
                    "id": version_id,
                    "lane": LANE,
                    "dataset_name": dataset_name,
                    "semver": semver,
                    "row_count": len(row_hashes),
                    "content_root_hash": _sha256("".join(row_hashes)),
                    "benchmark_pin": benchmark_pin,
                    "doi": doi,
                    "supersedes_id": supersedes_id,
                    "release_notes": release_notes,
                }
            )
            .execute()
        )
        version = _row(DatasetVersion, (res.data or [{}])[0])
        for gr in gold_rows:
            gr["dataset_version_id"] = version.id
            self._db.table("sci_crop_gold").insert(gr).execute()
        if benchmark_pin and not _rows(
            self._db, "sci_benchmarks", lane=LANE, name=benchmark_pin
        ):
            hidden = self._hidden_split_hash(version.id)
            self._db.table("sci_benchmarks").insert(
                {
                    "lane": LANE,
                    "name": benchmark_pin,
                    "dataset_version_id": version.id,
                    "metric": models.BENCHMARK_METRIC,
                    "hidden_split_hash": hidden,
                    "status": "open",
                }
            ).execute()
        return version

    def _hidden_split_hash(self, version_id: str) -> str:
        answers = sorted(
            f"{r['silver_id']}|{r['yield_t_ha']}"
            for r in _rows(self._db, "sci_crop_gold", dataset_version_id=version_id)
            if r.get("split") == "test_future"
        )
        return _sha256("\n".join(answers))

    def update_gold(self, gold_id: str, values: dict) -> GoldRow:
        raise models.ImmutableVersionError(
            "sci_crop_gold is immutable: use correct_measurement + a new "
            "dataset version (TW-593 §3.3)"
        )

    def delete_gold(self, gold_id: str) -> None:
        raise models.ImmutableVersionError(
            "sci_crop_gold is immutable: rows are never deleted"
        )

    def update_version(self, version_id: str, values: dict) -> DatasetVersion:
        raise models.ImmutableVersionError(
            "sci_dataset_versions are immutable snapshots: new version = new row"
        )

    # ------------------------------------------------------------ corrections
    def correct_measurement(
        self, silver_id: str, corrected_values: dict, reason: str, corrected_by: str
    ) -> DatasetVersion:
        """Supersession, never mutation. Mirrors the in-memory semantics."""
        measurements = self.measurements
        if silver_id not in measurements:
            raise StoreError(f"unknown silver_id {silver_id}")
        if not _rows(self._db, "sci_labs", id=corrected_by):
            raise StoreError(f"unknown corrected_by lab {corrected_by}")
        old = measurements[silver_id]
        res = (
            self._db.table("sci_crop_measurements")
            .insert(
                {
                    "artifact_id": old.artifact_id,
                    "lab_id": old.lab_id,
                    "calibration_id": old.calibration_id,
                    "conditions": {**old.conditions, "correction_of": silver_id},
                    "genotype": old.genotype,
                    "location": old.location,
                    "year": old.year,
                    "season": old.season,
                    "weather": old.weather,
                    "management": old.management,
                    "yield_t_ha": float(
                        corrected_values.get("yield_t_ha", old.yield_t_ha)
                    ),
                    "synthetic": bool(old.synthetic),
                }
            )
            .execute()
        )
        new_silver = _row(Measurement, (res.data or [{}])[0])

        latest = self._resolve_version(None)
        latest_gold = _rows(self._db, "sci_crop_gold", dataset_version_id=latest.id)
        old_map = {r["silver_id"]: r["split"] for r in latest_gold}
        silver_ids = [sid for sid in old_map if sid != silver_id] + [new_silver.id]
        carry = {sid: old_map[sid] for sid in old_map if sid != silver_id}
        carry[new_silver.id] = default_split(asdict(new_silver))

        new_semver = _bump_patch(latest.semver)
        c_res = (
            self._db.table("sci_corrections")
            .insert(
                {
                    "lane": LANE,
                    "corrects_silver_table": "sci_crop_measurements",
                    "corrects_silver_id": silver_id,
                    "reason": reason,
                    "corrected_values": corrected_values,
                    "corrected_by": corrected_by,
                    "new_dataset_version_id": None,  # linked after publish
                }
            )
            .execute()
        )
        correction_id = (c_res.data or [{}])[0].get("id")
        new_version = self.publish_version(
            latest.dataset_name,
            new_semver,
            release_notes=f"Correction: {reason}",
            supersedes_id=latest.id,
            carry_splits=carry,
            silver_ids=silver_ids,
        )
        if correction_id:
            self._db.table("sci_corrections").update(
                {"new_dataset_version_id": new_version.id}
            ).eq("id", correction_id).execute()
        return new_version

    def list_corrections(self, dataset_name: str) -> list[dict]:
        rows = _rows(self._db, "sci_corrections", lane=LANE)
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "lane": r["lane"],
                    "dataset_name": dataset_name,
                    "corrects_silver_id": r["corrects_silver_id"],
                    "reason": r["reason"],
                    "corrected_values": r["corrected_values"],
                    "corrected_by": r["corrected_by"],
                    "corrected_at": r.get("corrected_at"),
                    "new_dataset_version_id": r.get("new_dataset_version_id") or "",
                }
            )
        return out

    # ------------------------------------------------------------------ query
    def query_gold(
        self,
        version: str | None = None,
        genotype: str | None = None,
        location: str | None = None,
        year: int | None = None,
        split: str = "train",
    ) -> list[dict]:
        """Gold query. split defaults to 'train': test_future rows never leak."""
        if split not in (*models.VALID_SPLITS, "all"):
            raise StoreError(f"split {split!r} invalid")
        v = self._resolve_version(version) if self.versions else None
        q = self._db.table("sci_crop_gold").select("*")
        if v is not None:
            q = q.eq("dataset_version_id", v.id)
        if split != "all":
            q = q.eq("split", split)
        if genotype:
            q = q.eq("genotype", genotype)
        if location:
            q = q.eq("location", location)
        if year is not None:
            q = q.eq("year", year)
        rows = list(getattr(q.execute(), "data", []) or [])
        out = []
        for r in rows:
            masked = r.get("split") == "test_future"
            out.append(
                {
                    "id": r["id"],
                    "dataset_version_id": r["dataset_version_id"],
                    "silver_id": r["silver_id"],
                    "split": r["split"],
                    "genotype": r["genotype"],
                    "location": r["location"],
                    "year": r["year"],
                    "yield_t_ha": None if masked else r["yield_t_ha"],
                    "yield_withheld": masked,
                    "synthetic": r["synthetic"],
                    "attestation_summary": r.get("attestation_summary") or {},
                }
            )
        return sorted(out, key=lambda d: (d["genotype"], d["location"], d["year"]))

    # ------------------------------------------------------------ provenance
    def provenance_chain(self, gold_id: str) -> dict:
        """One query walks the whole chain. No chain, no trust (TW-593 §3.1)."""
        gold_rows = _rows(self._db, "sci_crop_gold", id=gold_id)
        if not gold_rows:
            raise StoreError(f"unknown gold row {gold_id}")
        row = gold_rows[0]
        silver_rows = _rows(self._db, "sci_crop_measurements", id=row["silver_id"])
        if not silver_rows:
            raise StoreError(f"dangling silver_id {row['silver_id']}")
        silver = silver_rows[0]
        bronze_rows = _rows(self._db, "sci_bronze_artifacts", id=silver["artifact_id"])
        if not bronze_rows:
            raise StoreError(f"dangling artifact_id {silver['artifact_id']}")
        bronze = bronze_rows[0]
        lab_rows = _rows(self._db, "sci_labs", id=silver["lab_id"])
        if not lab_rows:
            raise StoreError(f"dangling lab_id {silver['lab_id']}")
        lab = lab_rows[0]
        calibration = None
        if silver.get("calibration_id"):
            cal_rows = _rows(self._db, "sci_calibrations", id=silver["calibration_id"])
            calibration = cal_rows[0] if cal_rows else None
        return {
            "gold": {
                "id": row["id"],
                "split": row["split"],
                "dataset_version_id": row["dataset_version_id"],
            },
            "silver": {
                "id": silver["id"],
                "genotype": silver["genotype"],
                "location": silver["location"],
                "year": silver["year"],
                "conditions": silver["conditions"],
            },
            "bronze": {
                "id": bronze["id"],
                "storage_path": bronze["storage_path"],
                "content_hash": bronze["content_hash"],
                "collector_signature": bronze["collector_signature"],
                "schema_version": bronze["schema_version"],
            },
            "lab": {
                "id": lab["id"],
                "name": lab["name"],
                "kind": lab["kind"],
                "verification_status": lab["verification_status"],
            },
            "calibration": calibration,
        }

    # ------------------------------------------------------------ benchmarks
    def get_benchmark(self, name: str) -> dict:
        rows = _rows(self._db, "sci_benchmarks", lane=LANE, name=name)
        if not rows:
            raise StoreError(f"unknown benchmark {name!r}")
        return rows[0]

    def list_benchmarks(self) -> list[dict]:
        return _rows(self._db, "sci_benchmarks", lane=LANE)
