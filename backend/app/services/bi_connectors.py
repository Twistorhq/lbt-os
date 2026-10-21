"""BI connector framework (TW-570 M3 white-label BI, Databox wedge).

A BIConnector turns a data source into dashboard-ready AGGREGATES via a
standard interface: metric_definitions() declares what the connector offers
(including a machine-checkable output SHAPE for each metric — the security
contract the embed renderer enforces), and compute() returns the aggregates.

v1 ships two connectors:
- "internal": wraps the existing services/metrics.py aggregates. Zero new
  data plumbing — the metrics already exist and are already aggregate.
- "stripe": aggregates from the locally-synced sales/customers rows that
  the existing StripeProvider.sync() writes. Local tables only; no live
  provider API calls in the render path.

Adding a connector later means subclassing BIConnector and registering it
in BI_CONNECTOR_REGISTRY. Nothing else changes.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from supabase import Client

# ---------------------------------------------------------------------------
# Output shapes (the aggregate-only security contract, Rosa TW-570 Major 1)
# ---------------------------------------------------------------------------
# Every metric definition declares its output shape. render_embed validates
# each computed value against the shape and fails closed on mismatch, so a
# connector can never smuggle row-level data through the public embed path.
KPI_SHAPE = {
    "value": "number",
    "delta_pct": "number|null",
    "label": "string",
}
TREND_SHAPE = {
    "points": [{"label": "string", "value": "number"}],
}


class BIConnector:
    """Base class for BI connectors. Subclass and register."""

    key: str = ""
    label: str = ""

    def metric_definitions(self) -> list[dict[str, Any]]:
        """Describe offered metrics: key, label, unit, output shape."""
        raise NotImplementedError

    def compute(self, db: Client, org_id: str) -> dict[str, Any]:
        """Return {metric_key: aggregate_value}. Aggregates ONLY."""
        raise NotImplementedError


class InternalConnector(BIConnector):
    """Aggregates from the existing metrics services (no new plumbing)."""

    key = "internal"
    label = "Twistor Trades"

    def metric_definitions(self) -> list[dict[str, Any]]:
        return [
            {"key": "revenue_30d", "label": "Revenue (30d)", "unit": "usd",
             "output": {"type": "kpi", "shape": KPI_SHAPE}},
            {"key": "profit_30d", "label": "Profit (30d)", "unit": "usd",
             "output": {"type": "kpi", "shape": KPI_SHAPE}},
            {"key": "leads_30d", "label": "Leads (30d)", "unit": "count",
             "output": {"type": "kpi", "shape": KPI_SHAPE}},
            {"key": "conversion_rate", "label": "Lead conversion",
             "unit": "pct",
             "output": {"type": "kpi", "shape": KPI_SHAPE}},
            {"key": "revenue_trend_12w", "label": "Revenue trend (12w)",
             "unit": "usd",
             "output": {"type": "trend", "shape": TREND_SHAPE}},
        ]

    def compute(self, db: Client, org_id: str) -> dict[str, Any]:
        from .metrics import get_dashboard_metrics, get_revenue_trend

        m = get_dashboard_metrics(db, org_id, days=30) or {}
        revenue = m.get("revenue") or {}
        leads = m.get("leads") or {}
        trend = get_revenue_trend(db, org_id, weeks=12) or []
        points = [
            {"label": str(p.get("label") or p.get("week") or ""),
             "value": float(p.get("value") or p.get("revenue") or 0)}
            for p in trend if isinstance(p, dict)
        ]
        return {
            "revenue_30d": {
                "value": float(revenue.get("total") or 0),
                "delta_pct": None,
                "label": "Revenue (30d)",
            },
            "profit_30d": {
                "value": float(revenue.get("profit") or 0),
                "delta_pct": None,
                "label": "Profit (30d)",
            },
            "leads_30d": {
                "value": float(leads.get("total") or 0),
                "delta_pct": None,
                "label": "Leads (30d)",
            },
            "conversion_rate": {
                "value": float(leads.get("conversion_rate_pct") or 0),
                "delta_pct": None,
                "label": "Lead conversion",
            },
            "revenue_trend_12w": {"points": points},
        }


class StripeConnector(BIConnector):
    """Aggregates from locally-synced Stripe rows (sales/customers tables).

    The existing StripeProvider.sync() upserts charges -> sales and
    customers -> customers. This connector reads those local tables only.
    """

    key = "stripe"
    label = "Stripe"

    def metric_definitions(self) -> list[dict[str, Any]]:
        return [
            {"key": "stripe_revenue_30d", "label": "Stripe revenue (30d)",
             "unit": "usd",
             "output": {"type": "kpi", "shape": KPI_SHAPE}},
            {"key": "stripe_charges_30d", "label": "Stripe charges (30d)",
             "unit": "count",
             "output": {"type": "kpi", "shape": KPI_SHAPE}},
            {"key": "stripe_avg_ticket_30d",
             "label": "Stripe avg ticket (30d)", "unit": "usd",
             "output": {"type": "kpi", "shape": KPI_SHAPE}},
        ]

    def compute(self, db: Client, org_id: str) -> dict[str, Any]:
        since = (date.today() - timedelta(days=30)).isoformat()
        sales = (
            db.table("sales")
            .select("amount")
            .eq("org_id", org_id)
            .gte("sold_at", since)
            .execute()
        ).data or []
        amounts = [float(s.get("amount") or 0) for s in sales
                   if isinstance(s, dict)]
        total = round(sum(amounts), 2)
        count = len(amounts)
        avg = round(total / count, 2) if count else 0.0
        return {
            "stripe_revenue_30d": {
                "value": total, "delta_pct": None,
                "label": "Stripe revenue (30d)",
            },
            "stripe_charges_30d": {
                "value": float(count), "delta_pct": None,
                "label": "Stripe charges (30d)",
            },
            "stripe_avg_ticket_30d": {
                "value": avg, "delta_pct": None,
                "label": "Stripe avg ticket (30d)",
            },
        }


BI_CONNECTOR_REGISTRY: dict[str, BIConnector] = {
    "internal": InternalConnector(),
    "stripe": StripeConnector(),
}


def widget_metrics() -> dict[str, dict[str, Any]]:
    """Union of all registered connectors' metric definitions, keyed by key."""
    out: dict[str, dict[str, Any]] = {}
    for connector in BI_CONNECTOR_REGISTRY.values():
        for definition in connector.metric_definitions():
            out[definition["key"]] = definition
    return out
