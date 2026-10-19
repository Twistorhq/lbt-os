# Prepared by Twistor Holdings LLC
"""TW-606 paywall tests: dynamic volume curve, asset-value floors, audience
tracking, neutral API tolls, rate-limit tiers, subscription gate, Excel delivery.

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

    def select(self, *a, **kw):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def gte(self, col, val):
        self._rows = [r for r in self._rows if (r.get(col) or "") >= val]
        return self

    def order(self, col, desc=False):
        self._rows = sorted(self._rows, key=lambda r: r.get(col) or "",
                            reverse=bool(desc))
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
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


def test_download_curve_endpoints():
    # $0.01 at 0 downloads, $0.10 hard cap at 100,000, capped beyond
    assert paywall.download_price_cents(0) == 1
    assert paywall.download_price_cents(100_000) == 10
    assert paywall.download_price_cents(1_000_000) == 10
    assert paywall.download_price_cents(-50) == 1  # negative clamps


def test_download_curve_shape():
    # exponent 1.5: convex growth, pennies all the way, hard $0.10 cap
    p0 = paywall.download_price_cents(0)
    p10k = paywall.download_price_cents(10_000)
    p50k = paywall.download_price_cents(50_000)
    p100k = paywall.download_price_cents(100_000)
    assert p0 <= p10k < p50k < p100k
    # at 10k downloads (10% of scale): 1 + 9 * 0.1**1.5 ~= 1.3c
    assert 1 <= p10k <= 2
    # at 50k downloads: 1 + 9 * 0.5**1.5 ~= 4.2c
    assert 3 <= p50k <= 5
    assert p100k == 10  # the hard cap


def test_download_curve_configurable_params():
    # exponent and cap are parameters, not magic
    assert paywall.download_price_cents(50_000, exponent=1.0) != \
        paywall.download_price_cents(50_000, exponent=2.0)
    assert paywall.download_price_cents(1_000_000, cap_cents=1000) == 1000


def test_floor_never_exceeds_cap():
    # Rosa (TW-606 round 2): a stale/misconfigured floor above the cap must
    # never resurrect a higher price. Hard $0.10 cap, no exceptions.
    assert paywall.download_price_cents(0, floor_cents=999) == 10
    assert paywall.download_price_cents(100_000, floor_cents=999) == 10
    assert paywall.download_price_cents(50_000, floor_cents=2999) == 10
    # a DB version row with a stale rev-2 floor is clamped too
    db = FakeDB({
        "sci_dataset_versions": [
            {"id": "ver-9", "price_floor_cents": 2999},
        ],
        "toll_events": [],
    })
    assert paywall.download_fee_cents(db, "org-1", "ver-9") == 10


def test_asset_value_floors():
    # standard floor $0.01; premium/flagship floors higher but still
    # pennies under the $0.10 cap; curve still caps
    assert paywall.floor_for_value_tier("standard") == 1
    assert paywall.floor_for_value_tier("premium") == 5
    assert paywall.floor_for_value_tier("flagship") == 9
    assert paywall.floor_for_value_tier("nonsense") == 1  # unknown -> default
    assert paywall.download_price_cents(0, floor_cents=5) == 5
    assert paywall.download_price_cents(100_000, floor_cents=9) == 10


def test_version_price_floor_from_row():
    assert paywall.version_price_floor_cents(None) == 1
    assert paywall.version_price_floor_cents({}) == 1
    assert paywall.version_price_floor_cents({"price_floor_cents": 5}) == 5


def test_customer_tier_audience_tracking():
    # the signup question is audience tracking only -- no pricing effect
    db = FakeDB({"organizations": [{"id": "org-1", "customer_tier": "student"}]})
    assert paywall.org_customer_tier(db, "org-1") == "student"
    db2 = FakeDB({"organizations": [{"id": "org-2", "customer_tier": "researcher"}]})
    assert paywall.org_customer_tier(db2, "org-2") == "researcher"
    db3 = FakeDB({"organizations": [{"id": "org-3", "customer_tier": "other"}]})
    assert paywall.org_customer_tier(db3, "org-3") == "other"
    # unset -> default 'other'
    db4 = FakeDB({"organizations": [{"id": "org-4"}]})
    assert paywall.org_customer_tier(db4, "org-4") == "other"
    # missing column -> graceful default
    db5 = FakeDB({})
    assert paywall.org_customer_tier(db5, "org-9") == "other"


def test_download_fee_is_curve_price():
    # TW-606 Rev 3: the download fee IS the dataset's live curve price --
    # pennies, hard-capped at $0.10. Same for every audience category.
    db = FakeDB({
        "sci_dataset_versions": [
            {"id": "ver-1", "price_floor_cents": 1},
        ],
        "toll_events": [],  # 0 downloads -> $0.01
    })
    assert paywall.download_fee_cents(db, "org-1", "ver-1") == 1
    # premium floor starts higher but still pennies
    db2 = FakeDB({
        "sci_dataset_versions": [
            {"id": "ver-2", "price_floor_cents": 9},
        ],
        "toll_events": [],
    })
    assert paywall.download_fee_cents(db2, "org-2", "ver-2") == 9
    # even at 100k downloads, never more than $0.10
    db3 = FakeDB({
        "sci_dataset_versions": [
            {"id": "ver-3", "price_floor_cents": 1},
        ],
        "toll_events": [
            {"event_type": "data.science.download", "source_id": "ver-3"},
        ] * 100_000,
    })
    assert paywall.download_fee_cents(db3, "org-3", "ver-3") == 10


def test_cumulative_downloads_from_ledger():
    db = FakeDB({"toll_events": [
        {"event_type": "data.science.download", "source_id": "ver-1"},
        {"event_type": "data.science.download", "source_id": "ver-1"},
        {"event_type": "data.science.download", "source_id": "ver-2"},
        {"event_type": "data.science.query", "source_id": "ver-1"},
    ]})
    assert paywall.cumulative_downloads(db, "ver-1") == 2
    assert paywall.cumulative_downloads(db, "ver-2") == 1
    assert paywall.cumulative_downloads(db, "ver-9") == 0


def test_dataset_download_price_uses_floor_and_count():
    db = FakeDB({
        "sci_dataset_versions": [
            {"id": "ver-1", "price_floor_cents": 5, "row_count": 1000},
        ],
        "toll_events": [
            {"event_type": "data.science.download", "source_id": "ver-1"},
        ] * 50_000,
    })
    price = paywall.dataset_download_price_cents(db, "ver-1")
    assert price == paywall.download_price_cents(50_000, floor_cents=5)
    assert price > 5  # above the premium floor
    assert price <= 10  # but never above the $0.10 cap


def test_neutral_api_toll_converges_to_download_price(monkeypatch):
    # full-dataset API pull ~= current download price (economic neutrality)
    calls = []

    def fake_record(db, org_id, event_type, quantity=1.0, amount_cents=None,
                    idempotency_key=None, **kw):
        calls.append({"event_type": event_type, "amount_cents": amount_cents,
                      "quantity": quantity, "source_id": kw.get("source_id")})
        return {"ok": True}

    import app.services.toll_service as toll_service

    monkeypatch.setattr(toll_service, "record_toll_event", fake_record)
    db = FakeDB({
        "sci_dataset_versions": [
            {"id": "ver-1", "lane": "carbon", "price_floor_cents": 1,
             "row_count": 30_000,
             "released_at": "2026-10-18T00:00:00+00:00"},
        ],
        "toll_events": [],  # 0 downloads -> $0.01 price
    })
    paywall.emit_neutral_data_toll(db, "org-1", "r1", "carbon", 30_000)
    assert calls[0]["event_type"] == "data.science.api_rows"
    # full pull (30k/30k rows) at $0.01 download price -> 1c
    assert calls[0]["amount_cents"] == 1
    # half pull -> ~0c (rounds), proportional
    paywall.emit_neutral_data_toll(db, "org-1", "r2", "carbon", 15_000)
    assert calls[1]["amount_cents"] == 0


def test_neutral_api_toll_capped_per_call(monkeypatch):
    # Rosa (TW-606 round 2): a stale row_count must never let one API call
    # exceed the $0.10 hard cap. served=10,000 with row_count=1 would charge
    # $1,000 without the per-call cap.
    calls = []

    def fake_record(db, org_id, event_type, quantity=1.0, amount_cents=None,
                    idempotency_key=None, **kw):
        calls.append({"event_type": event_type, "amount_cents": amount_cents,
                      "quantity": quantity})
        return {"ok": True}

    import app.services.toll_service as toll_service

    monkeypatch.setattr(toll_service, "record_toll_event", fake_record)
    db = FakeDB({
        "sci_dataset_versions": [
            {"id": "ver-1", "lane": "carbon", "price_floor_cents": 1,
             "row_count": 1,  # stale metadata
             "released_at": "2026-10-18T00:00:00+00:00"},
        ],
        "toll_events": [],
    })
    paywall.emit_neutral_data_toll(db, "org-1", "r1", "carbon", 10_000)
    assert calls[0]["event_type"] == "data.science.api_rows"
    assert calls[0]["amount_cents"] == 10  # capped, not $1,000
    assert calls[0]["amount_cents"] <= 10


def test_neutral_toll_falls_back_without_version(monkeypatch):
    # no version row -> legacy flat query toll (pre-existing behavior)
    calls = []

    def fake_record(db, org_id, event_type, quantity=1.0, amount_cents=None,
                    idempotency_key=None, **kw):
        calls.append({"event_type": event_type})
        return {"ok": True}

    import app.config as config
    import app.services.toll_service as toll_service

    monkeypatch.setattr(toll_service, "record_toll_event", fake_record)
    monkeypatch.setitem(config.TOLL_RATES, "data.science.query", 5)
    db = FakeDB({"toll_events": []})
    paywall.emit_neutral_data_toll(db, "org-1", "r1", "carbon", 100)
    assert calls[0]["event_type"] == "data.science.query"


def test_rate_limit_tiers_same_curve():
    assert paywall.rate_limit_tier_fee_cents(0, "standard") == 1
    assert paywall.rate_limit_tier_fee_cents(0, "pro") == 5
    assert paywall.rate_limit_tier_fee_cents(0, "scale") == 20
    # higher limits cost more; all move with the curve
    assert (paywall.rate_limit_tier_fee_cents(100_000, "scale") >
            paywall.rate_limit_tier_fee_cents(100_000, "pro") >
            paywall.rate_limit_tier_fee_cents(100_000, "standard"))
    assert paywall.rate_limit_tier_fee_cents(100_000, "standard") == 10
    # unknown tier -> default
    assert paywall.rate_limit_tier_fee_cents(0, "nonsense") == 1


def test_org_rate_limit_tier_defaults():
    db = FakeDB({"organizations": [{"id": "org-1", "rate_limit_tier": "pro"}]})
    assert paywall.org_rate_limit_tier(db, "org-1") == "pro"
    db2 = FakeDB({"organizations": [{"id": "org-2"}]})
    assert paywall.org_rate_limit_tier(db2, "org-2") == "standard"
    assert paywall.org_rate_limit_tier(FakeDB({}), "org-9") == "standard"


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


def test_emit_download_toll_charges_curve_price(monkeypatch):
    calls = []

    def fake_record(db, org_id, event_type, quantity=1.0, amount_cents=None,
                    idempotency_key=None, **kw):
        calls.append({"event_type": event_type, "amount_cents": amount_cents,
                      "source_id": kw.get("source_id")})
        return {"ok": True}

    import app.services.toll_service as toll_service

    monkeypatch.setattr(toll_service, "record_toll_event", fake_record)
    # no version row -> floor $0.01 at 0 downloads -> 1c
    db = FakeDB({})
    paywall.emit_download_toll(db, "org-1", "dl1", "ver-1")
    assert calls[0]["event_type"] == "data.science.download"
    assert calls[0]["amount_cents"] == 1
    assert calls[0]["source_id"] == "ver-1"
    # premium version -> floor $0.05, still pennies
    db2 = FakeDB({
        "sci_dataset_versions": [
            {"id": "ver-2", "price_floor_cents": 5},
        ],
        "toll_events": [],
    })
    paywall.emit_download_toll(db2, "org-2", "dl2", "ver-2")
    assert calls[1]["event_type"] == "data.science.download"
    assert calls[1]["amount_cents"] == 5
    assert calls[1]["source_id"] == "ver-2"


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
