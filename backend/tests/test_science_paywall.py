# Prepared by Twistor Holdings LLC
"""TW-599 Wave 1 paywall tests: tiers, delivery matrix, fees, subscription
gate, dynamic derivative pricing, Excel download tolls.

Unit-level with fakes: no live database, no network.
"""
from __future__ import annotations

import io

import pytest
from fastapi import HTTPException

from app.data_layer.science import paywall


# ------------------------------------------------------------------ fakes
class _Q:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def gte(self, col, val):
        self._rows = [r for r in self._rows if (r.get(col) or "") >= val]
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class FakeDB:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _Q(self._tables.get(name, []))


def _org_db(status="active"):
    return FakeDB({
        "organizations": [{"id": "org-1", "subscription_status": status}],
        "toll_events": [],
    })


# ------------------------------------------------------------------ tiers
def test_dataset_tier_boundaries():
    assert paywall.dataset_tier(0) == "small"
    assert paywall.dataset_tier(30_000) == "small"  # Justynn's small benchmark
    assert paywall.dataset_tier(99_999) == "small"
    assert paywall.dataset_tier(100_000) == "medium"
    assert paywall.dataset_tier(999_999) == "medium"
    assert paywall.dataset_tier(1_000_000) == "large"


def test_delivery_matrix_matches_justynn_spec():
    # large -> API only; medium -> API + Excel; small -> Excel (+API if needed)
    assert paywall.delivery_for_tier("small") == {"api": True, "excel": True}
    assert paywall.delivery_for_tier("medium") == {"api": True, "excel": True}
    assert paywall.delivery_for_tier("large") == {"api": True, "excel": False}


def test_excel_fees_inside_his_bands():
    # small $5-7, medium $7-10, large $20-25
    assert 500 <= paywall.excel_fee_cents("small") <= 700
    assert 700 <= paywall.excel_fee_cents("medium") <= 1000
    assert 2000 <= paywall.excel_fee_cents("large") <= 2500
    assert paywall.excel_fee_cents("small") == 600
    assert paywall.excel_fee_cents("medium") == 850
    assert paywall.excel_fee_cents("large") == 2250


def test_check_excel_delivery_blocks_large():
    paywall.check_excel_delivery("small", 30_000)  # no raise
    paywall.check_excel_delivery("medium", 500_000)  # no raise
    with pytest.raises(HTTPException) as exc_info:
        paywall.check_excel_delivery("large", 2_000_000)
    assert exc_info.value.status_code == 403


# ------------------------------------------------------- subscription gate
def test_gate_allows_active_subscription():
    assert paywall.require_active_subscription(_org_db("active"), "org-1") == "org-1"


def test_gate_blocks_everything_else():
    for status in ("past_due", "inactive", "trialing", None):
        db = _org_db(status) if status else FakeDB({"organizations": []})
        with pytest.raises(HTTPException) as exc_info:
            paywall.require_active_subscription(db, "org-1")
        assert exc_info.value.status_code == 402


def test_gate_blocks_unscoped_orgs():
    db = _org_db("active")
    for org in (None, "", "public", "unscoped", "anonymous"):
        with pytest.raises(HTTPException) as exc_info:
            paywall.require_active_subscription(db, org)
        assert exc_info.value.status_code == 402


def test_gate_lookup_failure_closes_gate():
    class BoomDB:
        def table(self, name):
            raise RuntimeError("db down")

    with pytest.raises(HTTPException) as exc_info:
        paywall.require_active_subscription(BoomDB(), "org-1")
    assert exc_info.value.status_code == 402  # fail closed


# ------------------------------------------------- dynamic derivative pricing
def _spend_db(spend_cents):
    return FakeDB({
        "toll_events": [
            {"org_id": "org-1", "amount_cents": spend_cents,
             "occurred_at": "2026-10-18T00:00:00+00:00"},
        ],
    })


