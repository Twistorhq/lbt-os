from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from typing import Any

from supabase import Client

from ..database import is_unique_violation
from .templates import get_template

DEFAULT_LEAD_SOURCES = ["referral", "website", "google", "social", "yelp", "other"]
LEAD_STATUSES = ["new", "contacted", "qualified", "proposal", "won", "lost"]
DEFAULT_SERVICES = [
    "Custom molded bio-composite parts",
    "Prototype material run",
    "Sustainable packaging consultation",
    "Small-batch composite fabrication",
    "Manufacturing feasibility review",
    "Industrial material sourcing",
]
DEFAULT_EXPENSE_CATEGORIES = ["materials", "marketing", "equipment", "software", "insurance", "utilities", "misc", "payroll"]
DEFAULT_CUSTOMER_NAMES = [
    "Frontier Outdoor Goods",
    "Mesa Labs",
    "Alpine Industrial",
    "Peak Packaging Co",
    "Terra Build Systems",
    "Blue Pine Manufacturing",
    "Range Supply Group",
    "Summit AgTech",
    "Evergreen Fabrication",
    "Canyon Process Works",
]
DEFAULT_VENDOR_NAMES = ["Acme Supply", "Denver Utilities", "Material Depot", "BrandForge", "CloudOps"]
LEAD_NAMES = [
    "Jordan Lee",
    "Taylor Morgan",
    "Chris Patel",
    "Morgan Rivera",
    "Jamie Brooks",
    "Skyler Evans",
    "Avery Kim",
    "Cameron Diaz",
    "Riley Parker",
    "Alex Chen",
    "Dakota Young",
    "Parker Scott",
    "Reese Bennett",
    "Hayden Foster",
    "Quinn Adams",
    "Emerson Ward",
    "Finley Cooper",
    "Harper Reed",
    "Rowan Ellis",
    "Blake Turner",
]


