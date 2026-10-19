# Prepared by Twistor Holdings LLC
"""TW-608 rate-limit notifications: warn at 80%, shut off at 100%.

No-surprise-overcharges rule: a customer must NEVER be charged overage
fees they did not see coming. Warning (email + usage block/headers) fires
before the limit; at 100% the API returns 429 and nothing is served or
charged. Unit-level with fakes: no live database, no network.
"""
from __future__ import annotations

import pathlib as _pl
import re as _re
from datetime import datetime, timedelta, timezone

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


class BoomDB:
    def table(self, name):
        raise RuntimeError("db down")


def _iso(dt):
    return dt.isoformat()


def _org_db(status="active", tier="standard", billing_email=None, events=()):
    org = {"id": "org-1", "subscription_status": status,
           "rate_limit_tier": tier}
    if billing_email:
        org["billing_email"] = billing_email
    return FakeDB({
        "organizations": [org],
        "toll_events": list(events),
    })


def _sci_event(org="org-1", seconds_ago=10, event_type="data.science.api_rows"):
    return {
        "org_id": org,
        "event_type": event_type,
        "occurred_at": _iso(datetime.now(timezone.utc)
                            - timedelta(seconds=seconds_ago)),
    }


@pytest.fixture(autouse=True)
def _clear_warn_state():
    paywall._rate_warn_sent.clear()
    yield
    paywall._rate_warn_sent.clear()


# ------------------------------------------------------------------ config
def test_config_defaults():
    assert paywall.RATE_LIMIT_WARN_RATIO == 0.80
    assert paywall.RATE_LIMIT_WINDOW_SECONDS == 60
    assert paywall.RATE_LIMIT_WARN_COOLDOWN_SECONDS == 900


# ------------------------------------------------------------------ usage counting
def test_usage_counts_science_events_in_window_only():
    now_events = [_sci_event(seconds_ago=5) for _ in range(3)]
    old = [_sci_event(seconds_ago=600)]  # outside the 60s window
    other_org = [_sci_event(org="org-9", seconds_ago=5)]
    non_science = [_sci_event(seconds_ago=5, event_type="billing.invoice")]
    db = _org_db(events=now_events + old + other_org + non_science)
    u = paywall.rate_limit_usage(db, "org-1")
    assert u["used"] == 3
    assert u["rpm"] == 60
    assert u["ratio"] == pytest.approx(3 / 60)
    assert u["warn"] is False
    assert u["over"] is False


def test_usage_warn_flag_at_80_percent():
    db = _org_db(events=[_sci_event(seconds_ago=5) for _ in range(49)])
    u = paywall.rate_limit_usage(db, "org-1")
    assert u["warn"] is True
    assert u["over"] is False


def test_usage_over_flag_at_100_percent():
    db = _org_db(events=[_sci_event(seconds_ago=5) for _ in range(60)])
    u = paywall.rate_limit_usage(db, "org-1")
    assert u["over"] is True
    assert u["warn"] is True


def test_usage_respects_tier_rpm():
    db = _org_db(tier="pro",
                 events=[_sci_event(seconds_ago=5) for _ in range(100)])
    u = paywall.rate_limit_usage(db, "org-1")
    assert u["rpm"] == 600
    assert u["warn"] is False  # 100/600 < 80%


def test_usage_unscoped_org_not_limited():
    db = _org_db(events=[_sci_event(seconds_ago=5) for _ in range(500)])
    u = paywall.rate_limit_usage(db, "unscoped")
    assert u["ratio"] == 0.0
    assert u["over"] is False


def test_usage_fail_open_on_db_error():
    db = BoomDB()
    u = paywall.rate_limit_usage(db, "org-1")
    assert u["ratio"] == 0.0
    assert u["over"] is False


# ------------------------------------------------------------------ enforcement
def test_enforce_raises_429_when_over_and_charges_nothing():
    events = [_sci_event(seconds_ago=5) for _ in range(60)]
    db = _org_db(events=events)
    before = len(db._tables["toll_events"])
    with pytest.raises(HTTPException) as ei:
        paywall.enforce_rate_limit(db, "org-1")
    assert ei.value.status_code == 429
    assert "never" in ei.value.detail.lower() or "overage" in ei.value.detail.lower()
    assert "Retry-After" in ei.value.headers
    assert "X-Twistor-RateLimit-Used" in ei.value.headers
    # the 429 path emits no toll: the ledger is untouched
    assert len(db._tables["toll_events"]) == before


def test_enforce_passes_below_limit():
    db = _org_db(events=[_sci_event(seconds_ago=5) for _ in range(10)])
    u = paywall.enforce_rate_limit(db, "org-1")
    assert u["over"] is False


def test_enforce_fail_open_on_db_error():
    u = paywall.enforce_rate_limit(BoomDB(), "org-1")
    assert u["over"] is False  # serve, do not block a paying customer


def test_gate_subscription_first():
    db = _org_db(status="canceled",
                 events=[_sci_event(seconds_ago=5) for _ in range(60)])
    with pytest.raises(HTTPException) as ei:
        paywall.gate(db, "org-1")
    assert ei.value.status_code == 402  # subscription gate fires before 429


def test_gate_ok_path():
    db = _org_db(events=[_sci_event(seconds_ago=5) for _ in range(2)])
    assert paywall.gate(db, "org-1") == "org-1"