def test_volume_multiplier_tiers():
    assert paywall.volume_rate_multiplier(_spend_db(0), "org-1") == 1.0
    assert paywall.volume_rate_multiplier(_spend_db(4_999), "org-1") == 1.0
    assert paywall.volume_rate_multiplier(_spend_db(5_000), "org-1") == 0.9
    assert paywall.volume_rate_multiplier(_spend_db(19_999), "org-1") == 0.9
    assert paywall.volume_rate_multiplier(_spend_db(20_000), "org-1") == 0.8
    assert paywall.volume_rate_multiplier(_spend_db(99_999), "org-1") == 0.8
    assert paywall.volume_rate_multiplier(_spend_db(100_000), "org-1") == 0.7
    assert paywall.volume_rate_multiplier(_spend_db(500_000), "org-1") == 0.7


def test_metered_toll_applies_volume_discount(monkeypatch):
    calls = []

    def fake_record(db, org_id, event_type, quantity=1.0, amount_cents=None,
                    idempotency_key=None, **kw):
        calls.append({"event_type": event_type, "amount_cents": amount_cents,
                      "quantity": quantity, "idempotency_key": idempotency_key})
        return {"ok": True}

    import app.services.toll_service as toll_service

    monkeypatch.setattr(toll_service, "record_toll_event", fake_record)
    # $0.05 query, no volume -> 5c
    paywall.metered_toll(_spend_db(0), "org-1", "data.science.query", "r1")
    assert calls[0]["amount_cents"] == 5
    # $0.05 query, $60 trailing spend -> 0.9x -> round(4.5) = 4c
    paywall.metered_toll(_spend_db(6_000), "org-1", "data.science.query", "r2")
    assert calls[1]["amount_cents"] == 4
    # bulk: 2.5k rows at $0.25/k -> 62.5c -> 62c at 1.0x
    paywall.metered_toll(_spend_db(0), "org-1", "data.science.bulk", "r3",
                         quantity=2.5)
    assert calls[2]["amount_cents"] == 62


def test_emit_download_toll_charges_tier_fee(monkeypatch):
    calls = []

    def fake_record(db, org_id, event_type, quantity=1.0, amount_cents=None,
                    idempotency_key=None, **kw):
        calls.append({"event_type": event_type, "amount_cents": amount_cents})
        return {"ok": True}

    import app.services.toll_service as toll_service

    monkeypatch.setattr(toll_service, "record_toll_event", fake_record)
    paywall.emit_download_toll(_org_db(), "org-1", "dl1", "small", "ver-1")
    assert calls[0]["event_type"] == "data.science.download"
    assert calls[0]["amount_cents"] == 600  # on top of the subscription


# ------------------------------------------------------------------ Excel
def test_build_excel_bytes_roundtrip():
    rows = [
        {"id": "a", "genotype": "Futura 75", "thc_pct": 0.18,
         "traits": {"nested": True}},
        {"id": "b", "genotype": "CRS-1", "thc_pct": 0.31, "traits": {}},
    ]
    content = paywall.build_excel_bytes(
        rows, ["id", "genotype", "thc_pct", "traits"],
        "climateshift-1", "1.0.0", "small", 600, synthetic=True,
    )
    assert content[:4] == b"PK\x03\x04"  # xlsx is a zip
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content))
    assert "DATA" in wb.sheetnames and "README" in wb.sheetnames
    ws = wb["DATA"]
    assert ws.cell(1, 1).value == "id"
    assert ws.cell(2, 2).value == "Futura 75"
    assert ws.cell(3, 3).value == 0.31
    readme = wb["README"]
    assert readme.cell(2, 2).value == "1.0.0"
    assert readme.cell(6, 2).value == "$6.00"
    assert "synthetic" in str(readme.cell(7, 2).value).lower()


def test_excel_response_headers():
    resp = paywall.excel_response(b"PK\x03\x04 fake", "s2s-trust", "1.0.0")
    assert resp.media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="s2s-trust-1.0.0.xlsx"' in resp.headers["content-disposition"]
