"""TW-949 (TW-948-B1): persistence service behavior.

Runs against a stateful in-memory Supabase stand-in in the house
pattern (test_agentperms_service.py). Behaviors pinned here:
- every read and write is scoped to (org, agent, workspace);
  another org or agent sees nothing and can parent nothing
  across the boundary, and a foreign workspace_id override is
  refused (fix round, Rosa must-fix 1),
- lineage: a child session points at its parent and a chain walk
  returns the full order, root first,
- duplicate fire identity is rejected at the service layer via
  targeted queries (the migration UNIQUE is the database layer,
  pinned separately),
- message reads AND the append return are the masked projection
  only; the raw body lives in the bronze column, reachable only
  via get_message_bronze, and its keyed hash matches at
  ingestion (Rosa must-fix 3, Rex F1 and F4),
- mask_text covers every TW-552 identifier category (Rex F3),
- continuity merges surfaced ids without duplicates, with its
  free-text fields masked like the projections (Rex F6),
- staged writes land pending, body and target fields masked
  (Rex F5).
"""

import hashlib
import os

import pytest

os.environ.setdefault("TWISTOR_PERSISTENCE_HASH_KEY", "tw949-test-key")

from app.persistence import models, service  # noqa: E402

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
        # The append return is the masked projection: no bronze,
        # no raw search text, ever (Rosa must-fix 3, Rex F1).
        assert "body_bronze" not in row
        assert "search_text" not in row
        # The hash is the keyed ingestion hash, never a bare
        # SHA-256 of the body (Rex F4).
        assert row["body_hash"] == models.hash_body(body, org_id=ORG, agent_id=AGENT)
        assert row["body_hash"] != hashlib.sha256(body.encode()).hexdigest()
        assert row["body_hash"].startswith("hmac-sha256:")
        assert "720-555-0142" not in row["body_masked"]
        assert "sam@example.com" not in row["body_masked"]
        # Bronze storage is proven through the named audit path.
        bronze = service.get_message_bronze(db, ORG, AGENT, row["id"])
        assert bronze is not None and bronze["body_bronze"] == body
        assert service.get_message_bronze(db, OTHER_ORG, AGENT, row["id"]) is None
        projections = service.list_messages(db, ORG, AGENT, session["id"])
        assert len(projections) == 1
        assert "body_bronze" not in projections[0]
        assert projections[0]["body_masked"] == row["body_masked"]
        assert projections[0]["seq"] == 1

    def test_short_message_hash_is_not_dictionary_reversible(self, db):
        # A body that IS one identifier: with a bare SHA-256 the
        # shipped hash would equal sha256(identifier). Keyed, it
        # must not (Rex F4 probe).
        session = _session(db)
        body = "720-555-0142"
        row = service.append_message(
            db, ORG, AGENT, session["id"], models.MessageAppend(role="user", body=body)
        )
        bare = hashlib.sha256(body.encode()).hexdigest()
        assert row["body_hash"] != bare
        assert not row["body_hash"].endswith(bare)
        staged = service.stage_write(
            db,
            ORG,
            AGENT,
            models.StagedWriteCreate(
                kind="memory", target_key="owner.phone", proposed_body=body
            ),
        )
        assert staged["proposed_body_hash"] != bare
        assert staged["proposed_body_hash"].startswith("hmac-sha256:")

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

    def test_stage_write_masks_body_and_target_fields(self, db):
        # Rex F5 probe: address, UEI and 7-digit phone in the
        # body, raw email in the target key. None may be stored.
        row = service.stage_write(
            db,
            ORG,
            AGENT,
            models.StagedWriteCreate(
                kind="memory",
                target_key="sam@example.com",
                target_ref="reach sam@example.com at 555-0142",
                proposed_body=(
                    "Owner at 1259 S Dallas Ct, UEI K7X9P2QW4RT8, call 555-0142"
                ),
            ),
            staged_by=AGENT,
        )
        stored = " ".join(
            str(row[k] or "")
            for k in ("target_key", "target_ref", "proposed_body_masked")
        )
        for secret in (
            "sam@example.com",
            "Dallas",
            "K7X9P2QW4RT8",
            "555-0142",
            "1259",
        ):
            assert secret not in stored