def build_seed_profile(industry: str | None) -> dict[str, list[str]]:
    template = get_template(industry)
    if not template:
        return {
            "services": DEFAULT_SERVICES,
            "lead_sources": DEFAULT_LEAD_SOURCES,
            "expense_categories": DEFAULT_EXPENSE_CATEGORIES,
            "customer_names": DEFAULT_CUSTOMER_NAMES,
            "vendor_names": DEFAULT_VENDOR_NAMES,
        }

    return {
        "services": template.get("services") or DEFAULT_SERVICES,
        "lead_sources": template.get("lead_sources") or DEFAULT_LEAD_SOURCES,
        "expense_categories": template.get("expense_categories") or DEFAULT_EXPENSE_CATEGORIES,
        "customer_names": template.get("sample_customers") or DEFAULT_CUSTOMER_NAMES,
        "vendor_names": template.get("sample_vendors") or DEFAULT_VENDOR_NAMES,
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_missing_table_error(exc: Exception) -> bool:
    return "Could not find the table" in str(exc)


def recent_datetime(days_back: int) -> datetime:
    base = utc_now() - timedelta(days=random.randint(1, days_back))
    return base.replace(hour=random.randint(8, 17), minute=random.choice([0, 15, 30, 45]), second=0, microsecond=0)


def recent_date(days_back: int) -> date:
    return date.today() - timedelta(days=random.randint(1, days_back))


def reset_org_operating_data(db: Client, org_id: str) -> None:
    for table in ("integration_record_links", "integration_sync_runs", "integration_connections", "audit_reports", "sales", "customers", "leads", "expenses"):
        try:
            db.table(table).delete().eq("org_id", org_id).execute()
        except Exception as exc:
            if _is_missing_table_error(exc):
                continue
            raise


def create_leads(db: Client, org_id: str, count: int, profile: dict[str, list[str]]) -> list[dict[str, Any]]:
    leads: list[dict[str, Any]] = []
    lead_sources = profile["lead_sources"]
    services = profile["services"]
    for idx in range(count):
        status = random.choices(LEAD_STATUSES, weights=[4, 4, 4, 3, 3, 2], k=1)[0]
        created_at = recent_datetime(90)
        follow_up_at = None
        contacted_at = None
        converted_at = None
        lost_reason = None
        if status in {"contacted", "qualified", "proposal", "won", "lost"}:
            contacted_at = created_at + timedelta(days=random.randint(1, 6))
        if status in {"qualified", "proposal"}:
            follow_up_at = created_at + timedelta(days=random.randint(3, 10))
        if status == "won":
            converted_at = created_at + timedelta(days=random.randint(7, 18))
        if status == "lost":
            lost_reason = random.choice(["Budget", "Went with competitor", "Timing", "Project paused"])
        if idx % 6 == 0:
            follow_up_at = created_at + timedelta(days=random.randint(2, 8))
            contacted_at = None
            if status not in {"won", "lost"}:
                status = random.choice(["new", "contacted", "qualified"])

        lead = {
            "org_id": org_id,
            "name": LEAD_NAMES[idx % len(LEAD_NAMES)],
            "email": f"lead{idx + 1}@example.com",
            "phone": f"303-555-{1000 + idx}",
            "source": random.choice(lead_sources),
            "status": status,
            "service_interest": random.choice(services),
            "estimated_value": round(random.uniform(2500, 18000), 2),
            "notes": "Seeded pipeline record for dashboard testing.",
            "assigned_to": "seed-user",
            "follow_up_at": follow_up_at.isoformat() if follow_up_at else None,
            "contacted_at": contacted_at.isoformat() if contacted_at else None,
            "converted_at": converted_at.isoformat() if converted_at else None,
            "lost_reason": lost_reason,
            "created_at": created_at.isoformat(),
            "updated_at": created_at.isoformat(),
        }
        result = db.table("leads").insert(lead).execute()
        leads.append(result.data[0])
    return leads


def create_customers(db: Client, org_id: str, leads: list[dict[str, Any]], count: int, profile: dict[str, list[str]]) -> list[dict[str, Any]]:
    customers: list[dict[str, Any]] = []
    won_leads = [lead for lead in leads if lead["status"] == "won"]
    customer_names = profile["customer_names"]
    for idx in range(count):
        linked_lead = won_leads[idx] if idx < len(won_leads) else None
        created_at = recent_datetime(120)
        customer = {
            "org_id": org_id,
            "lead_id": linked_lead["id"] if linked_lead else None,
            "name": customer_names[idx % len(customer_names)],
            "email": f"customer{idx + 1}@example.com",
            "phone": f"720-555-{2000 + idx}",
            "address": f"{100 + idx} Market St, Denver, CO",
            "tags": random.sample(["vip", "loyal", "at_risk", "inactive"], k=random.randint(0, 2)),
            "notes": "Seeded customer for pipeline testing.",
            "created_at": created_at.isoformat(),
            "updated_at": created_at.isoformat(),
        }
        result = db.table("customers").insert(customer).execute()
        customers.append(result.data[0])
    return customers


def create_sales(db: Client, org_id: str, leads: list[dict[str, Any]], customers: list[dict[str, Any]], count: int, profile: dict[str, list[str]]) -> list[dict[str, Any]]:
    sales: list[dict[str, Any]] = []
    won_leads = [lead for lead in leads if lead["status"] == "won"]
    services = profile["services"]
    lead_sources = profile["lead_sources"]
    for idx in range(count):
        customer = random.choice(customers)
        linked_lead = random.choice(won_leads) if won_leads and idx < len(won_leads) else None
        sold_at = recent_datetime(90)
        amount = round(random.uniform(4800, 28000), 2)
        cost = round(amount * random.uniform(0.38, 0.72), 2)
        sale = {
            "org_id": org_id,
            "customer_id": customer["id"],
            "lead_id": linked_lead["id"] if linked_lead else None,
            "service": random.choice(services),
            "amount": amount,
            "cost": cost,
            "payment_method": random.choice(["card", "bank_transfer", "check"]),
            "payment_status": random.choices(["paid", "pending"], weights=[5, 1], k=1)[0],
            "source": linked_lead["source"] if linked_lead else random.choice(lead_sources),
            "invoice_number": f"INV-SEED-{1000 + idx}",
            "notes": "Seeded revenue record for analytics testing.",
            "sold_at": sold_at.isoformat(),
            "created_at": sold_at.isoformat(),
            "updated_at": sold_at.isoformat(),
        }
        result = db.table("sales").insert(sale).execute()
        sales.append(result.data[0])
    return sales


def create_expenses(db: Client, org_id: str, count: int, profile: dict[str, list[str]]) -> list[dict[str, Any]]:
    expenses: list[dict[str, Any]] = []
    expense_categories = profile["expense_categories"]
    vendor_names = profile["vendor_names"]
    recurring_categories = {"software", "insurance", "utilities", "rent", "phone"}
    for idx in range(count):
        expense_date = recent_date(90)
        category = random.choice(expense_categories)
        expense = {
            "org_id": org_id,
            "category": category,
            "description": f"Seeded {category} expense #{idx + 1}",
            "amount": round(random.uniform(120, 6400), 2),
            "vendor": random.choice(vendor_names),
            "is_recurring": category in recurring_categories,
            "recurrence_period": "monthly" if category in recurring_categories else None,
            "expense_date": expense_date.isoformat(),
            "created_at": datetime.combine(expense_date, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
            "updated_at": datetime.combine(expense_date, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
        }
        result = db.table("expenses").insert(expense).execute()
        expenses.append(result.data[0])
    return expenses


def seed_org_data(db: Client, org_id: str, industry: str | None, seed: int = 42) -> dict[str, Any]:
    random.seed(seed)
    profile = build_seed_profile(industry)
    leads = create_leads(db, org_id, 20, profile)
    customers = create_customers(db, org_id, leads, 10, profile)
    sales = create_sales(db, org_id, leads, customers, 12, profile)
    expenses = create_expenses(db, org_id, 8, profile)
    return {
        "industry": industry or "general",
        "counts": {
            "leads": len(leads),
            "customers": len(customers),
            "sales": len(sales),
            "expenses": len(expenses),
            "total": len(leads) + len(customers) + len(sales) + len(expenses),
        },
    }


def bootstrap_demo_org(
    db: Client,
    *,
    user_id: str,
    name: str,
    industry: str,
    city: str = "Denver",
    state: str = "CO",
    seed: int = 42,
) -> dict[str, Any]:
    # TW-1323 (Rex F1): defensive read (oldest row wins, never
    # maybe_single) + conflict-safe insert, same as create_organization.
    existing_rows = (
        db.table("organizations")
        .select("*")
        .eq("clerk_user_id", user_id)
        .order("created_at")
        .limit(1)
        .execute()
    ).data
    existing = existing_rows[0] if existing_rows else None

    def _apply_demo_profile(org_id: str) -> dict[str, Any]:
        return (
            db.table("organizations")
            .update({
                "name": name,
                "industry": industry,
                "city": city,
                "state": state,
                "onboarding_complete": True,
            })
            .eq("id", org_id)
            .execute()
        ).data[0]

    if existing is not None:
        org = _apply_demo_profile(existing["id"])
        reset_org_operating_data(db, org["id"])
    else:
        try:
            org = (
                db.table("organizations")
                .insert({
                    "clerk_user_id": user_id,
                    "clerk_org_id": user_id,
                    "name": name,
                    "industry": industry,
                    "city": city,
                    "state": state,
                    "onboarding_complete": True,
                })
                .execute()
            ).data[0]
        except Exception as exc:
            if not is_unique_violation(exc):
                raise
            raced_rows = (
                db.table("organizations")
                .select("*")
                .eq("clerk_user_id", user_id)
                .order("created_at")
                .limit(1)
                .execute()
            ).data
            if not raced_rows:
                raise
            org = _apply_demo_profile(raced_rows[0]["id"])
            reset_org_operating_data(db, org["id"])

    seed_summary = seed_org_data(db, org["id"], industry, seed=seed)
    return {"organization": org, "seed_summary": seed_summary}