# ------------------------------------------------------------------ warning email
def test_warning_email_sent_once_per_cooldown(monkeypatch):
    sent = []
    import app.services.email as email_svc

    def fake_send(**kw):
        sent.append(kw)

    monkeypatch.setattr(email_svc, "_send", fake_send)
    db = _org_db(billing_email="ops@example.com",
                 events=[_sci_event(seconds_ago=5) for _ in range(50)])
    assert paywall.maybe_send_rate_limit_warning(db, "org-1") is True
    assert paywall.maybe_send_rate_limit_warning(db, "org-1") is True
    assert len(sent) == 1  # second call deduped inside the cooldown
    assert "ops@example.com" in sent[0]["to"]
    assert "Rate limit warning" in sent[0]["subject"]
    assert "50/60" in sent[0]["subject"]


def test_warning_email_skipped_without_billing_email(monkeypatch):
    sent = []
    import app.services.email as email_svc

    monkeypatch.setattr(email_svc, "_send", lambda **kw: sent.append(kw))
    db = _org_db(events=[_sci_event(seconds_ago=5) for _ in range(50)])
    # warning state is still active in-band; just no email address to send to
    assert paywall.maybe_send_rate_limit_warning(db, "org-1") is True
    assert sent == []


def test_no_warning_below_threshold(monkeypatch):
    sent = []
    import app.services.email as email_svc

    monkeypatch.setattr(email_svc, "_send", lambda **kw: sent.append(kw))
    db = _org_db(billing_email="ops@example.com",
                 events=[_sci_event(seconds_ago=5) for _ in range(10)])
    assert paywall.maybe_send_rate_limit_warning(db, "org-1") is False
    assert sent == []


# ------------------------------------------------------------------ usage block / headers
def test_usage_block_carries_rate_limit_fields():
    db = _org_db(events=[_sci_event(seconds_ago=5) for _ in range(50)])
    usage = paywall.build_usage_block(db, "org-1", request_id="r-1", records=7)
    assert usage["rate_limit_rpm"] == 60
    assert usage["rate_limit_used"] == 50
    assert usage["rate_limit_warning"] is True


def test_usage_headers_carry_warning_flag():
    class Resp:
        headers = {}

    usage = {"toll": "$0.10", "records": 5, "toll_cents": 10,
             "period_total_cents": 100, "rate_limit_rpm": 60,
             "rate_limit_used": 50, "rate_limit_warning": True}
    paywall.set_usage_headers(Resp(), usage)
    assert Resp.headers["X-Twistor-RateLimit-Warning"] == "1"
    assert Resp.headers["X-Twistor-RateLimit-Used"] == "50"
    assert Resp.headers["X-Twistor-RateLimit-Limit"] == "60"


def test_usage_headers_quiet_below_threshold():
    class Resp:
        headers = {}

    usage = {"toll": "$0.01", "records": 1, "toll_cents": 1,
             "period_total_cents": 10, "rate_limit_rpm": 60,
             "rate_limit_used": 3, "rate_limit_warning": False}
    paywall.set_usage_headers(Resp(), usage)
    assert "X-Twistor-RateLimit-Warning" not in Resp.headers


# ------------------------------------------------------------------ router wiring (Rosa round 1)
def _router_src(lane):
    p = _pl.Path(__file__).parent.parent / "app" / "data_layer" / "science" / lane / "router.py"
    return p.read_text().split("\n")


def test_older_lane_endpoints_all_enforce_before_serving():
    """Every @router endpoint in the 4 older lanes calls enforce_rate_limit
    at top level of the function body (indent == 4), before any DB work.
    Catches the high_tc round-1 misses (loop/branch placement, skipped
    endpoints)."""
    for lane in ("electrocatalysis", "high_tc", "pfas", "turbulence"):
        lines = _router_src(lane)
        # collect endpoint def line numbers (def following an @router decorator)
        endpoints = []
        for i, line in enumerate(lines):
            if line.strip().startswith("@router."):
                j = i + 1
                while j < len(lines) and not _re.match(r"^(async )?def \w+\(", lines[j].strip()):
                    j += 1
                assert j < len(lines), f"{lane}: decorator without def"
                endpoints.append(j)
        assert endpoints, f"{lane}: no endpoints found"
        for d in endpoints:
            # find the end of this def: next def/decorator at col 0 or EOF
            e = d + 1
            while e < len(lines) and not (
                lines[e].strip().startswith("@router.")
                or _re.match(r"^(async )?def \w+\(", lines[e].strip())
                or _re.match(r"^[a-zA-Z_]", lines[e])
            ):
                e += 1
            body = lines[d:e]
            hits = [ln for ln in body if "paywall.enforce_rate_limit(" in ln]
            assert hits, f"{lane}:{d + 1}: endpoint has no enforce_rate_limit call"
            for h in hits:
                indent = len(h) - len(h.lstrip())
                assert indent == 4, (
                    f"{lane}:{d + 1}: enforce_rate_limit not at function top "
                    f"level (indent {indent}) -- must run before any serving"
                )


def test_wave1_gates_cover_subscription_and_rate_limit():
    """Wave-1 _gate helpers route through paywall.gate (402 + 429)."""
    for lane in ("carbon", "subseasonal", "crop_climate"):
        src = "\n".join(_router_src(lane))
        assert "paywall.gate(" in src, f"{lane}: _gate does not call paywall.gate"
