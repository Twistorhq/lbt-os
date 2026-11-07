"""TW-940 Twistor Social Safety Desk Minimum (Phase 6 slice): TDD.

Covers the Jan 1 Denver launch safety desk on the interim trust
model (no live identity verification; accountability is behavioral:
standing, strikes, bans):

- report filing (implemented, no longer the Phase 6 stub) with
  intake routing: serious categories bypass ambassadors to staff,
  routine categories may go to ambassadors, metro fail-closed;
- triage queue (list / claim / resolve) with role-scoped access;
- sanction actions (strike / suspend / restrict) wired into the
  existing member-context enforcement, and the identity-anchored
  ban wired into the existing rsn_bans enforcement in
  get_member_context;
- appeal intake + acknowledgement + fresh-reviewer decision;
- evidence packets: hash-chained manifest items, legal-hold freeze;
- disclosure: owner-scoped reporter / subject views (data only, no
  external sends) plus the external legal disclosure log workflow.

Authorization model (documented for Rosa / Rex):
- ``rsn_safety_staff`` (migration_social_v3, UNAPPLIED) is the staff
  role record: role ``staff`` or ``safety_lead``, status ``active``.
  No row, or a non-active row, is not staff. Fail closed.
- ``rsn_ambassadors`` status ``active`` is the ambassador record.
  Ambassadors may only list / claim eligible categories in their
  metro, and may only resolve as unfounded or escalate. They may
  never sanction, ban, touch evidence, decide appeals, or answer
  disclosures (TW-648 / design.md ambassador scope).
- Bans, appeal decisions, legal holds, and external disclosure
  decisions require ``safety_lead`` (appeal decisions additionally
  require a fresh reviewer: never the sanction / ban issuer).
- Every desk action writes ``rsn_audit_log`` (actor, action,
  target, sha256 detail hash). No raw notes live in the audit log.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_social_core_loop import NOW, FakeDb, add_member

from app.social import models, service

LATER = NOW + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def add_staff(db: FakeDb, *, role="staff", status="active"):
    user_id = add_member(db, standing="regular")
    db.seed(
        "rsn_safety_staff",
        {"user_id": user_id, "role": role, "status": status},
    )
    return user_id


def add_lead(db: FakeDb):
    return add_staff(db, role="safety_lead")


def add_ambassador(db: FakeDb, *, metro="denver", status="active"):
    user_id = add_member(db, standing="pillar")
    db.seed(
        "rsn_ambassadors",
        {"user_id": user_id, "metro": metro, "status": status},
    )
    return user_id


def file_report(
    db: FakeDb,
    reporter,
    subject,
    *,
    category="harassment",
    target_type="user",
    target_id=None,
    now=NOW,
):
    return service.create_report(
        db,
        UUID(reporter),
        models.ReportCreate(
            target_type=target_type,
            target_id=UUID(target_id or subject),
            subject_user_id=UUID(subject),
            category=category,
            description="something happened at the meetup",
        ),
        now=now,
    )


def staff_case(db, *, category="harassment"):
    """A filed report claimed by a staff member. Returns ids."""
    staff = add_staff(db)
    reporter = add_member(db)
    subject = add_member(db, standing="regular")
    report = file_report(db, reporter, subject, category=category)
    service.claim_report(db, UUID(staff), UUID(report["id"]), now=NOW)
    return staff, reporter, subject, report


def audit_actions(db):
    return [(r["actor"], r["action"]) for r in db.rows("rsn_audit_log")]


# ---------------------------------------------------------------------------
# Report filing
# ---------------------------------------------------------------------------


class TestCreateReport:
    def test_filing_writes_open_report_and_audit(self):
        db = FakeDb()
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject)
        assert report["status"] == "open"
        assert report["reporter_id"] == reporter
        assert report["subject_user_id"] == subject
        assert db.rows("rsn_reports")[0]["status"] == "open"
        assert (reporter, "report.filed") in audit_actions(db)

    def test_serious_category_routes_to_staff_queue(self):
        db = FakeDb()
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject, category="threats")
        assert report["queue"] == "staff"

    def test_routine_category_routes_to_ambassador_queue(self):
        db = FakeDb()
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject, category="harassment")
        assert report["queue"] == "ambassador"

    def test_ghost_town_account_cannot_file(self):
        db = FakeDb()
        reporter = add_member(db, tier=None)
        subject = add_member(db)
        with pytest.raises(service.ForbiddenError):
            file_report(db, reporter, subject)

    def test_user_target_defaults_subject_to_target(self):
        db = FakeDb()
        reporter = add_member(db)
        subject = add_member(db)
        report = service.create_report(
            db,
            UUID(reporter),
            models.ReportCreate(
                target_type="user",
                target_id=UUID(subject),
                category="fraud",
                description="impersonation attempt",
            ),
            now=NOW,
        )
        assert report["subject_user_id"] == subject


# ---------------------------------------------------------------------------
# Triage queue
# ---------------------------------------------------------------------------


class TestTriageQueue:
    def test_plain_member_cannot_list_queue(self):
        db = FakeDb()
        member = add_member(db)
        with pytest.raises(service.ForbiddenError):
            service.list_report_queue(db, UUID(member))

    def test_inactive_staff_row_is_not_staff(self):
        db = FakeDb()
        user = add_staff(db, status="revoked")
        with pytest.raises(service.ForbiddenError):
            service.list_report_queue(db, UUID(user))

    def test_staff_sees_all_reports(self):
        db = FakeDb()
        staff = add_staff(db)
        reporter = add_member(db)
        subject = add_member(db)
        file_report(db, reporter, subject, category="threats")
        file_report(db, reporter, subject, category="harassment")
        queue = service.list_report_queue(db, UUID(staff))
        assert len(queue) == 2

    def test_ambassador_sees_only_eligible_metro_category(self):
        db = FakeDb()
        ambassador = add_ambassador(db, metro="denver")
        reporter = add_member(db)
        subject = add_member(db)
        group = db.seed(
            "rsn_groups",
            {
                "name": "Denver Hikers",
                "metro": "denver",
                "min_age_band": "age_18_plus",
                "created_by": reporter,
                "visibility": "public",
            },
        )
        # Routine category against a Denver group: ambassador-visible.
        file_report(
            db,
            reporter,
            subject,
            category="harassment",
            target_type="group",
            target_id=group["id"],
        )
        # Serious category bypasses ambassadors at intake.
        file_report(db, reporter, subject, category="sexual_misconduct")
        # No resolvable metro (user target) fails closed to staff.
        file_report(db, reporter, subject, category="fraud")
        queue = service.list_report_queue(db, UUID(ambassador))
        assert len(queue) == 1
        assert queue[0]["target_type"] == "group"

    def test_claim_sets_triaged_and_assignee(self):
        db = FakeDb()
        staff = add_staff(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject)
        claimed = service.claim_report(db, UUID(staff), UUID(report["id"]), now=NOW)
        assert claimed["status"] == "triaged"
        assert claimed["assigned_to"] == staff
        assert (staff, "report.claimed") in audit_actions(db)

    def test_double_claim_conflicts(self):
        db = FakeDb()
        staff = add_staff(db)
        other = add_staff(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject)
        service.claim_report(db, UUID(staff), UUID(report["id"]), now=NOW)
        with pytest.raises(service.ConflictError):
            service.claim_report(db, UUID(other), UUID(report["id"]), now=NOW)

    def test_reporter_cannot_claim_own_report(self):
        db = FakeDb()
        staff = add_staff(db)
        subject = add_member(db)
        report = file_report(db, staff, subject)
        with pytest.raises(service.ForbiddenError):
            service.claim_report(db, UUID(staff), UUID(report["id"]), now=NOW)

    def test_ambassador_cannot_claim_serious_category(self):
        db = FakeDb()
        ambassador = add_ambassador(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject, category="identity_fraud")
        with pytest.raises(service.ForbiddenError):
            service.claim_report(db, UUID(ambassador), UUID(report["id"]), now=NOW)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestResolveReport:
    def test_resolve_requires_claim_first(self):
        db = FakeDb()
        staff = add_staff(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject)
        with pytest.raises(service.ConflictError):
            service.resolve_report(
                db,
                UUID(staff),
                UUID(report["id"]),
                models.ReportResolve(outcome="unfounded", note="no evidence"),
                now=NOW,
            )

    def test_other_reviewers_case_is_forbidden(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db)
        other = add_staff(db)
        with pytest.raises(service.ForbiddenError):
            service.resolve_report(
                db,
                UUID(other),
                UUID(report["id"]),
                models.ReportResolve(outcome="unfounded", note="not mine"),
                now=NOW,
            )

    def test_unfounded_resolution_records_note_and_trust_event(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        resolved = service.resolve_report(
            db,
            UUID(staff),
            UUID(report["id"]),
            models.ReportResolve(outcome="unfounded", note="no corroboration"),
            now=LATER,
        )
        assert resolved["status"] == "unfounded"
        assert resolved["resolution_note"] == "no corroboration"
        assert resolved["resolved_at"] is not None
        events = [
            e
            for e in db.rows("rsn_trust_events")
            if e["user_id"] == subject and e["event_type"] == "report_unfounded"
        ]
        assert len(events) == 1

    def test_ambassador_may_close_unfounded_but_not_action(self):
        db = FakeDb()
        ambassador = add_ambassador(db, metro="denver")
        reporter = add_member(db)
        subject = add_member(db)
        group = db.seed(
            "rsn_groups",
            {
                "name": "Denver Hikers",
                "metro": "denver",
                "min_age_band": "age_18_plus",
                "created_by": reporter,
                "visibility": "public",
            },
        )
        report = file_report(
            db,
            reporter,
            subject,
            category="harassment",
            target_type="group",
            target_id=group["id"],
        )
        service.claim_report(db, UUID(ambassador), UUID(report["id"]), now=NOW)
        with pytest.raises(service.ForbiddenError):
            service.resolve_report(
                db,
                UUID(ambassador),
                UUID(report["id"]),
                models.ReportResolve(outcome="actioned", note="sanction them"),
                now=NOW,
            )
        resolved = service.resolve_report(
            db,
            UUID(ambassador),
            UUID(report["id"]),
            models.ReportResolve(outcome="unfounded", note="single uncorroborated"),
            now=NOW,
        )
        assert resolved["status"] == "unfounded"

    def test_ambassador_escalation_returns_case_to_staff_queue(self):
        db = FakeDb()
        ambassador = add_ambassador(db, metro="denver")
        reporter = add_member(db)
        subject = add_member(db)
        group = db.seed(
            "rsn_groups",
            {
                "name": "Denver Hikers",
                "metro": "denver",
                "min_age_band": "age_18_plus",
                "created_by": reporter,
                "visibility": "public",
            },
        )
        report = file_report(
            db,
            reporter,
            subject,
            category="harassment",
            target_type="group",
            target_id=group["id"],
        )
        service.claim_report(db, UUID(ambassador), UUID(report["id"]), now=NOW)
        escalated = service.resolve_report(
            db,
            UUID(ambassador),
            UUID(report["id"]),
            models.ReportResolve(outcome="escalated", note="needs staff eyes"),
            now=NOW,
        )
        assert escalated["status"] == "open"
        assert escalated["assigned_to"] is None
        staff = add_staff(db)
        queue = service.list_report_queue(db, UUID(staff), status="open")
        assert [r["id"] for r in queue] == [report["id"]]

    def test_actioned_requires_a_linked_sanction_or_ban(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db)
        with pytest.raises(service.ConflictError):
            service.resolve_report(
                db,
                UUID(staff),
                UUID(report["id"]),
                models.ReportResolve(outcome="actioned", note="did something"),
                now=NOW,
            )


# ---------------------------------------------------------------------------
# Sanctions
# ---------------------------------------------------------------------------


def sanction(db, staff, report, *, action="strike", **kwargs):
    payload = models.SanctionCreate(
        action=action,
        reason_category="harassment",
        note="case notes on file",
        **kwargs,
    )
    return service.issue_sanction(db, UUID(staff), UUID(report["id"]), payload, now=NOW)


class TestSanctions:
    def test_ambassador_cannot_sanction(self):
        db = FakeDb()
        ambassador = add_ambassador(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject)
        with pytest.raises(service.ForbiddenError):
            sanction(db, ambassador, report)

    def test_strike_writes_sanction_and_identity_strike_trust_event(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        row = sanction(db, staff, report)
        assert row["level"] == "strike"
        assert row["user_id"] == subject
        assert row["issued_by"] == staff
        assert row["status"] == "active"
        strikes = [
            e
            for e in db.rows("rsn_trust_events")
            if e["user_id"] == subject and e["event_type"] == "identity_strike"
        ]
        assert len(strikes) == 1
        assert (staff, "sanction.issued") in audit_actions(db)

    def test_actioned_resolution_after_sanction(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        sanction(db, staff, report)
        resolved = service.resolve_report(
            db,
            UUID(staff),
            UUID(report["id"]),
            models.ReportResolve(outcome="actioned", note="strike issued"),
            now=LATER,
        )
        assert resolved["status"] == "actioned"
        events = [
            e
            for e in db.rows("rsn_trust_events")
            if e["user_id"] == subject and e["event_type"] == "report_substantiated"
        ]
        assert len(events) == 1

    def test_suspend_requires_expiry_within_72_hours(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db, category="threats")
        with pytest.raises(service.ConflictError):
            sanction(db, staff, report, action="suspend")
        with pytest.raises(service.ConflictError):
            sanction(
                db,
                staff,
                report,
                action="suspend",
                expires_at=NOW + timedelta(hours=73),
            )

    def test_suspension_removes_voice_until_expiry(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db, category="threats")
        sanction(
            db, staff, report, action="suspend", expires_at=NOW + timedelta(hours=24)
        )
        ctx = service.get_member_context(db, UUID(subject), now=NOW)
        assert ctx.can_write is False
        assert ctx.suspended_until == NOW + timedelta(hours=24)
        with pytest.raises(service.ForbiddenError):
            service.create_group(
                db,
                UUID(subject),
                models.GroupCreate(
                    name="New Group",
                    slug="new-group-tw940",
                    category="outdoors",
                    metro="denver",
                    min_age_band="age_18_plus",
                ),
                now=NOW,
            )
        # After expiry the sanction no longer binds.
        ctx = service.get_member_context(
            db, UUID(subject), now=NOW + timedelta(hours=25)
        )
        assert ctx.suspended is False
        assert ctx.can_write is True

    def test_restriction_blocks_hosting(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        sanction(db, staff, report, action="restrict")
        ctx = service.get_member_context(db, UUID(subject))
        assert ctx.restricted is True
        with pytest.raises(service.ForbiddenError):
            service.create_group(
                db,
                UUID(subject),
                models.GroupCreate(
                    name="New Group",
                    slug="restricted-group-tw940",
                    category="outdoors",
                    metro="denver",
                    min_age_band="age_18_plus",
                ),
                now=NOW,
            )

    def test_duplicate_active_sanction_from_same_case_conflicts(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db)
        sanction(db, staff, report)
        with pytest.raises(service.ConflictError):
            sanction(db, staff, report)


# ---------------------------------------------------------------------------
# Bans
# ---------------------------------------------------------------------------


def ban(db, lead, report, **kwargs):
    payload = models.BanCreate(
        reason_category="threats", note="credible threat, staff reviewed", **kwargs
    )
    return service.issue_ban(db, UUID(lead), UUID(report["id"]), payload, now=NOW)


class TestBans:
    def test_staff_cannot_ban_lead_required(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db, category="threats")
        with pytest.raises(service.ForbiddenError):
            ban(db, staff, report)

    def test_platform_ban_feeds_existing_enforcement(self):
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject, category="threats")
        service.claim_report(db, UUID(lead), UUID(report["id"]), now=NOW)
        row = ban(db, lead, report)
        assert row["scope"] == "platform"
        assert row["appeal_status"] == "none"
        ctx = service.get_member_context(db, UUID(subject))
        assert ctx.banned is True
        with pytest.raises(service.ForbiddenError):
            service.list_groups(db, UUID(subject))
        assert (lead, "ban.issued") in audit_actions(db)

    def test_group_scope_ban_is_not_a_platform_ban(self):
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        subject = add_member(db)
        group = db.seed("rsn_groups", {"name": "G", "metro": "denver"})
        report = file_report(db, reporter, subject, category="threats")
        service.claim_report(db, UUID(lead), UUID(report["id"]), now=NOW)
        ban(db, lead, report, scope="group", group_id=UUID(group["id"]))
        ctx = service.get_member_context(db, UUID(subject))
        assert ctx.banned is False

    def test_duplicate_platform_ban_conflicts(self):
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject, category="threats")
        service.claim_report(db, UUID(lead), UUID(report["id"]), now=NOW)
        ban(db, lead, report)
        with pytest.raises(service.ConflictError):
            ban(db, lead, report)


# ---------------------------------------------------------------------------
# Appeals
# ---------------------------------------------------------------------------


def appeal(db, appellant, target_type, target_id, *, statement="please review"):
    return service.create_appeal(
        db,
        UUID(appellant),
        models.AppealCreate(
            target_type=target_type, target_id=UUID(target_id), statement=statement
        ),
        now=NOW,
    )


class TestAppeals:
    def test_subject_can_appeal_own_sanction_with_sla(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        row = sanction(db, staff, report)
        filed = appeal(db, subject, "sanction", row["id"])
        assert filed["status"] == "submitted"
        assert filed["sla_due_at"] == (NOW + timedelta(days=7)).isoformat()
        assert filed["ack_due_at"] == (NOW + timedelta(hours=24)).isoformat()

    def test_stranger_cannot_appeal_someone_elses_sanction(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db)
        row = sanction(db, staff, report)
        stranger = add_member(db)
        with pytest.raises(service.ForbiddenError):
            appeal(db, stranger, "sanction", row["id"])

    def test_duplicate_pending_appeal_conflicts(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        row = sanction(db, staff, report)
        appeal(db, subject, "sanction", row["id"])
        with pytest.raises(service.ConflictError):
            appeal(db, subject, "sanction", row["id"])

    def test_banned_member_can_still_appeal_the_ban(self):
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject, category="threats")
        service.claim_report(db, UUID(lead), UUID(report["id"]), now=NOW)
        row = ban(db, lead, report)
        filed = appeal(db, subject, "ban", row["id"])
        assert filed["status"] == "submitted"
        ban_row = db.rows("rsn_bans")[0]
        assert ban_row["appeal_status"] == "pending"

    def test_acknowledge_moves_to_under_review(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        row = sanction(db, staff, report)
        filed = appeal(db, subject, "sanction", row["id"])
        reviewer = add_staff(db)
        acked = service.acknowledge_appeal(
            db, UUID(reviewer), UUID(filed["id"]), now=LATER
        )
        assert acked["status"] == "under_review"
        assert acked["assigned_to"] == reviewer
        assert acked["ack_at"] is not None

    def test_issuer_cannot_decide_own_case_fresh_reviewer_rule(self):
        # TW-940 fix round 1 (Rosa must-fix 2): appeal decisions are
        # lead-gated, so the fresh-reviewer probe uses a LEAD issuer;
        # the refusal must come from the issuer rule, not the gate.
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject)
        service.claim_report(db, UUID(lead), UUID(report["id"]), now=NOW)
        row = sanction(db, lead, report)
        filed = appeal(db, subject, "sanction", row["id"])
        with pytest.raises(service.ForbiddenError):
            service.decide_appeal(
                db,
                UUID(lead),
                UUID(filed["id"]),
                models.AppealDecision(
                    decision="overturned", outcome_note="I changed my mind"
                ),
                now=LATER,
            )

    def test_overturn_lifts_sanction(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        row = sanction(db, staff, report)
        filed = appeal(db, subject, "sanction", row["id"])
        reviewer = add_lead(db)
        decided = service.decide_appeal(
            db,
            UUID(reviewer),
            UUID(filed["id"]),
            models.AppealDecision(
                decision="overturned", outcome_note="evidence did not hold up"
            ),
            now=LATER,
        )
        assert decided["status"] == "overturned"
        assert db.rows("rsn_sanctions")[0]["status"] == "overturned"
        assert (reviewer, "appeal.decided") in audit_actions(db)

    def test_overturned_ban_is_lifted_and_enforcement_clears(self):
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject, category="threats")
        service.claim_report(db, UUID(lead), UUID(report["id"]), now=NOW)
        row = ban(db, lead, report)
        filed = appeal(db, subject, "ban", row["id"])
        reviewer = add_lead(db)
        service.decide_appeal(
            db,
            UUID(reviewer),
            UUID(filed["id"]),
            models.AppealDecision(decision="overturned", outcome_note="wrong person"),
            now=LATER,
        )
        assert db.rows("rsn_bans")[0]["lifted_at"] is not None
        assert db.rows("rsn_bans")[0]["appeal_status"] == "overturned"
        ctx = service.get_member_context(db, UUID(subject))
        assert ctx.banned is False

    def test_modified_decision_changes_sanction_level(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        row = sanction(db, staff, report, action="restrict")
        filed = appeal(db, subject, "sanction", row["id"])
        reviewer = add_lead(db)
        decided = service.decide_appeal(
            db,
            UUID(reviewer),
            UUID(filed["id"]),
            models.AppealDecision(
                decision="modified",
                outcome_note="restriction too broad",
                modified_level="warning",
            ),
            now=LATER,
        )
        assert decided["status"] == "modified"
        assert db.rows("rsn_sanctions")[0]["level"] == "warning"


# ---------------------------------------------------------------------------
# Evidence packets
# ---------------------------------------------------------------------------


def packet(db, staff, report):
    return service.create_evidence_packet(
        db,
        UUID(staff),
        models.EvidencePacketCreate(case_type="report", case_ref=report["id"]),
        now=NOW,
    )


def attach(db, staff, packet_id, *, ref="rsn_reports/row-1", digest="a" * 64):
    return service.attach_evidence(
        db,
        UUID(staff),
        UUID(packet_id),
        models.EvidenceItemIn(kind="report_text", ref=ref, content_sha256=digest),
        now=NOW,
    )


class TestEvidence:
    def test_packet_for_missing_case_is_not_found(self):
        db = FakeDb()
        staff = add_staff(db)
        with pytest.raises(service.NotFoundError):
            service.create_evidence_packet(
                db,
                UUID(staff),
                models.EvidencePacketCreate(case_type="report", case_ref=str(uuid4())),
                now=NOW,
            )

    def test_attach_builds_hash_chain(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db)
        created = packet(db, staff, report)
        assert created["retention_until"] is not None
        first = attach(db, staff, created["id"])
        second = attach(
            db, staff, created["id"], ref="rsn_checkins/row-9", digest="b" * 64
        )
        items = second["snapshot_manifest"]["items"]
        assert len(items) == 2
        assert items[1]["prev_hash"] == items[0]["item_hash"]
        assert second["snapshot_manifest"]["chain_head"] == items[1]["item_hash"]
        assert first["id"] == second["id"]

    def test_ambassador_cannot_touch_evidence(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db)
        created = packet(db, staff, report)
        ambassador = add_ambassador(db)
        with pytest.raises(service.ForbiddenError):
            attach(db, ambassador, created["id"])

    def test_legal_hold_requires_lead_and_freezes_manifest(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db)
        created = packet(db, staff, report)
        with pytest.raises(service.ForbiddenError):
            service.set_evidence_legal_hold(
                db,
                UUID(staff),
                UUID(created["id"]),
                models.LegalHoldIn(legal_hold=True),
                now=NOW,
            )
        lead = add_lead(db)
        held = service.set_evidence_legal_hold(
            db,
            UUID(lead),
            UUID(created["id"]),
            models.LegalHoldIn(legal_hold=True),
            now=NOW,
        )
        assert held["legal_hold"] is True
        with pytest.raises(service.ConflictError):
            attach(db, staff, created["id"])


# ---------------------------------------------------------------------------
# Disclosure
# ---------------------------------------------------------------------------


class TestDisclosureViews:
    def _resolved_case(self, db):
        staff, reporter, subject, report = staff_case(db)
        sanction(db, staff, report)
        service.resolve_report(
            db,
            UUID(staff),
            UUID(report["id"]),
            models.ReportResolve(outcome="actioned", note="strike issued"),
            now=LATER,
        )
        return staff, reporter, subject, report

    def test_reporter_sees_outcome_without_evidence(self):
        db = FakeDb()
        _, reporter, _, report = self._resolved_case(db)
        view = service.get_report_disclosure(db, UUID(reporter), UUID(report["id"]))
        assert view["audience"] == "reporter"
        assert view["status"] == "actioned"
        assert view["sanction_levels"] == ["strike"]
        assert "snapshot_manifest" not in str(view)
        assert "description" not in view

    def test_subject_sees_category_and_sanction_but_not_reporter(self):
        db = FakeDb()
        _, _, subject, report = self._resolved_case(db)
        view = service.get_report_disclosure(db, UUID(subject), UUID(report["id"]))
        assert view["audience"] == "subject"
        assert view["category"] == "harassment"
        assert view["sanctions"][0]["level"] == "strike"
        assert "reporter_id" not in view
        assert "description" not in view

    def test_unrelated_member_gets_restricted_shell(self):
        db = FakeDb()
        _, _, _, report = self._resolved_case(db)
        stranger = add_member(db)
        with pytest.raises(service.NotFoundError):
            service.get_report_disclosure(db, UUID(stranger), UUID(report["id"]))

    def test_staff_sees_full_case(self):
        db = FakeDb()
        staff, _, _, report = self._resolved_case(db)
        view = service.get_report_disclosure(db, UUID(staff), UUID(report["id"]))
        assert view["audience"] == "staff"
        assert view["report"]["description"]


class TestExternalDisclosures:
    def _packet(self, db):
        staff, _, _, report = staff_case(db)
        return staff, packet(db, staff, report)

    def test_disclosure_lifecycle_is_record_only(self):
        db = FakeDb()
        staff, created = self._packet(db)
        row = service.create_disclosure(
            db,
            UUID(staff),
            models.DisclosureCreate(
                packet_id=UUID(created["id"]),
                request_type="subpoena",
                requesting_agency="Example County Court",
                scope_note="packet manifest only",
            ),
            now=NOW,
        )
        assert row["status"] == "pending_review"
        assert row["disclosed_at"] is None
        with pytest.raises(service.ForbiddenError):
            service.decide_disclosure(
                db,
                UUID(staff),
                UUID(row["id"]),
                models.DisclosureDecision(decision="disclose"),
                now=LATER,
            )
        lead = add_lead(db)
        decided = service.decide_disclosure(
            db,
            UUID(lead),
            UUID(row["id"]),
            models.DisclosureDecision(decision="disclose"),
            now=LATER,
        )
        assert decided["status"] == "disclosed"
        assert decided["disclosed_at"] is not None
        assert decided["legal_review_by"] == lead

    def test_denied_disclosure_never_gets_disclosed_at(self):
        db = FakeDb()
        staff, created = self._packet(db)
        row = service.create_disclosure(
            db,
            UUID(staff),
            models.DisclosureCreate(
                packet_id=UUID(created["id"]),
                request_type="warrant",
                requesting_agency="Example Agency",
                scope_note="overbroad",
            ),
            now=NOW,
        )
        lead = add_lead(db)
        decided = service.decide_disclosure(
            db,
            UUID(lead),
            UUID(row["id"]),
            models.DisclosureDecision(decision="deny", scope_note="overbroad"),
            now=LATER,
        )
        assert decided["status"] == "denied"
        assert decided["disclosed_at"] is None


# ---------------------------------------------------------------------------
# Migration v3 (file only, NOT applied)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fix round 1 (Rosa gate on ca339c6): her FakeDb probes, turned into
# tests. Each test reproduces the probe against the pre-fix code.
# ---------------------------------------------------------------------------

FAR_FUTURE = NOW + timedelta(days=365)


def suspend_member(db: FakeDb, user_id, *, expires_at=FAR_FUTURE):
    """Seed an active desk suspension binding this member (the same
    row shape issue_sanction writes, resolved by
    get_member_context)."""
    return db.seed(
        "rsn_sanctions",
        {
            "user_id": user_id,
            "level": "suspension",
            "status": "active",
            "expires_at": expires_at,
            "issued_by": "seed-issuer",
        },
    )


def ban_member(db: FakeDb, user_id):
    """Flip the member's identity anchor to banned (the vfy-side ban
    get_member_context already enforces)."""
    for row in db.rows("vfy_identity_anchors"):
        if row["user_id"] == user_id:
            row["is_banned"] = True


class TestSubjectConflictOfInterest:
    """Rosa must-fix 1: the SUBJECT of a report can never work their
    own case, even with a desk role."""

    def test_subject_with_staff_role_cannot_claim_own_case(self):
        db = FakeDb()
        subject = add_staff(db)
        reporter = add_member(db)
        report = file_report(db, reporter, subject)
        with pytest.raises(service.ForbiddenError):
            service.claim_report(db, UUID(subject), UUID(report["id"]), now=NOW)
        assert db.rows("rsn_reports")[0]["assigned_to"] is None

    def test_subject_cannot_resolve_own_case_unfounded(self):
        db = FakeDb()
        subject = add_staff(db)
        reporter = add_member(db)
        report = file_report(db, reporter, subject)
        # Defense in depth: even holding the claim (seeded, as if it
        # predated the claim fix), resolve refuses the subject.
        row = db.rows("rsn_reports")[0]
        row["status"] = "triaged"
        row["assigned_to"] = subject
        with pytest.raises(service.ForbiddenError):
            service.resolve_report(
                db,
                UUID(subject),
                UUID(report["id"]),
                models.ReportResolve(outcome="unfounded", note="nothing here"),
                now=NOW,
            )
        # No +1 trust event may be written for the subject.
        events = [
            e
            for e in db.rows("rsn_trust_events")
            if e["user_id"] == subject and e["event_type"] == "report_unfounded"
        ]
        assert events == []
        assert db.rows("rsn_reports")[0]["status"] == "triaged"


class TestAppealDecisionLeadGate:
    """Rosa must-fix 2: decide_appeal requires safety_lead. A plain
    staff member cannot overturn a lead-issued ban (or any appeal)."""

    def test_plain_staff_cannot_overturn_lead_issued_ban(self):
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject, category="threats")
        service.claim_report(db, UUID(lead), UUID(report["id"]), now=NOW)
        row = ban(db, lead, report)
        filed = appeal(db, subject, "ban", row["id"])
        staff_reviewer = add_staff(db)
        with pytest.raises(service.ForbiddenError):
            service.decide_appeal(
                db,
                UUID(staff_reviewer),
                UUID(filed["id"]),
                models.AppealDecision(
                    decision="overturned", outcome_note="looks fine to me"
                ),
                now=LATER,
            )
        assert db.rows("rsn_bans")[0]["lifted_at"] is None
        ctx = service.get_member_context(db, UUID(subject))
        assert ctx.banned is True

    def test_plain_staff_cannot_decide_sanction_appeal(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        row = sanction(db, staff, report)
        filed = appeal(db, subject, "sanction", row["id"])
        other_staff = add_staff(db)
        with pytest.raises(service.ForbiddenError):
            service.decide_appeal(
                db,
                UUID(other_staff),
                UUID(filed["id"]),
                models.AppealDecision(decision="upheld", outcome_note="stands"),
                now=LATER,
            )
        assert db.rows("rsn_appeals")[0]["status"] == "submitted"


class TestDisclosureAudienceGate:
    """Rosa must-fixes 3 + 4: the staff disclosure view is gated on
    an active, non-banned, non-suspended desk status and never goes
    to the case subject; the subject view is a redacted projection
    (no staff-side note, no issued_by)."""

    def test_staff_subject_gets_subject_view_not_staff_view(self):
        db = FakeDb()
        subject = add_staff(db)
        reporter = add_member(db)
        reviewer = add_staff(db)
        report = file_report(db, reporter, subject)
        service.claim_report(db, UUID(reviewer), UUID(report["id"]), now=NOW)
        sanction(db, reviewer, report)
        view = service.get_report_disclosure(db, UUID(subject), UUID(report["id"]))
        assert view["audience"] == "subject"
        assert "report" not in view
        assert "reporter_id" not in str(view)

    def test_banned_staff_loses_staff_disclosure_view(self):
        db = FakeDb()
        _, _, _, report = staff_case(db)
        viewer = add_staff(db)
        ban_member(db, viewer)
        with pytest.raises(service.NotFoundError):
            service.get_report_disclosure(db, UUID(viewer), UUID(report["id"]))

    def test_suspended_staff_loses_staff_disclosure_view(self):
        db = FakeDb()
        _, _, _, report = staff_case(db)
        viewer = add_staff(db)
        suspend_member(db, viewer)
        with pytest.raises(service.NotFoundError):
            service.get_report_disclosure(db, UUID(viewer), UUID(report["id"]))

    def test_subject_view_redacts_sanction_and_ban_internals(self):
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject, category="threats")
        service.claim_report(db, UUID(lead), UUID(report["id"]), now=NOW)
        sanction(db, lead, report)
        ban(db, lead, report)
        view = service.get_report_disclosure(db, UUID(subject), UUID(report["id"]))
        assert view["audience"] == "subject"
        sanction_view = view["sanctions"][0]
        assert sanction_view["level"] == "strike"
        assert sanction_view["status"] == "active"
        assert "note" not in sanction_view
        assert "issued_by" not in sanction_view
        assert "identity_anchor_hash" not in sanction_view
        ban_view = view["bans"][0]
        assert ban_view["scope"] == "platform"
        assert "note" not in ban_view
        assert "issued_by" not in ban_view
        assert "identity_anchor_hash" not in ban_view
        assert view["can_appeal"] is True


class TestSuspendedStaffTriageGate:
    """Rosa must-fix 5: triage routes through the shared desk gate,
    so a suspended staff member loses list / claim / resolve."""

    def test_suspended_staff_cannot_list_queue(self):
        db = FakeDb()
        staff = add_staff(db)
        suspend_member(db, staff)
        with pytest.raises(service.ForbiddenError):
            service.list_report_queue(db, UUID(staff))

    def test_suspended_staff_cannot_claim(self):
        db = FakeDb()
        staff = add_staff(db)
        reporter = add_member(db)
        subject = add_member(db)
        report = file_report(db, reporter, subject)
        suspend_member(db, staff)
        with pytest.raises(service.ForbiddenError):
            service.claim_report(db, UUID(staff), UUID(report["id"]), now=NOW)

    def test_suspended_staff_cannot_resolve_claimed_case(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db)
        suspend_member(db, staff)
        with pytest.raises(service.ForbiddenError):
            service.resolve_report(
                db,
                UUID(staff),
                UUID(report["id"]),
                models.ReportResolve(outcome="unfounded", note="no evidence"),
                now=NOW,
            )


class TestAcknowledgeConflictOfInterest:
    """Rosa must-fix 6: the issuer (and the appellant) cannot
    acknowledge, and thereby self-assign, an appeal."""

    def test_issuer_cannot_acknowledge_appeal_of_own_sanction(self):
        db = FakeDb()
        staff, _, subject, report = staff_case(db)
        row = sanction(db, staff, report)
        filed = appeal(db, subject, "sanction", row["id"])
        with pytest.raises(service.ForbiddenError):
            service.acknowledge_appeal(db, UUID(staff), UUID(filed["id"]), now=LATER)
        assert db.rows("rsn_appeals")[0]["status"] == "submitted"
        assert db.rows("rsn_appeals")[0]["assigned_to"] is None

    def test_appellant_cannot_acknowledge_own_appeal(self):
        db = FakeDb()
        subject = add_staff(db)
        issuer = add_staff(db)
        reporter = add_member(db)
        report = file_report(db, reporter, subject)
        service.claim_report(db, UUID(issuer), UUID(report["id"]), now=NOW)
        row = sanction(db, issuer, report)
        filed = appeal(db, subject, "sanction", row["id"])
        with pytest.raises(service.ForbiddenError):
            service.acknowledge_appeal(db, UUID(subject), UUID(filed["id"]), now=LATER)


class TestSensitiveReadsAreAudited:
    """Rosa must-fix 7 (TW-801 lens): the header promises every desk
    action writes rsn_audit_log; the sensitive reads must too."""

    def test_queue_appeals_evidence_and_disclosure_reads_audit(self):
        db = FakeDb()
        staff, _, _, report = staff_case(db)
        created = packet(db, staff, report)
        service.list_report_queue(db, UUID(staff))
        service.list_appeals(db, UUID(staff))
        service.get_evidence_packet(db, UUID(staff), UUID(created["id"]))
        service.get_report_disclosure(db, UUID(staff), UUID(report["id"]))
        actions = [action for _, action in audit_actions(db)]
        for expected in (
            "report.queue_listed",
            "appeal.listed",
            "evidence.viewed",
            "report.disclosure_viewed",
        ):
            assert expected in actions, expected


class TestUnanchoredSubjectConflict:
    """Rosa nit: the stated guarantee that a subject with no
    identity anchor cannot be sanctioned or banned through the
    desk (the enforcement is anchor-keyed) had no test."""

    def test_sanction_unanchored_subject_conflicts(self):
        db = FakeDb()
        staff = add_staff(db)
        reporter = add_member(db)
        ghost = add_member(db, tier=None)
        report = file_report(db, reporter, ghost)
        service.claim_report(db, UUID(staff), UUID(report["id"]), now=NOW)
        with pytest.raises(service.ConflictError):
            sanction(db, staff, report)

    def test_ban_unanchored_subject_conflicts(self):
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        ghost = add_member(db, tier=None)
        report = file_report(db, reporter, ghost, category="threats")
        service.claim_report(db, UUID(lead), UUID(report["id"]), now=NOW)
        with pytest.raises(service.ConflictError):
            ban(db, lead, report)

    def test_sanction_subjectless_report_conflicts(self):
        db = FakeDb()
        staff = add_staff(db)
        reporter = add_member(db)
        group = db.seed("rsn_groups", {"name": "G", "metro": "denver"})
        report = service.create_report(
            db,
            UUID(reporter),
            models.ReportCreate(
                target_type="group",
                target_id=UUID(group["id"]),
                category="fraud",
                description="the group itself is a scam",
            ),
            now=NOW,
        )
        service.claim_report(db, UUID(staff), UUID(report["id"]), now=NOW)
        with pytest.raises(service.ConflictError):
            sanction(db, staff, report)


class TestModifiedSuspensionCap:
    """Rosa nit: a 'modified' appeal outcome landing on suspension
    re-validates the Section 6.2 72h cap against the kept expiry."""

    def _restriction_appeal(self, db, *, expires_at):
        staff, _, subject, report = staff_case(db)
        row = sanction(db, staff, report, action="restrict")
        stored = db.rows("rsn_sanctions")[0]
        stored["expires_at"] = expires_at
        filed = appeal(db, subject, "sanction", row["id"])
        return filed

    def test_modified_to_suspension_beyond_72h_cap_conflicts(self):
        db = FakeDb()
        filed = self._restriction_appeal(db, expires_at=NOW + timedelta(hours=100))
        reviewer = add_lead(db)
        with pytest.raises(service.ConflictError):
            service.decide_appeal(
                db,
                UUID(reviewer),
                UUID(filed["id"]),
                models.AppealDecision(
                    decision="modified",
                    outcome_note="suspension instead",
                    modified_level="suspension",
                ),
                now=LATER,
            )
        assert db.rows("rsn_sanctions")[0]["level"] == "restriction"

    def test_modified_to_suspension_within_cap_keeps_expiry(self):
        db = FakeDb()
        expiry = NOW + timedelta(hours=24)
        filed = self._restriction_appeal(db, expires_at=expiry)
        reviewer = add_lead(db)
        decided = service.decide_appeal(
            db,
            UUID(reviewer),
            UUID(filed["id"]),
            models.AppealDecision(
                decision="modified",
                outcome_note="suspension instead",
                modified_level="suspension",
            ),
            now=LATER,
        )
        assert decided["status"] == "modified"
        row = db.rows("rsn_sanctions")[0]
        assert row["level"] == "suspension"
        assert row["expires_at"] == expiry


class TestDuplicateGroupBan:
    """Rosa nit: duplicate group-scope bans for the same
    anchor + group conflict, like platform duplicates do."""

    def test_duplicate_group_scope_ban_conflicts_other_group_ok(self):
        db = FakeDb()
        lead = add_lead(db)
        reporter = add_member(db)
        subject = add_member(db)
        group = db.seed("rsn_groups", {"name": "G", "metro": "denver"})
        other_group = db.seed("rsn_groups", {"name": "H", "metro": "denver"})
        first = file_report(db, reporter, subject, category="threats")
        service.claim_report(db, UUID(lead), UUID(first["id"]), now=NOW)
        ban(db, lead, first, scope="group", group_id=UUID(group["id"]))
        second = file_report(db, reporter, subject, category="threats")
        service.claim_report(db, UUID(lead), UUID(second["id"]), now=NOW)
        with pytest.raises(service.ConflictError):
            ban(db, lead, second, scope="group", group_id=UUID(group["id"]))
        row = ban(db, lead, second, scope="group", group_id=UUID(other_group["id"]))
        assert row["scope"] == "group"
        assert row["group_id"] == other_group["id"]


# ---------------------------------------------------------------------------
# Migration v3 (file only, NOT applied)
# ---------------------------------------------------------------------------


class TestMigrationV3File:
    def test_v3_declares_staff_table_and_desk_columns(self):
        sql = (
            Path(__file__).resolve().parents[2] / "supabase" / "migration_social_v3.sql"
        ).read_text()
        for needle in (
            "rsn_safety_staff",
            "safety_lead",
            "source_report_id",
            "resolution_note",
            "ack_at",
            "decided_by",
            "retention_until",
            "'strike'",
            "'suspension'",
            "ENABLE ROW LEVEL SECURITY",
        ):
            assert needle in sql, needle
