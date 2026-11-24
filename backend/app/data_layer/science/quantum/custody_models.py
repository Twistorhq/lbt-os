"""Quantum custody ORM mirrors (TW-1329).

SQLAlchemy representations of sci_quantum_jobs,
sci_quantum_backend_snapshots, and sci_quantum_alerts, mirroring
supabase/migration_sci_quantum_custody_v1.sql constraint-for-constraint.
Same pattern as models.py: these are the data-logic test harness
(SQLite) and the typed contract; the production sink is Supabase
Postgres, where the migration owns the real tables.

The custody rule these tables enforce (TW-1328): the provider is a
calculator, not a filing cabinet. A job row exists BEFORE submission
(intent), carries the provider job ID from the moment submission
returns, and reaches 'custodied' only when the result payload and a
fresh backend snapshot are both in bronze.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
)

from .models import Base

JOB_STATUSES = (
    "intent",
    "submitted",
    "running",
    "completed",
    "custodied",
    "failed",
    "orphaned",
)

ALERT_KINDS = ("auth_failure", "reconcile_gap", "diff_gap", "export_failure")

SNAPSHOT_KINDS = ("properties", "configuration")


class SciQuantumJob(Base):
    """One quantum job's custody lifecycle, written at intent time."""

    __tablename__ = "sci_quantum_jobs"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    provider = Column(Text, nullable=False)
    backend = Column(Text, nullable=False)
    provider_job_id = Column(Text, nullable=True)
    circuit_qasm = Column(Text, nullable=False)
    circuit_sha256 = Column(Text, nullable=False)
    shots = Column(Integer, nullable=False)
    status = Column(Text, nullable=False, default="intent")
    intent_recorded_at = Column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    custodied_at = Column(DateTime(timezone=True), nullable=True)
    result_artifact_id = Column(
        Uuid, ForeignKey("sci_bronze_artifacts.id"), nullable=True
    )
    snapshot_artifact_id = Column(
        Uuid, ForeignKey("sci_bronze_artifacts.id"), nullable=True
    )
    job_metadata = Column(JSON, nullable=False, default=dict)
    provenance = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        CheckConstraint("length(provider) > 0", name="ck_quantum_jobs_provider"),
        CheckConstraint("length(backend) > 0", name="ck_quantum_jobs_backend"),
        CheckConstraint("shots >= 0", name="ck_quantum_jobs_shots"),
        CheckConstraint(
            "status IN ('intent', 'submitted', 'running', 'completed', "
            "'custodied', 'failed', 'orphaned')",
            name="ck_quantum_jobs_status",
        ),
        Index(
            "uq_quantum_jobs_provider_job",
            "provider",
            "provider_job_id",
            unique=True,
        ),
        Index("idx_quantum_jobs_status", "status"),
        Index("idx_quantum_jobs_provider_backend", "provider", "backend"),
    )


class SciQuantumBackendSnapshot(Base):
    """Ledger row for one backend telemetry capture in bronze."""

    __tablename__ = "sci_quantum_backend_snapshots"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    provider = Column(Text, nullable=False)
    backend = Column(Text, nullable=False)
    snapshot_date = Column(Date, nullable=False)
    kind = Column(Text, nullable=False)
    artifact_id = Column(Uuid, ForeignKey("sci_bronze_artifacts.id"), nullable=False)
    content_hash = Column(Text, nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    source = Column(Text, nullable=False)
    provenance = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        CheckConstraint("length(provider) > 0", name="ck_quantum_snap_provider"),
        CheckConstraint("length(backend) > 0", name="ck_quantum_snap_backend"),
        CheckConstraint(
            "kind IN ('properties', 'configuration')", name="ck_quantum_snap_kind"
        ),
        Index("idx_quantum_snap_series", "provider", "backend", "snapshot_date"),
    )


class SciQuantumAlert(Base):
    """A custody alert: auth failure or reconciliation gap, paged same-day."""

    __tablename__ = "sci_quantum_alerts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    provider = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    detail = Column(JSON, nullable=False, default=dict)
    raised_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("length(provider) > 0", name="ck_quantum_alerts_provider"),
        CheckConstraint(
            "kind IN ('auth_failure', 'reconcile_gap', 'diff_gap', 'export_failure')",
            name="ck_quantum_alerts_kind",
        ),
        Index("idx_quantum_alerts_open", "provider", "kind"),
    )
