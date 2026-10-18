"""High-Tc lane ORM mirrors (TW-593 Track 2).

SQLAlchemy representations of sci_hightc_measurements / sci_hightc_gold,
mirroring supabase/migration_sci_high_tc_v1.sql constraint-for-constraint.
These are the data-logic test harness (SQLite) and the typed contract for
Dre's integration pass; the hot serving path uses the Supabase client via
router.py. FK targets in the shared core (sci_bronze_artifacts,
sci_calibrations, sci_labs, sci_dataset_versions) are referenced by table
name only — the electrocatalysis lane owns those models.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    insert,
    select,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

STRUCTURES = (
    "cuprate",
    "iron_pnictide",
    "iron_chalcogenide",
    "hydride",
    "nickelate",
    "perovskite",
    "mgb2",
    "conventional",
)

MEASUREMENT_METHODS = ("resistivity", "susceptibility", "specific_heat", "other")


class HighTcMeasurement(Base):
    """Silver: one cleaned Tc measurement with provenance attached."""

    __tablename__ = "sci_hightc_measurements"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_id = Column(
        Uuid,
        ForeignKey("sci_bronze_artifacts.id"),
        nullable=False,
    )
    lab_id = Column(Uuid, ForeignKey("sci_labs.id"), nullable=True)
    calibration_id = Column(
        Uuid, ForeignKey("sci_calibrations.id"), nullable=True
    )
    conditions = Column(JSON, nullable=False, default=dict)
    measured_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    composition = Column(Text, nullable=False)
    structure = Column(Text, nullable=False)
    synthesis_route = Column(Text, nullable=True)
    tc_k = Column(Numeric, nullable=False)
    measurement_method = Column(Text, nullable=False)
    pressure_gpa = Column(Numeric, nullable=False, default=0)
    synthetic = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        # length() is portable (SQLite + Postgres); the migration's
        # char_length() is the Postgres-canonical spelling of the same check.
        CheckConstraint("length(composition) > 0", name="ck_hightc_composition"),
        CheckConstraint(
            "structure IN ('cuprate', 'iron_pnictide', 'iron_chalcogenide', "
            "'hydride', 'nickelate', 'perovskite', 'mgb2', 'conventional')",
            name="ck_hightc_structure",
        ),
        CheckConstraint("tc_k > 0 AND tc_k < 400", name="ck_hightc_tc_range"),
        CheckConstraint(
            "measurement_method IN ('resistivity', 'susceptibility', "
            "'specific_heat', 'other')",
            name="ck_hightc_method",
        ),
        CheckConstraint("pressure_gpa >= 0", name="ck_hightc_pressure"),
        Index("idx_hightc_meas_structure", "structure"),
        Index("idx_hightc_meas_tc", "tc_k"),
        Index("idx_hightc_meas_pressure", "pressure_gpa"),
    )


class HighTcGold(Base):
    """Gold: immutable benchmark-ready snapshot rows."""

    __tablename__ = "sci_hightc_gold"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    dataset_version_id = Column(
        Uuid,
        ForeignKey("sci_dataset_versions.id"),
        nullable=False,
    )
    silver_id = Column(
        Uuid,
        ForeignKey("sci_hightc_measurements.id"),
        nullable=False,
    )
    composition = Column(Text, nullable=False)
    structure = Column(Text, nullable=False)
    tc_k = Column(Numeric, nullable=False)
    pressure_gpa = Column(Numeric, nullable=False, default=0)
    measurement_method = Column(Text, nullable=False)
    synthetic = Column(Boolean, nullable=False, default=False)
    provenance_hash = Column(Text, nullable=False)
    attestation_summary = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id", "silver_id", name="uq_hightc_gold_version_silver"
        ),
        Index("idx_hightc_gold_version", "dataset_version_id"),
        Index("idx_hightc_gold_tc", "tc_k"),
    )


def _forbid_gold_mutation(mapper, connection, target):  # noqa: ARG001
    raise RuntimeError(
        "sci_hightc_gold is immutable: issue a correction and mint a new "
        "dataset version (mirrors the DB trigger trg_hightc_gold_no_mutate)"
    )


event.listen(HighTcGold, "before_update", _forbid_gold_mutation)
event.listen(HighTcGold, "before_delete", _forbid_gold_mutation)


def supersede_gold(
    session,
    *,
    gold_table: Table,
    old_version_id,
    new_version_id,
    overrides: dict,
) -> int:
    """Correction supersession: copy a gold version forward with corrections.

    Never mutates history: inserts fresh rows under ``new_version_id`` copied
    from ``old_version_id``'s rows, applying per-silver-row ``overrides``
    (``{silver_id: {column: corrected_value}}``). Returns the row count.
    """
    rows = session.execute(
        select(gold_table).where(gold_table.c.dataset_version_id == old_version_id)
    ).mappings()
    inserted = 0
    for row in rows:
        payload = dict(row)
        payload.pop("id", None)
        payload["dataset_version_id"] = new_version_id
        silver_id = payload["silver_id"]
        if silver_id in overrides:
            payload.update(overrides[silver_id])
        session.execute(insert(gold_table).values(**payload))
        inserted += 1
    return inserted
