"""TW-949 (TW-948-B1): persistence service behavior.

Runs against a stateful in-memory Supabase stand-in in the house
pattern (test_agentperms_service.py). Behaviors pinned here:
- every read and write is scoped to (org, agent); another org or
  agent sees nothing and can parent nothing across the boundary,
- lineage: a child session points at its parent and a chain walk
  returns the full order, root first,
- duplicate fire identity is rejected at the service layer (the
  migration UNIQUE is the database layer, pinned separately),
- message reads return the masked projection only; the raw body
  lives in the bronze column and its hash matches at ingestion,
- continuity merges surfaced ids without duplicates,
- staged writes land pending, in masked shape only.
"""

import hashlib

import pytest

from app.persistence import models, service

ORG = "org-1"
OTHER_ORG = "org-2"
AGENT = "agent-1"
OTHER_AGENT = "agent-2"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table, mode, payload=None):
        self._db = db
        self._table = table
        self._mode = mode
        self._payload = payload
        self._filters = []
        self._order = None
        self._limit = None

    def eq(self, col, value):
        self._filters.append((col, value))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matching(self):
        rows = self._db.tables.get(self._table, [])
        return [
            r for r in rows if all(r.get(col) == value for col, value in self._filters)
        ]

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", self._db.next_id(self._table))
            self._db.tables.setdefault(self._table, []).append(row)
            return _Result([dict(row)])
        if self._mode == "update":
            updated = []
            for row in self._matching():
                row.update(self._payload)
                updated.append(dict(row))
            return _Result(updated)
        rows = [dict(r) for r in self._matching()]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows)


class _Table:
    def __init__(self, db, name):
        self._db = db
        self._name = name

    def select(self, _cols="*"):
        return _Query(self._db, self._name, "select")

    def insert(self, row):
        return _Query(self._db, self._name, "insert", row)

    def update(self, payload):
        return _Query(self._db, self._name, "update", payload)


class FakeDb:
    def __init__(self):
        self.tables = {}
        self._counters = {}

    def next_id(self, table):
        self._counters[table] = self._counters.get(table, 0) + 1
        return f"{table}-{self._counters[table]}"

    def table(self, name):
        return _Table(self, name)


@pytest.fixture
def db():
    return FakeDb()


def _session(db, org=ORG, agent=AGENT, **kwargs):
    return service.create_session(db, org, agent, models.SessionCreate(**kwargs))


def _job(db, org=ORG, agent=AGENT, name="morning-scan"):
    return service.create_job(
        db, org, agent, models.JobCreate(name=name, prompt="Scan the board.")
    )


class TestSessionScoping:
    def test_workspace_key_stamped(self, db):
        row = _session(db)
        assert row["workspace_id"] == models.workspace_id_for(ORG, AGENT)
        assert row["org_id"] == ORG and row["agent_identity_id"] == AGENT

    def test_other_org_and_agent_see_nothing(self, db):
        row = _session(db)
        assert service.get_session(db, OTHER_ORG, AGENT, row["id"]) is None
        assert service.get_session(db, ORG, OTHER_AGENT, row["id"]) is None
        assert service.list_sessions(db, OTHER_ORG, AGENT) == []

    def test_channel_filter(self, db):
        _session(db, channel="chat")
        _session(db, channel="phone")
        assert len(service.list_sessions(db, ORG, AGENT, channel="phone")) == 1


class TestLineage:
    def test_child_points_at_parent_and_chain_walks_in_order(self, db):
        root = _session(db, title="root")
        child = _session(db, title="child", parent_session_id=root["id"])
        grandchild = _session(db, title="grandchild", parent_session_id=child["id"])
        assert child["parent_session_id"] == root["id"]
        chain = service.walk_lineage(db, ORG, AGENT, grandchild["id"])
        assert [s["id"] for s in chain] == [root["id"], child["id"], grandchild["id"]]
        assert [s["title"] for s in chain] == ["root", "child", "grandchild"]

    def test_parent_in_another_scope_is_refused(self, db):
        foreign = _session(db, org=OTHER_ORG)
        with pytest.raises(models.PersistenceError):
            _session(db, parent_session_id=foreign["id"])

    def test_walk_of_single_session_is_itself(self, db):
        root = _session(db)
        assert [s["id"] for s in service.walk_lineage(db, ORG, AGENT, root["id"])] == [
            root["id"]
        ]


