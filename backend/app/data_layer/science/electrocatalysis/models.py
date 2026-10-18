"""TW-593 Track 2 · Electrocatalysis lane — SQLAlchemy reference models.

Prepared by Twistor Holdings LLC.

Reference implementation of the sci core schema from
``supabase/migration_sci_core_v1.sql``. The migrations are the source of
truth for DDL; these models mirror them so lane services can walk the
provenance chain (gold → silver → bronze artifact → lab → calibration) in
Python. The other five lanes copy this shape.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on Postgres, JSON on sqlite (tests). The migrations are the DDL source
# of truth; this keeps the reference models runnable without a live database.
JSONB_COL = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class SciLab(Base):
    __tablename__ = "sci_labs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # academic|national_lab|industry|independent
    verification_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    public_key: Mapped[str | None] = mapped_column(Text)
    contact_hashed: Mapped[str | None] = mapped_column(Text)  # TW-552: hashed at ingestion
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "kind IN ('academic','national_lab','industry','independent')",
            name="ck_sci_labs_kind",
        ),
        CheckConstraint(
            "verification_status IN ('pending','verified','suspended')",
            name="ck_sci_labs_status",
        ),
    )


class SciCalibration(Base):
    __tablename__ = "sci_calibrations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    lab_id: Mapped[str] = mapped_column(ForeignKey("sci_labs.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_kind: Mapped[str] = mapped_column(Text, nullable=False)
    calibrated_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[str | None] = mapped_column(DateTime(timezone=True))
    standard_ref: Mapped[str] = mapped_column(Text, nullable=False)
    calibration_payload_hash: Mapped[str | None] = mapped_column(Text)

    lab: Mapped[SciLab] = relationship("SciLab")

    __table_args__ = (
        Index("idx_sci_calibrations_lab", "lab_id"),
    )


class SciBronzeArtifact(Base):
    __tablename__ = "sci_bronze_artifacts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    lane: Mapped[str] = mapped_column(Text, nullable=False)
    lab_id: Mapped[str] = mapped_column(ForeignKey("sci_labs.id"), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)  # sha256
    collector_signature: Mapped[str | None] = mapped_column(Text)  # lab sig over content_hash
    collected_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)

    lab: Mapped[SciLab] = relationship("SciLab")

    __table_args__ = (
        Index("idx_sci_bronze_lane", "lane"),
        Index("idx_sci_bronze_lab", "lab_id"),
    )


class SciDatasetVersion(Base):
    __tablename__ = "sci_dataset_versions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    lane: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_name: Mapped[str] = mapped_column(Text, nullable=False)
    semver: Mapped[str] = mapped_column(Text, nullable=False)  # append-only: new row, never UPDATE
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_root_hash: Mapped[str] = mapped_column(Text, nullable=False)  # merkle root
    benchmark_pin: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)  # minted on Justynn's call
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("sci_dataset_versions.id"))
    released_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    release_notes: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("lane", "dataset_name", "semver", name="uq_sci_version_lane_name_semver"),
        CheckConstraint("row_count >= 0", name="ck_sci_version_row_count"),
    )


class SciCorrection(Base):
    __tablename__ = "sci_corrections"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    lane: Mapped[str] = mapped_column(Text, nullable=False)
    corrects_silver_table: Mapped[str] = mapped_column(Text, nullable=False)
    corrects_silver_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_values: Mapped[dict] = mapped_column(JSONB_COL, nullable=False)
    corrected_by: Mapped[str | None] = mapped_column(ForeignKey("sci_labs.id"))
    corrected_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    new_dataset_version_id: Mapped[str | None] = mapped_column(ForeignKey("sci_dataset_versions.id"))


class SciManifest(Base):
    __tablename__ = "sci_manifests"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    lane: Mapped[str] = mapped_column(Text, nullable=False)
    result_name: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("sci_dataset_versions.id"), nullable=False)
    code_ref: Mapped[str] = mapped_column(Text, nullable=False)  # git sha / container digest
    env_hash: Mapped[str] = mapped_column(Text, nullable=False)
    run_params: Mapped[dict] = mapped_column(JSONB_COL, nullable=False)


class SciAttestation(Base):
    __tablename__ = "sci_attestations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    lane: Mapped[str] = mapped_column(Text, nullable=False)
    attests_silver_table: Mapped[str] = mapped_column(Text, nullable=False)
    attests_silver_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    attestor_lab_id: Mapped[str] = mapped_column(ForeignKey("sci_labs.id"), nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)  # reproduced|partial|failed
    evidence_hash: Mapped[str | None] = mapped_column(Text)
    attested_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('reproduced','partial','failed')",
            name="ck_sci_attestation_verdict",
        ),
        Index("idx_sci_attestations_silver", "lane", "attests_silver_table", "attests_silver_id"),
    )


class SciBenchmark(Base):
    __tablename__ = "sci_benchmarks"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    lane: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # catbench_v1 ...
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("sci_dataset_versions.id"), nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    hidden_split_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")

    __table_args__ = (
        UniqueConstraint("lane", "name", name="uq_sci_benchmark_lane_name"),
        CheckConstraint("status IN ('open','closed')", name="ck_sci_benchmark_status"),
    )


class SciBenchmarkRun(Base):
    __tablename__ = "sci_benchmark_runs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    benchmark_id: Mapped[str] = mapped_column(ForeignKey("sci_benchmarks.id"), nullable=False)
    submitter: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Numeric, nullable=False)
    manifest_id: Mapped[str | None] = mapped_column(ForeignKey("sci_manifests.id"))
    submitted_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SciElectroMeasurement(Base):
    """Silver: cleaned electrocatalysis measurements with provenance attached."""

    __tablename__ = "sci_electro_measurements"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("sci_bronze_artifacts.id"), nullable=False)
    lab_id: Mapped[str] = mapped_column(ForeignKey("sci_labs.id"), nullable=False)
    calibration_id: Mapped[str | None] = mapped_column(ForeignKey("sci_calibrations.id"))
    conditions: Mapped[dict] = mapped_column(JSONB_COL, nullable=False)  # lane-schema'd: temp_c, pressure_bar, ...
    measured_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))

    composition: Mapped[str | None] = mapped_column(Text)
    surface_facet: Mapped[str | None] = mapped_column(Text)
    adsorbate: Mapped[str | None] = mapped_column(Text)
    dft_adsorption_ev: Mapped[float | None] = mapped_column(Numeric)
    experimental_tof: Mapped[float | None] = mapped_column(Numeric)  # s^-1
    faradaic_efficiency: Mapped[float | None] = mapped_column(Numeric)  # 0–100 %
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    artifact: Mapped[SciBronzeArtifact] = relationship("SciBronzeArtifact")
    lab: Mapped[SciLab] = relationship("SciLab")
    calibration: Mapped[SciCalibration | None] = relationship("SciCalibration")

    __table_args__ = (
        CheckConstraint(
            "faradaic_efficiency IS NULL OR (faradaic_efficiency >= 0 AND faradaic_efficiency <= 100)",
            name="ck_sci_electro_fe_range",
        ),
        Index("idx_sci_electro_artifact", "artifact_id"),
        Index("idx_sci_electro_composition", "composition"),
        Index("idx_sci_electro_adsorbate", "adsorbate"),
    )


class SciElectroGold(Base):
    """Gold: benchmark-ready mart rows pinned to a dataset version."""

    __tablename__ = "sci_electro_gold"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    silver_id: Mapped[str] = mapped_column(ForeignKey("sci_electro_measurements.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("sci_dataset_versions.id"), nullable=False)

    composition: Mapped[str | None] = mapped_column(Text)
    surface_facet: Mapped[str | None] = mapped_column(Text)
    adsorbate: Mapped[str | None] = mapped_column(Text)
    faradaic_efficiency: Mapped[float | None] = mapped_column(Numeric)
    experimental_tof: Mapped[float | None] = mapped_column(Numeric)

    attestation_summary: Mapped[dict] = mapped_column(JSONB_COL, nullable=False)
    row_hash: Mapped[str] = mapped_column(Text, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    silver: Mapped[SciElectroMeasurement] = relationship("SciElectroMeasurement")
    version: Mapped[SciDatasetVersion] = relationship("SciDatasetVersion")

    __table_args__ = (
        Index("idx_sci_electro_gold_version", "dataset_version_id"),
    )
