"""Tests for the TW-570 M3 white-label BI (Databox wedge) (Zeke Okafor).

Token-gated public embeds (aggregate-only), org theming, connector
framework, toll-metered via the M1 ledger. Follows the test_missed_call.py
FakeDB pattern: no network, no real DB.
"""
import hashlib
import unittest
from unittest import mock

from app.services import bi_connectors, bi_service

ORG_A = "org-bi-a"
ORG_B = "org-bi-b"
ORG_NAME = "Obi Trades Co"  # fixture named for family, per tradition


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._rows = list(db._tables.get(table, []))
        self._pending_insert = None
        self._pending_update = None
        self._pending_delete = False
        self._single = False

    def select(self, _cols="*"):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def neq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) != value]
        return self

    def gte(self, col, value):
        self._rows = [r for r in self._rows
                      if r.get(col) is not None and r.get(col) >= value]
        return self

    def lte(self, col, value):
        self._rows = [r for r in self._rows
                      if r.get(col) is not None and r.get(col) <= value]
        return self

    def lt(self, col, value):
        self._rows = [r for r in self._rows
                      if r.get(col) is not None and r.get(col) < value]
        return self

    def gt(self, col, value):
        self._rows = [r for r in self._rows
                      if r.get(col) is not None and r.get(col) > value]
        return self

    def in_(self, col, values):
        self._rows = [r for r in self._rows if r.get(col) in values]
        return self

    def order(self, col, desc=False):
        self._rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._single = True
        return self

    def insert(self, row):
        self._pending_insert = dict(row)
        return self

    def update(self, values):
        self._pending_update = dict(values)
        return self

    def delete(self):
        self._pending_delete = True
        return self

    def execute(self):
        if self._pending_insert is not None:
            row = self._pending_insert
            row.setdefault("id", f"row-{len(self._db._tables[self._table])}")
            # emulate UNIQUE(token_hash) like Postgres would
            if self._table == "bi_embeds":
                for existing in self._db._tables[self._table]:
                    if existing.get("token_hash") == row.get("token_hash"):
                        raise RuntimeError("duplicate key value violates unique constraint")
            self._db._tables[self._table].append(row)
            self._pending_insert = None
            if self._single:
                self._single = False
                return FakeResult(row)
            return FakeResult([row])
        if self._pending_update is not None:
            for r in self._rows:
                r.update(self._pending_update)
            self._pending_update = None
            if self._single:
                self._single = False
                return FakeResult(self._rows[0] if self._rows else None)
            return FakeResult(self._rows)
        if self._pending_delete:
            for r in list(self._rows):
                self._db._tables[self._table].remove(r)
            self._pending_delete = False
            return FakeResult([])
        if self._single:
            self._single = False
            return FakeResult(self._rows[0] if self._rows else None)
        return FakeResult(self._rows)


class FakeDB:
    TABLES = (
        "organizations", "bi_dashboards", "bi_embeds", "bi_metric_snapshots",
        "toll_events", "sales", "customers", "leads", "expenses",
        "integration_connections",
    )

    def __init__(self):
        self._tables = {t: [] for t in self.TABLES}

    def table(self, name):
        if name not in self._tables:
            raise RuntimeError(f"table {name} does not exist")
        return FakeQuery(self, name)


class ExplodingTollDB(FakeDB):
    """toll_events inserts raise — the embed must still serve (TW-208)."""

    def table(self, name):
        if name == "toll_events":
            raise RuntimeError("toll ledger down")
        return super().table(name)


def _seed_org(db, org_id):
    db._tables["organizations"].append({
        "id": org_id, "name": ORG_NAME, "plan": "trades_pro",
        "subscription_status": "active",
    })


def _seed_dashboard(db, org_id, dash_id="dash-1", is_public=True, widgets=None):
    db._tables["bi_dashboards"].append({
        "id": dash_id, "org_id": org_id, "name": "Shop Overview",
        "slug": "shop-overview", "is_public": is_public,
        "layout": {"widgets": widgets or [
            {"id": "w1", "metric": "revenue_30d", "title": "Revenue",
             "chart": "kpi", "connector": "internal"},
        ]},
        "theme": {"primary_color": "#ff5c1a"},
    })


def _seed_embed(db, org_id=ORG_A, dash_id="dash-1", token="raw-token-abc",
                revoked=False, expired=False):
    expires = None
    if expired:
        expires = "2000-01-01T00:00:00+00:00"
    db._tables["bi_embeds"].append({
        "id": "embed-1", "org_id": org_id, "dashboard_id": dash_id,
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "expires_at": expires, "revoked": revoked,
    })
    return token




class EmbedTokenTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        _seed_org(self.db, ORG_A)
        _seed_dashboard(self.db, ORG_A)
        self.token = _seed_embed(self.db)

    def test_unknown_token_returns_401(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            bi_service.render_embed(self.db, "nope-not-a-token")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_token_guessing_returns_401(self):
        import secrets

        from fastapi import HTTPException
        for _ in range(25):
            with self.assertRaises(HTTPException) as ctx:
                bi_service.render_embed(self.db, secrets.token_urlsafe(32))
            self.assertEqual(ctx.exception.status_code, 401)

    def test_revoked_token_returns_403(self):
        from fastapi import HTTPException
        db = FakeDB()
        _seed_org(db, ORG_A)
        _seed_dashboard(db, ORG_A)
        tok = _seed_embed(db, token="revoked-tok", revoked=True)
        with self.assertRaises(HTTPException) as ctx:
            bi_service.render_embed(db, tok)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_expired_token_returns_403(self):
        from fastapi import HTTPException
        db = FakeDB()
        _seed_org(db, ORG_A)
        _seed_dashboard(db, ORG_A)
        tok = _seed_embed(db, token="expired-tok", expired=True)
        with self.assertRaises(HTTPException) as ctx:
            bi_service.render_embed(db, tok)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_is_public_false_valid_token_returns_404(self):
        from fastapi import HTTPException
        db = FakeDB()
        _seed_org(db, ORG_A)
        _seed_dashboard(db, ORG_A, is_public=False)
        tok = _seed_embed(db, token="private-tok")
        with self.assertRaises(HTTPException) as ctx:
            bi_service.render_embed(db, tok)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_valid_token_renders_aggregates(self):
        payload = bi_service.render_embed(self.db, self.token)
        self.assertIn("widgets", payload)
        self.assertIn("theme", payload["dashboard"])
        self.assertEqual(payload["widgets"][0]["metric"], "revenue_30d")

    def test_embed_payload_has_no_pii(self):
        import json
        import re
        payload = bi_service.render_embed(self.db, self.token)
        blob = json.dumps(payload)
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", blob),
                          "email leaked into embed payload")
        self.assertIsNone(re.search(r"\+?1?\d{3}[-.\s]?\d{3}[-.\s]?\d{4}", blob),
                          "phone-like value leaked into embed payload")

    def test_cross_org_embed_isolation(self):
        # Org B snapshots exist; org A token must render zero org-B values.
        self.db._tables["organizations"].append({"id": ORG_B, "name": "Other"})
        self.db._tables["bi_metric_snapshots"].append({
            "org_id": ORG_B, "connector_key": "internal",
            "metric_key": "revenue_30d",
            "value": {"value": 999999.0, "delta_pct": None, "label": "X"},
        })
        payload = bi_service.render_embed(self.db, self.token)
        import json
        self.assertNotIn("999999", json.dumps(payload))

    def test_token_hash_stored_not_plaintext(self):
        embed_row, raw = bi_service.issue_embed_token(self.db, ORG_A, "dash-1")
        stored = self.db._tables["bi_embeds"][-1]
        self.assertEqual(stored["token_hash"],
                         hashlib.sha256(raw.encode()).hexdigest())
        self.assertNotIn(raw, str(stored))

    def test_token_uniqueness(self):
        seen = set()
        for _ in range(200):
            _row, raw = bi_service.issue_embed_token(self.db, ORG_A, "dash-1")
            self.assertNotIn(raw, seen)
            seen.add(raw)


class AggregateOnlyTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        _seed_org(self.db, ORG_A)

    def test_unknown_metric_key_fails_closed(self):
        _seed_dashboard(self.db, ORG_A, widgets=[
            {"id": "w9", "metric": "raw_customers", "title": "X",
             "chart": "kpi", "connector": "internal"},
        ])
        tok = _seed_embed(self.db, token="tok-unknown-metric")
        payload = bi_service.render_embed(self.db, tok)
        self.assertEqual(payload["widgets"][0]["metric"], "raw_customers")
        self.assertIn("error", payload["widgets"][0])

    def test_shape_violation_fails_closed(self):
        # A connector returning row-shaped data for a KPI metric must fail
        # closed, never leak the rows (Rosa Major 1).
        evil = {"value": {"name": "Alice", "email": "a@x.com",
                          "phone": "+13035550142"},
                "delta_pct": None, "label": "Revenue"}
        self.assertFalse(bi_service.validate_metric_value(
            {"key": "revenue_30d", "output": {"type": "kpi", "shape": {
                "value": "number", "delta_pct": "number|null",
                "label": "string"}}}, evil))

    def test_shape_validator_rejects_bools_as_numbers(self):
        # isinstance(True, int) is True in Python — must not pass as number.
        self.assertFalse(bi_service.validate_metric_value(
            {"key": "m", "output": {"type": "kpi", "shape": {
                "value": "number", "delta_pct": "number|null",
                "label": "string"}}},
            {"value": True, "delta_pct": None, "label": "x"}))

    def test_shape_validator_accepts_valid_kpi_and_trend(self):
        kpi_def = {"key": "m", "output": {"type": "kpi", "shape": {
            "value": "number", "delta_pct": "number|null", "label": "string"}}}
        self.assertTrue(bi_service.validate_metric_value(
            kpi_def, {"value": 120.5, "delta_pct": None, "label": "Rev"}))
        trend_def = {"key": "t", "output": {"type": "trend", "shape": {
            "points": [{"label": "string", "value": "number"}]}}}
        self.assertTrue(bi_service.validate_metric_value(
            trend_def, {"points": [{"label": "W1", "value": 10}]}))
        self.assertFalse(bi_service.validate_metric_value(
            trend_def, {"points": [{"label": "W1", "value": 10,
                                    "extra": "leak"}]}))


class ThemeValidationTests(unittest.TestCase):
    def test_valid_theme_passes(self):
        theme = {"primary_color": "#ff5c1a", "logo_url": "https://x.test/l.png",
                 "font_family": "Segoe UI, Arial"}
        self.assertEqual(bi_service.validate_theme(theme)["primary_color"],
                         "#ff5c1a")

    def test_bad_color_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            bi_service.validate_theme({"primary_color": "red"})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_unknown_key_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            bi_service.validate_theme({"evil": "1"})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_non_https_logo_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            bi_service.validate_theme({"logo_url": "javascript:alert(1)"})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_font_family_injection_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            bi_service.validate_theme(
                {"font_family": "Arial; background:url(evil)"})
        self.assertEqual(ctx.exception.status_code, 400)


class TollTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        _seed_org(self.db, ORG_A)
        _seed_dashboard(self.db, ORG_A)
        self.token = _seed_embed(self.db)

    def test_embed_view_emits_toll(self):
        bi_service.render_embed(self.db, self.token)
        tolls = self.db._tables["toll_events"]
        self.assertTrue(any(t["event_type"] == "bi.embed_view"
                            for t in tolls),
                        "embed view did not emit a toll event")

    def test_toll_failure_does_not_break_embed(self):
        db = ExplodingTollDB()
        _seed_org(db, ORG_A)
        _seed_dashboard(db, ORG_A)
        tok = _seed_embed(db)
        payload = bi_service.render_embed(db, tok)  # must not raise
        self.assertIn("widgets", payload)

    def test_connector_sync_emits_toll(self):
        bi_service.sync_connector(self.db, ORG_A, "internal")
        tolls = self.db._tables["toll_events"]
        self.assertTrue(any(t["event_type"] == "bi.connector_sync"
                            for t in tolls),
                        "connector sync did not emit a toll event")

    def test_connector_sync_toll_failure_does_not_500(self):
        db = ExplodingTollDB()
        _seed_org(db, ORG_A)
        result = bi_service.sync_connector(db, ORG_A, "internal")  # no raise
        self.assertIn("metrics", result)


class ConnectorFrameworkTests(unittest.TestCase):
    def test_registry_has_internal_and_stripe(self):
        reg = bi_connectors.BI_CONNECTOR_REGISTRY
        self.assertIn("internal", reg)
        self.assertIn("stripe", reg)

    def test_metric_definitions_carry_output_shapes(self):
        for key, conn in bi_connectors.BI_CONNECTOR_REGISTRY.items():
            for m in conn.metric_definitions():
                self.assertIn("key", m)
                self.assertIn("output", m, f"{key} metric missing output shape")

    def test_internal_connector_computes_aggregates(self):
        db = FakeDB()
        _seed_org(db, ORG_A)
        db._tables["sales"].append({
            "org_id": ORG_A, "amount": 100.0, "payment_status": "paid",
            "sold_at": "2026-10-10", "source": "web", "cost": 40.0,
            "profit": 60.0, "customer_id": "c1",
        })
        out = bi_connectors.BI_CONNECTOR_REGISTRY["internal"].compute(db, ORG_A)
        self.assertIn("revenue_30d", out)
        for v in out.values():
            self.assertNotIsInstance(v, list, "connector leaked row lists")

    def test_stale_snapshot_renders_updating_no_compute(self):
        # Public path never triggers compute or snapshot writes (Rosa Minor 5).
        # If render_embed touched connector.compute, the mock raises.
        db = FakeDB()
        _seed_org(db, ORG_A)
        _seed_dashboard(db, ORG_A)  # revenue_30d widget, no snapshots seeded
        tok = _seed_embed(db, token="tok-stale")
        evil = mock.Mock()
        evil.compute.side_effect = AssertionError("compute on public path")
        evil.metric_definitions.return_value = \
            bi_connectors.InternalConnector().metric_definitions()
        snapshots_before = len(db._tables["bi_metric_snapshots"])
        with mock.patch.dict(bi_connectors.BI_CONNECTOR_REGISTRY,
                             {"internal": evil}):
            payload = bi_service.render_embed(db, tok)
        self.assertEqual(payload["widgets"][0].get("status"), "updating")
        self.assertEqual(len(db._tables["bi_metric_snapshots"]),
                         snapshots_before)


class HttpEndpointTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from app.auth import AuthContext, get_auth
        from app.database import get_db
        from app.main import app

        self.db = FakeDB()
        _seed_org(self.db, ORG_A)
        _seed_org(self.db, ORG_B)
        _seed_dashboard(self.db, ORG_A, dash_id="dash-a")
        _seed_dashboard(self.db, ORG_B, dash_id="dash-b")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_auth] = lambda: AuthContext(
            user_id="user-1", org_id=ORG_A, plan="trades_pro", is_owner=True,
        )
        self.client = TestClient(app, raise_server_exceptions=False)
        self._overrides = (app, get_db, get_auth)

    def tearDown(self):
        app, get_db, get_auth = self._overrides
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth, None)

    def test_cross_org_dashboard_get_returns_404(self):
        r = self.client.get("/api/v1/bi/dashboards/dash-b")
        self.assertEqual(r.status_code, 404)

    def test_cross_org_dashboard_patch_returns_404(self):
        r = self.client.patch("/api/v1/bi/dashboards/dash-b",
                              json={"name": "Hijacked"})
        self.assertEqual(r.status_code, 404)

    def test_cross_org_dashboard_delete_returns_404(self):
        r = self.client.delete("/api/v1/bi/dashboards/dash-b")
        self.assertEqual(r.status_code, 404)

    def test_cross_org_embed_issue_returns_404(self):
        r = self.client.post("/api/v1/bi/dashboards/dash-b/embeds")
        self.assertEqual(r.status_code, 404)

    def test_cross_org_preview_returns_404(self):
        r = self.client.get("/api/v1/bi/dashboards/dash-b/preview")
        self.assertEqual(r.status_code, 404)

    def test_preview_renders_for_owner(self):
        r = self.client.get("/api/v1/bi/dashboards/dash-a/preview")
        self.assertEqual(r.status_code, 200)
        self.assertIn("widgets", r.json())

    def test_public_embed_unknown_token_401(self):
        r = self.client.get("/api/v1/bi/embed/does-not-exist")
        self.assertEqual(r.status_code, 401)

    def test_dashboard_crud_roundtrip(self):
        r = self.client.post("/api/v1/bi/dashboards", json={
            "name": "Weekly", "slug": "weekly",
            "theme": {"primary_color": "#123abc"}})
        self.assertEqual(r.status_code, 201)
        dash_id = r.json()["id"]
        r = self.client.get(f"/api/v1/bi/dashboards/{dash_id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["slug"], "weekly")


class MigrationPolicyTests(unittest.TestCase):
    def test_rls_policies_exist_for_all_bi_tables(self):
        import re
        from pathlib import Path
        sql = Path(__file__).resolve().parents[2] / "supabase" / \
            "migration_bi_whitelabel.sql"
        text = sql.read_text()
        for table in ("bi_dashboards", "bi_embeds", "bi_metric_snapshots"):
            pattern = (r'CREATE POLICY\s+"?' + table + r'_org_isolation"?\s+'
                       r'ON\s+' + table + r'.*?USING\s*\(\s*org_id\s*=\s*'
                       r'auth_org_id\(\)\s*\).*?WITH\s+CHECK\s*\(\s*org_id\s*='
                       r'\s*auth_org_id\(\)\s*\)')
            self.assertIsNotNone(
                re.search(pattern, text, re.IGNORECASE | re.DOTALL),
                f"org_isolation RLS policy missing or malformed for {table}")


if __name__ == "__main__":
    unittest.main()
