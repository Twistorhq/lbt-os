"""TW-593 Track 2 crop-climate lane: domain models + repository (TW-596).

The database is the truth and Dre is its keeper. This module owns the
lane's data rules:

- Silver measurements carry provenance (artifact -> lab -> calibration)
  and lane-schema'd conditions (trial_design is mandatory).
- Gold rows are immutable snapshots: update/delete always fail loud.
  Corrections supersede via a new dataset version (TW-593 §3.3).
- Gold rows carry the benchmark's hidden split: 'train' | 'test_future'.
  Future-like conditions (projected 2035 climate) are withheld in
  'test_future'; their yields are masked on read and hashed at release.

Two backends, one contract: CropClimateStore is the in-memory
implementation used by tests and the V1 prototype. The Supabase-backed
implementation plugs into the same method contract at integration
(Dre's pass); SQL constraints in migration_sci_crop_climate_v1.sql mirror
every rule enforced here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)

LANE = "crop_climate"
DATASET_NAME = "climateshift-1"
BENCHMARK_NAME = "climateshift-1"
BENCHMARK_METRIC = "yield_mae_t_ha"

VALID_SPLITS = ("train", "test_future")
SEASONS = ("spring", "summer", "fall", "winter")
FUTURE_ANOMALY_THRESHOLD_C = 1.5  # temp_anomaly_c >= this => future-like


class StoreError(Exception):
    """Constraint / FK / validation failure."""


class ImmutableVersionError(StoreError):
    """Raised on any attempt to mutate an immutable gold row or version."""


# ------------------------------------------------------------------ entities
@dataclass
class Lab:
    id: str
    name: str
    kind: str  # academic | national_lab | industry | independent
    verification_status: str  # pending | verified | suspended
    created_at: str = field(default_factory=lambda: _now())


@dataclass
class BronzeArtifact:
    id: str
    lane: str
    lab_id: str
    storage_path: str
    content_hash: str
    collector_signature: str | None
    collected_at: str
    schema_version: int


@dataclass
class Calibration:
    id: str
    lab_id: str
    instrument_id: str
    instrument_kind: str
    calibrated_at: str
    valid_until: str | None
    standard_ref: str


@dataclass
class Measurement:
    """Silver row: one trial measurement with provenance attached."""

    id: str
    artifact_id: str
    lab_id: str
    calibration_id: str | None  # NULL-able: field trials use no instrument
    conditions: dict
    genotype: str
    location: str
    year: int
    season: str
    weather: dict
    management: dict
    yield_t_ha: float
    synthetic: bool
    # TW-599 hemp-first: crop-specific trait panel (hemp: cannabinoid panel
    # THC/CBD/CBG + fiber quality + lab accreditation ID). Empty for crops
    # without a trait panel.
    traits: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: _now())


@dataclass
class GoldRow:
    id: str
    dataset_version_id: str
    silver_id: str
    split: str  # 'train' | 'test_future'
    genotype: str
    location: str
    year: int
    yield_t_ha: float
    synthetic: bool
    attestation_summary: dict = field(default_factory=dict)
    # TW-599 hemp-first: trait panel denormalized into gold for serving
    traits: dict = field(default_factory=dict)


@dataclass
class DatasetVersion:
    id: str
    lane: str
    dataset_name: str
    semver: str
    row_count: int
    content_root_hash: str  # merkle root over the snapshot's row hashes
    benchmark_pin: str | None
    doi: str | None  # placeholder until Justynn calls minting
    supersedes_id: str | None
    released_at: str = field(default_factory=lambda: _now())
    release_notes: str = ""


@dataclass
class Correction:
    id: str
    lane: str
    dataset_name: str
    corrects_silver_id: str
    reason: str
    corrected_values: dict
    corrected_by: str
    corrected_at: str = field(default_factory=lambda: _now())
    new_dataset_version_id: str = ""


@dataclass
class Benchmark:
    id: str
    lane: str
    name: str
    dataset_version_id: str
    metric: str
    hidden_split_hash: str  # the withheld answers, hashed at release
    status: str  # open | closed
    leaderboard: list = field(default_factory=list)


# ------------------------------------------------------------------ helpers
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_split(record: dict) -> str:
    """Assign the hidden split.

    Future-like conditions — explicitly simulated 2035 climate, or a
    temperature anomaly at/above the future threshold — are withheld in
    'test_future'. Everything else trains.
    """
    conditions = record.get("conditions") or {}
    weather = record.get("weather") or {}
    if conditions.get("simulated_future_climate"):
        return "test_future"
    if (weather.get("temp_anomaly_c") or 0) >= FUTURE_ANOMALY_THRESHOLD_C:
        return "test_future"
    return "train"


# ------------------------------------------------------------------ store
class CropClimateStore:
    """In-memory repository implementing the lane's data rules.

    Method contract (the Supabase adapter must honor the same):
    register_lab / register_artifact / register_calibration
    add_measurement (validates constraints + FKs)
    publish_version (assigns splits, pins merkle root, mints benchmark hash)
    query_gold (split-aware: train is default, test_future yields masked)
    provenance_chain (gold -> silver -> bronze -> lab -> calibration)
    correct_measurement (supersession: new version, history intact)
    list_corrections / get_benchmark / list_benchmarks
    """

    def __init__(self) -> None:
        self.labs: dict[str, Lab] = {}
        self.artifacts: dict[str, BronzeArtifact] = {}
        self.calibrations: dict[str, Calibration] = {}
        self.measurements: dict[str, Measurement] = {}
        self.gold: dict[str, GoldRow] = {}
        self.versions: dict[str, DatasetVersion] = {}
        self.version_splits: dict[str, dict[str, str]] = {}  # version -> silver -> split
        self.split_fns: dict[str, object] = {}
        self.corrections: list[Correction] = []
        self.benchmarks: dict[str, Benchmark] = {}
        self.dead_letters: list[dict] = []
        self.api_keys: list[dict] = []  # test-double for sci_api_keys
        self.organizations: list[dict] = []  # test-double: subscription gate
        self.toll_events: list[dict] = []  # test-double: volume pricing

    def table(self, name: str):
        """Minimal query shim for cross-cutting lookups (sci_api_keys auth,
        organizations subscription gate, toll_events volume pricing).

        Test-double only: supports .select().eq().gte().execute() -> .data.
        """

        store = self

        class _Q:
            def __init__(self):
                self._table = name
                if name == "sci_api_keys":
                    self._rows = list(store.api_keys)
                elif name == "organizations":
                    self._rows = list(store.organizations)
                elif name == "toll_events":
                    self._rows = list(store.toll_events)
                else:
                    self._rows = []

            def select(self, *_a):
                return self

            def eq(self, col, val):
                self._rows = [r for r in self._rows if r.get(col) == val]
                return self

            def gte(self, col, val):
                self._rows = [r for r in self._rows
                              if (r.get(col) or "") >= val]
                return self

            def insert(self, row):
                # test-double: append straight to the store's live list
                target = {"sci_api_keys": store.api_keys,
                          "organizations": store.organizations,
                          "toll_events": store.toll_events}.get(self._table)
                if target is not None:
                    target.append(dict(row))
                return self

            def execute(self):
                return type("R", (), {"data": self._rows})()

        return _Q()

    # ---------------------------------------------------------- shared mirrors
    def register_lab(self, name: str, kind: str, verification_status: str) -> Lab:
        lab = Lab(id=_new_id(), name=name, kind=kind,
                  verification_status=verification_status)
        self.labs[lab.id] = lab
        return lab

    def register_artifact(self, lab_id: str, storage_path: str, content_hash: str,
                          collector_signature: str | None,
                          schema_version: int) -> BronzeArtifact:
        if lab_id not in self.labs:
            raise StoreError(f"unknown lab_id {lab_id}")
        artifact = BronzeArtifact(
            id=_new_id(), lane=LANE, lab_id=lab_id, storage_path=storage_path,
            content_hash=content_hash, collector_signature=collector_signature,
            collected_at=_now(), schema_version=schema_version)
        self.artifacts[artifact.id] = artifact
        return artifact

    def register_calibration(self, lab_id: str, instrument_id: str,
                             instrument_kind: str, calibrated_at: str,
                             standard_ref: str,
                             valid_until: str | None = None) -> Calibration:
        if lab_id not in self.labs:
            raise StoreError(f"unknown lab_id {lab_id}")
        cal = Calibration(id=_new_id(), lab_id=lab_id, instrument_id=instrument_id,
                          instrument_kind=instrument_kind,
                          calibrated_at=calibrated_at, valid_until=valid_until,
                          standard_ref=standard_ref)
        self.calibrations[cal.id] = cal
        return cal

    # --------------------------------------------------------------- silver
    def add_measurement(self, artifact_id: str, lab_id: str,
                        calibration_id: str | None, conditions: dict,
                        genotype: str, location: str, year: int,
                        weather: dict, management: dict, yield_t_ha: float,
                        synthetic: bool = False,
                        season: str = "summer",
                        traits: dict | None = None) -> Measurement:
        # FKs — no dangling provenance, ever
        if artifact_id not in self.artifacts:
            raise StoreError(f"unknown artifact_id {artifact_id}")
        if lab_id not in self.labs:
            raise StoreError(f"unknown lab_id {lab_id}")
        if calibration_id is not None and calibration_id not in self.calibrations:
            raise StoreError(f"unknown calibration_id {calibration_id}")
        # constraints (mirrored in the migration)
        if not isinstance(conditions, dict) or "trial_design" not in conditions:
            raise StoreError("conditions must carry trial_design")
        if not (1900 <= year <= 2100):
            raise StoreError(f"year {year} out of range")
        if season not in SEASONS:
            raise StoreError(f"season {season!r} not in {SEASONS}")
        if not genotype or not location:
            raise StoreError("genotype and location are required")
        try:
            y = float(yield_t_ha)
        except (TypeError, ValueError) as exc:
            raise StoreError(f"yield_t_ha not numeric: {yield_t_ha}") from exc
        if not (0 <= y <= 60):
            raise StoreError(f"yield_t_ha {y} outside sane range 0..60")

        m = Measurement(
            id=_new_id(), artifact_id=artifact_id, lab_id=lab_id,
            calibration_id=calibration_id, conditions=conditions,
            genotype=genotype, location=location, year=year, season=season,
            weather=weather or {}, management=management or {},
            yield_t_ha=y, synthetic=bool(synthetic),
            traits=traits or {})
        self.measurements[m.id] = m
        return m

    # ----------------------------------------------------------------- gold
    def _insert_gold_row(self, dataset_version_id: str, silver_id: str,
                         split: str, genotype: str, location: str, year: int,
                         yield_t_ha: float, synthetic: bool,
                         attestation_summary: dict | None = None,
                         traits: dict | None = None) -> GoldRow:
        if dataset_version_id not in self.versions:
            raise StoreError(f"unknown dataset_version_id {dataset_version_id}")
        if silver_id not in self.measurements and silver_id != "dummy":
            raise StoreError(f"unknown silver_id {silver_id}")
        if split not in VALID_SPLITS:
            raise StoreError(
                f"split {split!r} invalid: must be one of {VALID_SPLITS}")
        row = GoldRow(
            id=_new_id(), dataset_version_id=dataset_version_id,
            silver_id=silver_id, split=split, genotype=genotype,
            location=location, year=year, yield_t_ha=float(yield_t_ha),
            synthetic=bool(synthetic),
            attestation_summary=attestation_summary or {},
            traits=traits or {})
        self.gold[row.id] = row
        return row

    def publish_version(self, dataset_name: str, semver: str, release_notes: str,
                        split_fn=default_split, benchmark_pin: str | None = None,
                        doi: str | None = None,
                        supersedes_id: str | None = None,
                        carry_splits: dict[str, str] | None = None,
                        silver_ids: list[str] | None = None) -> DatasetVersion:
        if any(v.dataset_name == dataset_name and v.semver == semver
               for v in self.versions.values()):
            raise StoreError(f"version {dataset_name} {semver} already exists")
        version = DatasetVersion(
            id=_new_id(), lane=LANE, dataset_name=dataset_name, semver=semver,
            row_count=0, content_root_hash="", benchmark_pin=benchmark_pin,
            doi=doi, supersedes_id=supersedes_id, release_notes=release_notes)
        self.versions[version.id] = version
        self.split_fns[version.id] = split_fn

        ids = silver_ids if silver_ids is not None else list(self.measurements)
        split_map: dict[str, str] = {}
        row_hashes: list[str] = []
        for sid in ids:
            m = self.measurements[sid]
            split = (carry_splits or {}).get(sid) or split_fn(asdict(m))
            if split not in VALID_SPLITS:
                raise StoreError(f"split_fn returned invalid split {split!r}")
            split_map[sid] = split
            self._insert_gold_row(
                version.id, sid, split, m.genotype, m.location, m.year,
                m.yield_t_ha, m.synthetic, traits=m.traits)
            row_hashes.append(_sha256(_canonical({
                "silver_id": sid, "split": split, "genotype": m.genotype,
                "location": m.location, "year": m.year,
                "yield_t_ha": m.yield_t_ha, "synthetic": m.synthetic,
                "traits": m.traits})))
        row_hashes.sort()
        version.row_count = len(row_hashes)
        version.content_root_hash = _sha256("".join(row_hashes))
        self.version_splits[version.id] = split_map

        if benchmark_pin and benchmark_pin not in self.benchmarks:
            self.benchmarks[benchmark_pin] = Benchmark(
                id=_new_id(), lane=LANE, name=benchmark_pin,
                dataset_version_id=version.id, metric=BENCHMARK_METRIC,
                hidden_split_hash=self._hidden_split_hash(version.id),
                status="open")
        return version

    def _hidden_split_hash(self, version_id: str) -> str:
        """Hash of the withheld answers: (silver_id, yield) for test_future rows."""
        answers = sorted(
            f"{r.silver_id}|{r.yield_t_ha}"
            for r in self.gold.values()
            if r.dataset_version_id == version_id and r.split == "test_future")
        return _sha256("\n".join(answers))

    def _resolve_version(self, version: str | None) -> DatasetVersion:
        if version is None:
            if not self.versions:
                raise StoreError("no dataset versions published")
            return max(self.versions.values(), key=lambda v: v.released_at)
        for v in self.versions.values():
            if v.semver == version or v.id == version:
                return v
        raise StoreError(f"unknown dataset version {version!r}")

    def get_version(self, version: str) -> DatasetVersion:
        return self._resolve_version(version)

    def list_versions(self) -> list[DatasetVersion]:
        return sorted(self.versions.values(), key=lambda v: v.released_at)

    def query_gold(self, version: str | None = None, genotype: str | None = None,
                   location: str | None = None, year: int | None = None,
                   split: str = "train") -> list[dict]:
        """Gold query. split defaults to 'train': test_future rows never leak.

        test_future rows are served with yields masked (withheld answers);
        their integrity is pinned by the benchmark's hidden_split_hash.
        """
        if split not in (*VALID_SPLITS, "all"):
            raise StoreError(f"split {split!r} invalid")
        v = self._resolve_version(version) if self.versions else None
        rows = []
        for r in self.gold.values():
            if v is not None and r.dataset_version_id != v.id:
                continue
            if split != "all" and r.split != split:
                continue
            if genotype and r.genotype != genotype:
                continue
            if location and r.location != location:
                continue
            if year is not None and r.year != year:
                continue
            masked = r.split == "test_future"
            rows.append({
                "id": r.id,
                "dataset_version_id": r.dataset_version_id,
                "silver_id": r.silver_id,
                "split": r.split,
                "genotype": r.genotype,
                "location": r.location,
                "year": r.year,
                "yield_t_ha": None if masked else r.yield_t_ha,
                "yield_withheld": masked,
                # TW-599: trait panels (hemp cannabinoids) are also answers:
                # masked on the withheld split, served on train.
                "traits": {} if masked else r.traits,
                "traits_withheld": masked,
                "synthetic": r.synthetic,
                "attestation_summary": r.attestation_summary,
            })
        return sorted(rows, key=lambda d: (d["genotype"], d["location"], d["year"]))

    # ------------------------------------------------------- immutability
    def update_gold(self, gold_id: str, values: dict) -> GoldRow:
        raise ImmutableVersionError(
            "sci_crop_gold is immutable: use correct_measurement + a new "
            "dataset version (TW-593 §3.3)")

    def delete_gold(self, gold_id: str) -> None:
        raise ImmutableVersionError(
            "sci_crop_gold is immutable: rows are never deleted")

    def update_version(self, version_id: str, values: dict) -> DatasetVersion:
        raise ImmutableVersionError(
            "sci_dataset_versions are immutable snapshots: new version = new row")

    # ------------------------------------------------------------ corrections
    def correct_measurement(self, silver_id: str, corrected_values: dict,
                            reason: str, corrected_by: str) -> DatasetVersion:
        """Supersession, never mutation (TW-593 §3.3).

        The old silver row and old dataset version stay intact. A new silver
        row carries the corrected values; a new dataset version (patch bump)
        swaps it in and chains supersedes_id.
        """
        if silver_id not in self.measurements:
            raise StoreError(f"unknown silver_id {silver_id}")
        if corrected_by not in self.labs:
            raise StoreError(f"unknown corrected_by lab {corrected_by}")
        old = self.measurements[silver_id]
        new_silver = Measurement(
            id=_new_id(), artifact_id=old.artifact_id, lab_id=old.lab_id,
            calibration_id=old.calibration_id,
            conditions={**old.conditions, "correction_of": silver_id},
            genotype=old.genotype, location=old.location, year=old.year,
            season=old.season, weather=old.weather, management=old.management,
            yield_t_ha=float(corrected_values.get("yield_t_ha", old.yield_t_ha)),
            synthetic=old.synthetic)
        self.measurements[new_silver.id] = new_silver

        latest = self._resolve_version(None)
        old_map = self.version_splits[latest.id]
        silver_ids = [sid for sid in old_map if sid != silver_id] + [new_silver.id]
        carry = {sid: old_map[sid] for sid in old_map if sid != silver_id}
        # the corrected row keeps its split unless the split fn says otherwise
        carry[new_silver.id] = default_split(asdict(new_silver))

        new_semver = _bump_patch(latest.semver)
        correction = Correction(
            id=_new_id(), lane=LANE, dataset_name=latest.dataset_name,
            corrects_silver_id=silver_id, reason=reason,
            corrected_values=corrected_values, corrected_by=corrected_by)
        self.corrections.append(correction)

        new_version = self.publish_version(
            latest.dataset_name, new_semver,
            release_notes=f"Correction: {reason}",
            supersedes_id=latest.id, carry_splits=carry, silver_ids=silver_ids)
        correction.new_dataset_version_id = new_version.id
        return new_version

    def list_corrections(self, dataset_name: str) -> list[dict]:
        return [asdict(c) for c in self.corrections
                if c.dataset_name == dataset_name]

    # ------------------------------------------------------------ provenance
    def provenance_chain(self, gold_id: str) -> dict:
        """One query walks the whole chain. No chain, no trust (TW-593 §3.1)."""
        row = self.gold.get(gold_id)
        if row is None:
            raise StoreError(f"unknown gold row {gold_id}")
        silver = self.measurements[row.silver_id]
        bronze = self.artifacts[silver.artifact_id]
        lab = self.labs[silver.lab_id]
        calibration = (self.calibrations[silver.calibration_id]
                       if silver.calibration_id else None)
        return {
            "gold": {"id": row.id, "split": row.split,
                     "dataset_version_id": row.dataset_version_id},
            "silver": {"id": silver.id, "genotype": silver.genotype,
                       "location": silver.location, "year": silver.year,
                       "conditions": silver.conditions},
            "bronze": {"id": bronze.id, "storage_path": bronze.storage_path,
                       "content_hash": bronze.content_hash,
                       "collector_signature": bronze.collector_signature,
                       "schema_version": bronze.schema_version},
            "lab": {"id": lab.id, "name": lab.name, "kind": lab.kind,
                    "verification_status": lab.verification_status},
            "calibration": (asdict(calibration) if calibration else None),
        }

    # ------------------------------------------------------------ benchmarks
    def get_benchmark(self, name: str) -> dict:
        bench = self.benchmarks.get(name)
        if bench is None:
            raise StoreError(f"unknown benchmark {name!r}")
        return asdict(bench)

    def list_benchmarks(self) -> list[dict]:
        return [asdict(b) for b in self.benchmarks.values()]


def _bump_patch(semver: str) -> str:
    parts = semver.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts.append("1")
    return ".".join(parts)
