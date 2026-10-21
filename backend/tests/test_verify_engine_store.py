"""TW-658 fix round: the Supabase store, driven through a fake client.

Rosa's TW-658 review (M3, M4): the in-memory double masked two
production-store defects. vfy_sessions has no document_type column
in the v1+v2 schema, so the class the session was created with
never survived a PostgREST round trip and submit_document died on
a KeyError; and velocity attempts shared the signal_type
"velocity" with the pipeline's decision summary, so list_attempts
crashed on the summary's detail shape after any session decided.

The fake client below mirrors the vfy_ row shapes PostgREST
returns: an insert stores the payload plus server defaults, a
select returns exactly the stored keys. The class carrier fix is
migration_verify_v3.sql (the column) plus the store writing it at
session creation; these tests pin both halves.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

from app.verify.engine.consent import grant_consent
from app.verify.engine.crypto import AesGcmEncryptor
from app.verify.engine.hashing import FieldHasher
from app.verify.engine.pipeline import VerificationPipeline
from app.verify.engine.staging import InMemoryStaging
from app.verify.engine.store import SupabaseVerifyStore
from app.verify.engine.velocity import AttemptRecord
from tests.test_verify_engine_pipeline import AMA_DOC, AMA_SELFIE, _frames_for, _ports

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

# Server-side defaults a PostgREST insert fills in (v1 schema).
_DEFAULTS = {
    "vfy_engine_versions": lambda: {"id": uuid.uuid4().hex, "is_active": True},
    "vfy_sessions": lambda: {
        "id": uuid.uuid4().hex,
        "status": "started",
        "decision_by": None,
        "confidence_band": None,
        "decided_at": None,
    },
    "vfy_consents": lambda: {"id": uuid.uuid4().hex, "revoked_at": None},
    "vfy_documents": lambda: {"id": uuid.uuid4().hex},
    "vfy_biometrics": lambda: {"id": uuid.uuid4().hex, "destroyed_at": None},
    "vfy_signals": lambda: {"id": uuid.uuid4().hex},
    "vfy_identity_anchors": lambda: {},
    "vfy_age_attestations": lambda: {},
    "vfy_verification_states": lambda: {"id": uuid.uuid4().hex},
    "vfy_retention_actions": lambda: {"id": uuid.uuid4().hex},
}


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table):
        self._client = client
        self._table = table
        self._op = None
        self._payload = None
        self._on_conflict = None
        self._filters = []

    def insert(self, row):
        self._op, self._payload = "insert", row
        return self

    def select(self, _cols="*"):
        self._op = "select"
        return self

    def update(self, fields):
        self._op, self._payload = "update", fields
        return self

    def upsert(self, row, on_conflict=None):
        self._op, self._payload, self._on_conflict = "upsert", row, on_conflict
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def is_(self, col, val):
        self._filters.append((col, None if val == "null" else val))
        return self

    def _matches(self, row):
        return all(row.get(col) == val for col, val in self._filters)

    def execute(self):
        rows = self._client.tables.setdefault(self._table, [])
        if self._op == "insert":
            stored = {**_DEFAULTS[self._table](), **self._payload}
            rows.append(stored)
            return _Result([dict(stored)])
        if self._op == "upsert":
            key = self._on_conflict
            for existing in rows:
                if existing.get(key) == self._payload.get(key):
                    existing.update(self._payload)
                    return _Result([dict(existing)])
            stored = {**_DEFAULTS[self._table](), **self._payload}
            rows.append(stored)
            return _Result([dict(stored)])
        if self._op == "update":
            updated = []
            for row in rows:
                if self._matches(row):
                    row.update(self._payload)
                    updated.append(dict(row))
            return _Result(updated)
        return _Result([dict(r) for r in rows if self._matches(r)])


class FakeSupabaseClient:
    """The slice of the supabase-py surface the store uses."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}

    def table(self, name):
        return _Query(self, name)


def _supabase_pipeline(store) -> VerificationPipeline:
    from app.verify.engine.config import EngineConfig

    return VerificationPipeline(
        store=store,
        staging=InMemoryStaging(),
        hasher=FieldHasher(b"tw658-store-test-key"),
        encryptor=AesGcmEncryptor(bytes(range(32))),
        ports=_ports(),
        config=EngineConfig(production_capture_enabled=True),
        rng=random.Random(7),
        clock=lambda: NOW,
    )


class TestSessionClassCarrier:
    def test_document_type_survives_a_postgrest_round_trip(self):
        store = SupabaseVerifyStore(FakeSupabaseClient())
        version_id = store.ensure_engine_version("v-test", {}, {})
        created = store.create_session(
            user_id="u-ama",
            document_type="passport",
            tier_requested=2,
            engine_version_id=version_id,
        )
        fetched = store.get_session(created["id"])
        # Rosa's M3 probe: the raw row could never contain the
        # class, and submit_document KeyErrored on it.
        assert fetched["document_type"] == "passport"

    def test_full_flow_over_the_supabase_store(self):
        client = FakeSupabaseClient()
        store = SupabaseVerifyStore(client)
        pipe = _supabase_pipeline(store)

        grant_consent(store, "u-ama", "passport")
        s1 = pipe.start_session("u-ama", "passport")
        assert pipe.submit_document(s1.id, "u-ama", AMA_DOC) == "document_received"
        challenges = pipe.begin_liveness(s1.id, "u-ama")
        v1 = pipe.submit_selfie(s1.id, "u-ama", AMA_SELFIE, _frames_for(challenges))
        assert v1.status == "passed"

        # A second member presenting the same face: the duplicate
        # search routes to review, and reaching the verdict at all
        # proves list_attempts survived the first session's decided
        # velocity summary row (Rosa's M4 probe crashed here).
        grant_consent(store, "u-kofi", "passport")
        s2 = pipe.start_session("u-kofi", "passport")
        pipe.submit_document(s2.id, "u-kofi", AMA_DOC)
        v2 = pipe.submit_selfie(s2.id, "u-kofi", AMA_SELFIE, _frames_for([]))
        assert v2.status == "review"

        docs = client.tables["vfy_documents"]
        assert {d["document_type"] for d in docs} == {"passport"}
        bios = client.tables["vfy_biometrics"]
        assert len(bios) == 1
        assert bios[0]["consent_id"] is not None


class TestVelocityLedgerShapes:
    def test_attempts_do_not_collide_with_the_decision_summary(self):
        store = SupabaseVerifyStore(FakeSupabaseClient())
        store.record_attempt("s-1", AttemptRecord("document", "doc-hash", NOW))
        store.record_attempt("s-1", AttemptRecord("device", "dev-hash", NOW))
        # The pipeline's per-decision summary signal, same table.
        store.add_signal(
            {
                "session_id": "s-1",
                "signal_type": "velocity",
                "score": None,
                "detail": {
                    "exceeded": [],
                    "document": "doc-hash",
                    "device": "dev-hash",
                    "network": None,
                },
            }
        )
        attempts = store.list_attempts()
        assert {(a.kind, a.key) for a in attempts} == {
            ("document", "doc-hash"),
            ("device", "dev-hash"),
        }

    def test_attempts_persist_under_their_own_signal_type(self):
        client = FakeSupabaseClient()
        store = SupabaseVerifyStore(client)
        store.record_attempt("s-1", AttemptRecord("document", "doc-hash", NOW))
        rows = client.tables["vfy_signals"]
        assert len(rows) == 1
        assert rows[0]["signal_type"] == "velocity_attempt"
        assert rows[0]["detail"]["kind"] == "document"
