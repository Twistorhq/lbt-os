# Prepared by Twistor Holdings LLC
"""TW-607 usage metering tests: Claude Code end-of-message style usage blocks.

Every science API response carries a usage block read from the toll ledger
(one source of truth). Unit-level with fakes: no live database, no network.
"""
from __future__ import annotations

import json

import pytest
from fastapi import Response

from app.data_layer.science import paywall


# ------------------------------------------------------------------ fakes
class _Q:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def gte(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col) or "") >= str(val)]
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return _R(self._rows)


class _R:
    def __init__(self, rows):
        self.data = rows


class _DB:
    """Fake supabase client. toll_events rows drive the usage readout."""

    def __init__(self, toll_rows=None, org_rows=None):
        self._toll_rows = list(toll_rows or [])
        self._org_rows = list(org_rows or [])

    def table(self, name):
        if name == "toll_events":
            return _Q(self._toll_rows)
        if name == "organizations":
            return _Q(self._org_rows)
        return _Q([])


def _toll_row(key, cents, org="org-1", event_type="data.science.query"):
    # occurred_at = now: inside the trailing-30d window the ledger reads.
    from datetime import datetime, timezone

    return {
        "idempotency_key": key,
        "amount_cents": cents,
        "org_id": org,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------ ledger read
def test_build_usage_block_reads_toll_from_ledger():
    """The per-call toll comes from the toll ledger, keyed by idempotency key.

    One source of truth: the readout is what was actually charged, never a
    recomputation from pricing formulas.
    """
    db = _DB(toll_rows=[
        _toll_row("sci:api_rows:req-1", 42),
        _toll_row("sci:req-1", 10),
    ])
    usage = paywall.build_usage_block(db, "org-1", request_id="req-1", records=100)
    assert usage["records"] == 100
    assert usage["toll_cents"] == 52
    assert usage["toll"] == "$0.52"
    assert usage["period"] == "trailing_30d"


def test_build_usage_block_sums_all_key_patterns():
    """Flat, neutral-row, and download tolls for one request all count."""
    db = _DB(toll_rows=[
        _toll_row("sci:download:req-9", 599),
        _toll_row("sci:req-9:download", 10),
    ])
    usage = paywall.build_usage_block(db, "org-1", request_id="req-9", records=36)
    assert usage["toll_cents"] == 609
    assert usage["toll"] == "$6.09"


def test_build_usage_block_period_total_from_ledger():
    """The running total is the org's trailing-30d science spend (ledger)."""
    db = _DB(toll_rows=[
        _toll_row("sci:req-1", 42, org="org-1"),
        _toll_row("sci:other", 1000, org="org-1"),
        _toll_row("sci:req-2", 9999, org="org-OTHER"),
    ])
    usage = paywall.build_usage_block(db, "org-1", request_id="req-1", records=1)
    assert usage["period_total_cents"] == 1042
    assert usage["period_total"] == "$10.42"


def test_build_usage_block_rate_limit_tier():
    """Tier status comes from the org's configured rate-limit tier."""
    db = _DB(org_rows=[{"id": "org-1", "rate_limit_tier": "pro"}])
    usage = paywall.build_usage_block(db, "org-1", request_id="r", records=0)
    assert usage["rate_limit_tier"] == "pro"
    assert usage["rate_limit_rpm"] == paywall.RATE_LIMIT_TIERS["pro"]["rpm"]


def test_build_usage_block_fail_soft_on_ledger_error():
    """TW-208: a ledger lookup failure still renders a (zeroed) usage block."""

    class _BoomDB:
        def table(self, _name):
            raise RuntimeError("db down")

    usage = paywall.build_usage_block(_BoomDB(), "org-1", request_id="r", records=5)
    assert usage["records"] == 5
    assert usage["toll_cents"] == 0
    assert usage["period_total_cents"] == 0


# ------------------------------------------------------------------ headers
def test_set_usage_headers():
    """Programmatic clients get X-Twistor-Toll / X-Twistor-Usage."""
    resp = Response()
    paywall.set_usage_headers(resp, {
        "records": 100, "toll_cents": 42, "toll": "$0.42",
        "period_total_cents": 1250,
    })
    assert resp.headers["X-Twistor-Toll"] == "$0.42"
    parsed = json.loads(resp.headers["X-Twistor-Usage"])
    assert parsed == {"records": 100, "toll_cents": 42,
                      "period_total_cents": 1250}


def test_set_usage_headers_noop_without_response_headers():
    """Header stamping never breaks serving (TW-208)."""
    paywall.set_usage_headers(object(), {"toll": "$1.00"})  # no .headers
    paywall.set_usage_headers(Response(), {})  # empty usage


# ------------------------------------------------------------------ finalize
def test_finalize_attaches_usage_to_dict():
    """Dict lane responses (electrocatalysis) get a usage key in the body."""
    db = _DB(toll_rows=[_toll_row("sci:req-1", 25)])
    resp = Response()
    result = {"as_of": "1.0.0", "data": [{"a": 1}, {"a": 2}]}
    out = paywall.finalize(result, db, "org-1", "req-1", resp)
    assert out["usage"]["records"] == 2
    assert out["usage"]["toll_cents"] == 25
    assert resp.headers["X-Twistor-Toll"] == "$0.25"


def test_finalize_attaches_usage_to_envelope_model():
    """Pydantic envelope lanes get .usage set (serializes via the field)."""
    from app.data_layer.science.carbon.schemas import ScienceEnvelope

    db = _DB(toll_rows=[_toll_row("sci:req-2", 10)])
    resp = Response()
    env = ScienceEnvelope(
        as_of={"dataset_name": "carbon-trust", "semver": "1.0.0",
               "content_root_hash": "abc"},
        provenance={"lab_count": 1, "attestation_summary": {},
                    "calibration_count": 0, "synthetic": True},
        synthetic=True,
        data=[{"x": 1}],
    )
    out = paywall.finalize(env, db, "org-1", "req-2", resp)
    assert out.usage["records"] == 1
    assert out.usage["toll_cents"] == 10
    dumped = out.model_dump()
    assert dumped["usage"]["toll"] == "$0.10"
    assert resp.headers["X-Twistor-Toll"] == "$0.10"


def test_finalize_list_gets_headers_only():
    """JSON arrays can't carry a body key: headers carry the usage."""
    db = _DB(toll_rows=[_toll_row("sci:req-3", 7)])
    resp = Response()
    out = paywall.finalize([{"a": 1}, {"a": 2}, {"a": 3}],
                           db, "org-1", "req-3", resp)
    assert isinstance(out, list) and len(out) == 3
    parsed = json.loads(resp.headers["X-Twistor-Usage"])
    assert parsed["records"] == 3
    assert parsed["toll_cents"] == 7


def test_finalize_converts_typed_models_without_usage_field():
    """Typed lane responses without a usage field become dicts carrying it."""
    from pydantic import BaseModel

    class _Typed(BaseModel):
        name: str

    db = _DB(toll_rows=[_toll_row("sci:req-4", 3)])
    out = paywall.finalize(_Typed(name="x"), db, "org-1", "req-4", None)
    assert isinstance(out, dict)
    assert out["name"] == "x"
    assert out["usage"]["toll_cents"] == 3


def test_finalize_without_response_skips_headers():
    """finalize works when no response object is provided (service-layer use)."""
    db = _DB()
    out = paywall.finalize({"data": []}, db, None, "req-5", None)
    assert out["usage"]["toll_cents"] == 0
    assert out["usage"]["rate_limit_tier"] == paywall.DEFAULT_RATE_LIMIT_TIER


# ------------------------------------------------------------------ counting
@pytest.mark.parametrize("result,expected", [
    ({"data": [1, 2, 3]}, 3),
    ({"versions": [1, 2]}, 2),
    ({"rows": [1]}, 1),
    ({"records": []}, 0),
    ([1, 2, 3, 4], 4),
    ({"data": {"a": 1}}, 1),
    ({"other": "x"}, 0),
    (None, 0),
])
def test_count_records_shapes(result, expected):
    assert paywall._count_records(result) == expected