class TestWorkspaceScoping:
    """Rosa must-fix 1 regression: the probe that failed the gate.

    create_session(workspace_id='someone-elses-workspace') used
    to succeed and the row was visible under the default scope.
    """

    def test_foreign_workspace_override_refused_on_create_session(self, db):
        with pytest.raises(models.PersistenceError):
            service.create_session(
                db,
                ORG,
                AGENT,
                models.SessionCreate(),
                workspace_id="someone-elses-workspace",
            )
        assert service.list_sessions(db, ORG, AGENT) == []

    def test_foreign_workspace_override_refused_on_job_and_stage(self, db):
        with pytest.raises(models.PersistenceError):
            service.create_job(
                db,
                ORG,
                AGENT,
                models.JobCreate(name="j", prompt="p"),
                workspace_id="someone-elses-workspace",
            )
        with pytest.raises(models.PersistenceError):
            service.stage_write(
                db,
                ORG,
                AGENT,
                models.StagedWriteCreate(
                    kind="memory", target_key="k", proposed_body="b"
                ),
                workspace_id="someone-elses-workspace",
            )

    def test_matching_workspace_override_accepted(self, db):
        ws = models.workspace_id_for(ORG, AGENT)
        row = service.create_session(
            db, ORG, AGENT, models.SessionCreate(), workspace_id=ws
        )
        assert row["workspace_id"] == ws

    def test_row_stamped_with_foreign_workspace_is_invisible(self, db):
        # A bypass writer stamps a row with the same org and
        # agent but a foreign workspace: scoped reads filter on
        # the derived workspace, so the row cannot be read back
        # through this service at all.
        foreign = {
            "id": "agt_sessions-foreign",
            "org_id": ORG,
            "agent_identity_id": AGENT,
            "workspace_id": "someone-elses-workspace",
            "parent_session_id": None,
            "channel": "chat",
            "status": "active",
            "title": "foreign",
            "metadata": {},
            "started_at": "2026-11-05T00:00:00+00:00",
            "ended_at": None,
            "created_at": "2026-11-05T00:00:00+00:00",
            "updated_at": "2026-11-05T00:00:00+00:00",
        }
        db.tables.setdefault("agt_sessions", []).append(foreign)
        assert service.get_session(db, ORG, AGENT, "agt_sessions-foreign") is None
        assert service.list_sessions(db, ORG, AGENT) == []


class TestFireTargetedQueries:
    def test_duplicate_fire_key_across_scopes_still_rejected(self, db):
        # fire_key uniqueness is global (migration UNIQUE), so
        # the targeted eq check refuses a key recorded under
        # another org instead of scanning or missing it.
        job = _job(db)
        service.record_fire(
            db,
            ORG,
            AGENT,
            job["id"],
            models.FireCreate(fire_key="fire-1", scheduled_for="2026-11-05T07:00:00Z"),
        )
        other_job = _job(db, org=OTHER_ORG, name="other-scan")
        with pytest.raises(models.DuplicateFireError):
            service.record_fire(
                db,
                OTHER_ORG,
                AGENT,
                other_job["id"],
                models.FireCreate(
                    fire_key="fire-1", scheduled_for="2026-11-06T07:00:00Z"
                ),
            )


class TestMaskingCategories:
    """One pin per TW-552 category Rex probed (finding F3)."""

    @pytest.mark.parametrize(
        "body,secret",
        [
            ("mail sam@example.com today", "sam@example.com"),
            ("mail sam @ example.com today", "example.com"),
            ("mail sam at example dot com today", "example"),
            ("office 1259 S Dallas Ct suite 4", "Dallas"),
            ("office 4821 Elm Street", "Elm Street"),
            ("mail to PO Box 1234 please", "PO Box 1234"),
            ("born 03/15/1990 in Denver", "03/15/1990"),
            ("born 1990-03-15 in Denver", "1990-03-15"),
            ("born March 15, 1990 in Denver", "March 15"),
            ("ssn 123-45-6789 on file", "123-45-6789"),
            ("ssn 123 45 6789 on file", "123 45 6789"),
            ("ein 12-3456789 on file", "12-3456789"),
            ("call 720-555-0142 today", "720-555-0142"),
            ("call (720) 555-0142 today", "555-0142"),
            ("call 7205550142 today", "7205550142"),
            ("call 555-0142 today", "555-0142"),
            ("call 5550142 today", "5550142"),
            ("zip 80247 on file", "80247"),
            ("zip 80247-1234 on file", "80247"),
            ("uei K7X9P2QW4RT8 on file", "K7X9P2QW4RT8"),
            ("acct 123456789012 on file", "123456789012"),
        ],
    )
    def test_category_is_masked(self, body, secret):
        assert secret not in models.mask_text(body)

    def test_ordinary_words_pass_through(self):
        text = "the estimate followup is overdue and the crew is booked"
        assert models.mask_text(text) == text


class TestFreeTextMasking:
    """Rex F6, same-pipeline half: non-message free text."""

    def test_session_title_and_metadata_masked(self, db):
        row = service.create_session(
            db,
            ORG,
            AGENT,
            models.SessionCreate(title="Call sam@example.com about 555-0142"),
            metadata={"note": "owner ssn 123-45-6789", "count": 3},
        )
        assert "sam@example.com" not in row["title"]
        assert "555-0142" not in row["title"]
        assert "123-45-6789" not in row["metadata"]["note"]
        assert row["metadata"]["count"] == 3

    def test_continuity_summary_and_items_masked(self, db):
        # Rex F6 probe: raw email and SSN into continuity.
        job = _job(db)
        row = service.update_continuity(
            db,
            ORG,
            AGENT,
            job["id"],
            last_fire_id=None,
            summary="Emailed sam@example.com about ssn 123-45-6789",
            surfaced_item_ids=["sam@example.com", "lead-9"],
        )
        assert "sam@example.com" not in row["last_run_summary"]
        assert "123-45-6789" not in row["last_run_summary"]
        assert "sam@example.com" not in row["surfaced_item_ids"]
        assert "lead-9" in row["surfaced_item_ids"]
        fetched = service.get_continuity(db, ORG, AGENT, job["id"])
        assert "sam@example.com" not in fetched["last_run_summary"]