class TestMessages:
    def test_append_hashes_and_masks_and_projection_hides_bronze(self, db):
        session = _session(db)
        body = "Call me at 720-555-0142 or sam@example.com about the invoice"
        row = service.append_message(
            db, ORG, AGENT, session["id"], models.MessageAppend(role="user", body=body)
        )
        assert row["body_hash"] == hashlib.sha256(body.encode()).hexdigest()
        assert row["body_bronze"] == body
        assert "720-555-0142" not in row["body_masked"]
        assert "sam@example.com" not in row["body_masked"]
        projections = service.list_messages(db, ORG, AGENT, session["id"])
        assert len(projections) == 1
        assert "body_bronze" not in projections[0]
        assert projections[0]["body_masked"] == row["body_masked"]
        assert projections[0]["seq"] == 1

    def test_seq_increments_and_explicit_duplicate_seq_refused(self, db):
        session = _session(db)
        service.append_message(
            db, ORG, AGENT, session["id"], models.MessageAppend(role="user", body="one")
        )
        second = service.append_message(
            db,
            ORG,
            AGENT,
            session["id"],
            models.MessageAppend(role="assistant", body="two"),
        )
        assert second["seq"] == 2
        with pytest.raises(models.PersistenceError):
            service.append_message(
                db,
                ORG,
                AGENT,
                session["id"],
                models.MessageAppend(role="user", body="dup"),
                seq=1,
            )

    def test_append_to_foreign_session_refused(self, db):
        session = _session(db, org=OTHER_ORG)
        with pytest.raises(models.PersistenceError):
            service.append_message(
                db,
                ORG,
                AGENT,
                session["id"],
                models.MessageAppend(role="user", body="hi"),
            )

    def test_search_uses_masked_projection_and_scope(self, db):
        session = _session(db)
        service.append_message(
            db,
            ORG,
            AGENT,
            session["id"],
            models.MessageAppend(role="user", body="the estimate followup is overdue"),
        )
        hits = service.search_messages(db, ORG, AGENT, "estimate overdue")
        assert len(hits) == 1 and "body_bronze" not in hits[0]
        assert service.search_messages(db, OTHER_ORG, AGENT, "estimate") == []


class TestFires:
    def test_duplicate_fire_key_rejected_at_service_layer(self, db):
        job = _job(db)
        stmt = models.FireCreate(
            fire_key="fire-1", scheduled_for="2026-11-05T07:00:00Z"
        )
        service.record_fire(db, ORG, AGENT, job["id"], stmt)
        with pytest.raises(models.DuplicateFireError):
            service.record_fire(db, ORG, AGENT, job["id"], stmt)

    def test_same_job_same_scheduled_time_rejected(self, db):
        job = _job(db)
        service.record_fire(
            db,
            ORG,
            AGENT,
            job["id"],
            models.FireCreate(fire_key="fire-1", scheduled_for="2026-11-05T07:00:00Z"),
        )
        with pytest.raises(models.DuplicateFireError):
            service.record_fire(
                db,
                ORG,
                AGENT,
                job["id"],
                models.FireCreate(
                    fire_key="fire-2", scheduled_for="2026-11-05T07:00:00Z"
                ),
            )

    def test_fire_for_foreign_job_refused(self, db):
        job = _job(db, org=OTHER_ORG)
        with pytest.raises(models.PersistenceError):
            service.record_fire(
                db,
                ORG,
                AGENT,
                job["id"],
                models.FireCreate(
                    fire_key="fire-x", scheduled_for="2026-11-05T07:00:00Z"
                ),
            )


class TestContinuity:
    def test_continuity_merges_without_duplicates(self, db):
        job = _job(db)
        first = service.update_continuity(
            db,
            ORG,
            AGENT,
            job["id"],
            last_fire_id="fire-1",
            summary="run one",
            surfaced_item_ids=["lead-1", "lead-2"],
        )
        assert first["run_count"] == 1
        second = service.update_continuity(
            db,
            ORG,
            AGENT,
            job["id"],
            last_fire_id="fire-2",
            summary="run two",
            surfaced_item_ids=["lead-2", "lead-3"],
        )
        assert second["run_count"] == 2
        assert second["surfaced_item_ids"] == ["lead-1", "lead-2", "lead-3"]
        assert service.get_continuity(db, ORG, AGENT, job["id"])["id"] == first["id"]
        assert service.get_continuity(db, OTHER_ORG, AGENT, job["id"]) is None


class TestStagedWrites:
    def test_stage_write_lands_pending_masked(self, db):
        row = service.stage_write(
            db,
            ORG,
            AGENT,
            models.StagedWriteCreate(
                kind="memory",
                target_key="owner.preference",
                proposed_body="Owner prefers texts at 303-555-0199",
                pinned_target_hash="abc123",
            ),
            staged_by=AGENT,
        )
        assert row["status"] == "pending"
        assert "303-555-0199" not in row["proposed_body_masked"]
        assert "body_bronze" not in row and "proposed_body" not in row
        pending = service.list_staged_writes(db, ORG, AGENT, status="pending")
        assert [r["id"] for r in pending] == [row["id"]]
        assert service.list_staged_writes(db, OTHER_ORG, AGENT) == []

    def test_unknown_kind_refused_at_model_layer(self):
        with pytest.raises(models.PersistenceError):
            models.StagedWriteCreate(kind="webhook", target_key="k", proposed_body="b")
