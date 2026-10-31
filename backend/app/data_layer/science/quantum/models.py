"""Quantum lane ORM mirrors (TW-838).

SQLAlchemy representations of sci_quantum_observations /
sci_quantum_spec_snapshots, mirroring supabase/migration_sci_quantum_v1.sql
constraint-for-constraint. These are the data-logic test harness (SQLite)
and the typed contract for the integration pass; the production sink is
the Supabase client. FK targets in the shared core (sci_bronze_artifacts,
sci_calibrations, sci_labs) are referenced by table name only, the same
pattern as the other science lanes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

METRICS = (
    "t1",
    "t2",
    "frequency",
    "readout_error",
    "readout_length",
    "prob_meas0_prep1",
    "prob_meas1_prep0",
    "gate_error_1q",
    "gate_error_2q",
    "gate_length",
)

PROVENANCE_KINDS = ("marketing", "vendor_spec")


class SciQuantumObservation(Base):
    """Silver: one normalized calibration observation from a bronze capture.

    One row per (device, qubit_or_edge, metric, measured_at): the IBM
    properties payload exploded into the brief's single observation schema
    (vendor, device, generation, qubit/edge, metric, value, unit,
    measured_at) with the bronze artifact as provenance.
    """

    __tablename__ = "sci_quantum_observations"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_id = Column(
        Uuid,
        ForeignKey("sci_bronze_artifacts.id"),
        nullable=False,
    )
    calibration_id = Column(Uuid, ForeignKey("sci_calibrations.id"), nullable=True)
    vendor = Column(Text, nullable=False)
    device = Column(Text, nullable=False)
    device_generation = Column(Text, nullable=True)
    qubit_or_edge = Column(Text, nullable=False)
    metric = Column(Text, nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(Text, nullable=True)
    measured_at = Column(DateTime(timezone=True), nullable=False)
    source_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        CheckConstraint("length(metric) > 0", name="ck_quantum_obs_metric"),
        CheckConstraint("length(device) > 0", name="ck_quantum_obs_device"),
        CheckConstraint("length(vendor) > 0", name="ck_quantum_obs_vendor"),
        Index("idx_quantum_obs_device_metric", "vendor", "device", "metric"),
        Index("idx_quantum_obs_measured", "measured_at"),
        Index("idx_quantum_obs_artifact", "artifact_id"),
    )


class SciQuantumSpecSnapshot(Base):
    """Silver (parallel): vendor-published specification values.

    Marketing-grade data (IonQ / Quantinuum / Rigetti published spec pages)
    lands here with a provenance flag, NEVER mixed with measured telemetry
    in sci_quantum_observations (brief, Section 5).
    """

    __tablename__ = "sci_quantum_spec_snapshots"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_id = Column(
        Uuid,
        ForeignKey("sci_bronze_artifacts.id"),
        nullable=False,
    )
    vendor = Column(Text, nullable=False)
    device = Column(Text, nullable=False)
    metric = Column(Text, nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(Text, nullable=True)
    provenance = Column(Text, nullable=False)
    source_url = Column(Text, nullable=False)
    retrieved_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        CheckConstraint(
            "provenance IN ('marketing', 'vendor_spec')",
            name="ck_quantum_spec_provenance",
        ),
        CheckConstraint("length(metric) > 0", name="ck_quantum_spec_metric"),
        Index("idx_quantum_spec_device", "vendor", "device"),
    )
