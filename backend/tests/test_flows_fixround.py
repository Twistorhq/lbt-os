"""TW-625 fix round: prove-it tests for Rosa's 3 blockers + 10 majors.

Written in the fix round as regression proof (the core engine tests were
the RED-first set). Every test here reproduces a finding Rosa verified
by execution.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.flows import executor, migration, models, scheduler, service


def _db():
    """Supabase-shaped mock with a working upsert capture + empty selects."""
    db = MagicMock()
    saved = {}
    tbl = MagicMock()

    def _upsert(row, on_conflict=None):
        saved[row["id"]] = dict(row)
        m = MagicMock()
        m.execute.return_value = MagicMock(data=[row])
        return m

    tbl.upsert.side_effect = _upsert
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=[])
    chain.eq.return_value = chain
    tbl.select.return_value = chain
    upd = MagicMock()
    upd.eq.return_value = upd
    upd.execute.return_value = MagicMock(data=[])
    tbl.update.return_value = upd
    ins = MagicMock()
    ins.execute.return_value = MagicMock(data=[{"id": "x"}])
    tbl.insert.return_value = ins
    db.table.return_value = tbl
    db.saved = saved
    return db


def _wf_row(definition, **kw):
    base = {
        "id": "wf-1",
        "org_id": "org-1",
        "name": "Touch",
        "status": "active",
        "alert_to": None,
        "definition": definition,
    }
    base.update(kw)
    return base


def _touch_two_delays():
    return {
        "name": "Touch 1-4",
        "trigger": {"type": "manual"},
        "steps": [
            {
                "id": "n1",
                "type": "notify",
                "channel": "email",
                "to": "{{lead.email}}",
                "body": "touch1",
            },
            {"id": "d1", "type": "delay", "duration": "0s"},
            {
                "id": "n2",
                "type": "notify",
                "channel": "email",
                "to": "{{lead.email}}",
                "body": "touch2",
            },
            {"id": "d2", "type": "delay", "duration": "0s"},
            {
                "id": "n3",
                "type": "notify",
                "channel": "email",
                "to": "{{lead.email}}",
                "body": "touch3",
            },
        ],
    }


class TwoDelayResumeTests(unittest.TestCase):
    """Blocker 1: multi-delay resume must move forward, never re-send."""

    def test_second_delay_persists_new_resume_point(self):
        db = _db()
        wf_row = _wf_row(_touch_two_delays())
        with patch("app.flows.executor.notify_service") as nsvc:
            nsvc.send.return_value = {"ok": True, "id": "m-1"}
            with patch("app.flows.executor.budgets") as bdg:
                bdg.check_and_consume.return_value = ("ok", {})
                r1 = executor.run_workflow(
                    db, "org-1", wf_row, {"lead": {"email": "o@s.com"}}
                )
        self.assertEqual(r1["status"], "suspended")
        run_id = r1["run_id"]
        first = db.saved[run_id]
        self.assertEqual(first["resume_step_id"], "d1")
        # context persisted so post-delay steps see lead.email
        self.assertEqual(first["context"]["lead"]["email"], "o@s.com")

        # tick resumes from d1 -> n2 sends -> suspends at d2
        run_row = dict(first, resume_at="2000-01-01T00:00:00+00:00")
        with patch("app.flows.executor.notify_service") as nsvc:
            nsvc.send.return_value = {"ok": True, "id": "m-2"}
            with patch("app.flows.executor.budgets") as bdg:
                bdg.check_and_consume.return_value = ("ok", {})
                r2 = scheduler.resume_run(db, "org-1", wf_row, run_row)
        self.assertEqual(r2["status"], "suspended")
        second = db.saved[run_id]
        # THE blocker assertion: resume point moved FORWARD to d2
        self.assertEqual(second["resume_step_id"], "d2")
        self.assertEqual(second["context"]["lead"]["email"], "o@s.com")
        nsvc.send.assert_called_once()  # only n2 — n1 NOT re-sent

    def test_full_two_delay_sequence_no_duplicates(self):
        db = _db()
        wf_row = _wf_row(_touch_two_delays())
        sends = []
        with patch("app.flows.executor.notify_service") as nsvc:
            nsvc.send.side_effect = lambda *a, **k: (
                sends.append(k["body"]) or {"ok": True, "id": "m"}
            )
            with patch("app.flows.executor.budgets") as bdg:
                bdg.check_and_consume.return_value = ("ok", {})
                r1 = executor.run_workflow(
                    db, "org-1", wf_row, {"lead": {"email": "o@s.com"}}
                )
                run_id = r1["run_id"]
                scheduler.resume_run(
                    db,
                    "org-1",
                    wf_row,
                    dict(db.saved[run_id], resume_at="2000-01-01T00:00:00+00:00"),
                )
                r3 = scheduler.resume_run(
                    db,
                    "org-1",
                    wf_row,
                    dict(db.saved[run_id], resume_at="2000-01-01T00:00:00+00:00"),
                )
        self.assertEqual(r3["status"], "completed")
        # each touch sent exactly once, in order — no duplicates
        self.assertEqual(sends, ["touch1", "touch2", "touch3"])


class LoopCapTests(unittest.TestCase):
    """Blocker 2: the per-run step cap counts loop iterations."""

    def test_loop_iterations_hit_the_cap(self):
        db = _db()
        wf_row = _wf_row(
            {
                "name": "Blast",
                "trigger": {"type": "manual"},
                "steps": [
                    {
                        "id": "lp",
                        "type": "loop",
                        "over": "{{leads}}",
                        "as": "lead",
                        "max_iterations": 100,
                        "steps": [
                            {"id": "s1", "type": "set", "var": "x", "value": "1"}
                        ],
                    }
                ],
                "budget": {
                    "max_steps_per_run": 5,
                    "max_runs_per_day": 500,
                    "max_cost_per_day_cents": 10000,
                },
            }
        )
        with patch("app.flows.executor.budgets") as bdg:
            bdg.check_and_consume.return_value = ("ok", {})
            result = executor.run_workflow(
                db, "org-1", wf_row, {"leads": [{"e": i} for i in range(10)]}
            )
        self.assertEqual(result["status"], "halted")
        self.assertTrue(result["budget_paused"])
        # loop step + 4 iterations = 5; the 5th iteration never ran
        self.assertEqual(result["steps_executed"], 5)

    def test_negative_max_iterations_rejected(self):
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(
                {
                    "name": "x",
                    "trigger": {"type": "manual"},
                    "steps": [
                        {
                            "id": "lp",
                            "type": "loop",
                            "over": "{{l}}",
                            "as": "i",
                            "max_iterations": -3,
                            "steps": [
                                {"id": "s", "type": "set", "var": "x", "value": "1"}
                            ],
                        }
                    ],
                }
            )

    def test_loop_nesting_depth_limited(self):
        def _loop(depth):
            if depth == 0:
                return {"id": f"s{depth}", "type": "set", "var": "x", "value": "1"}
            return {
                "id": f"lp{depth}",
                "type": "loop",
                "over": "{{l}}",
                "as": "i",
                "steps": [_loop(depth - 1)],
            }

        with self.assertRaises(models.DefinitionError):
            models.validate_definition(
                {"name": "x", "trigger": {"type": "manual"}, "steps": [_loop(4)]}
            )


class LoopTollTests(unittest.TestCase):
    """Blocker 3: every loop iteration is tolled (unique idempotency keys)."""

    def test_iterations_get_distinct_toll_keys(self):
        db = _db()
        wf_row = _wf_row(
            {
                "name": "Blast",
                "trigger": {"type": "manual"},
                "steps": [
                    {
                        "id": "lp",
                        "type": "loop",
                        "over": "{{leads}}",
                        "as": "lead",
                        "max_iterations": 10,
                        "steps": [
                            {
                                "id": "nt",
                                "type": "notify",
                                "channel": "email",
                                "to": "{{lead.email}}",
                                "body": "hi",
                            }
                        ],
                    }
                ],
            }
        )
        keys = []
        with patch("app.flows.executor.notify_service") as nsvc:
            nsvc.send.return_value = {"ok": True, "id": "m"}
            with patch("app.flows.budgets.toll_step") as toll:
                toll.side_effect = lambda db, org, run, sid, key_suffix="": keys.append(
                    f"flows:{run}:{sid}{key_suffix}"
                )
                with patch(
                    "app.flows.executor.budgets.check_and_consume",
                    return_value=("ok", {}),
                ):
                    executor.run_workflow(
                        db,
                        "org-1",
                        wf_row,
                        {"leads": [{"email": f"{i}@s.com"} for i in range(3)]},
                    )
        iter_keys = [k for k in keys if ":nt:" in k]
        self.assertEqual(len(iter_keys), 3)
        self.assertEqual(len(set(iter_keys)), 3)  # all distinct


class WebhookSafetyTests(unittest.TestCase):
    """Major: SSRF posture + JSON bodies + host-only errors."""

    def test_http_scheme_refused(self):
        with self.assertRaises(executor.ConditionError):
            executor._safe_webhook_host("http://example.com/hook")

    def test_private_ip_refused(self):
        with self.assertRaises(executor.ConditionError):
            executor._safe_webhook_host("https://169.254.169.254/latest")

    def test_loopback_refused(self):
        with self.assertRaises(executor.ConditionError):
            executor._safe_webhook_host("https://127.0.0.1:8443/x")

    def test_public_host_allowed(self):
        with patch("app.flows.executor.socket.getaddrinfo") as gai:
            gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
            host = executor._safe_webhook_host("https://example.com/hook")
        self.assertEqual(host, "example.com")

    def test_webhook_body_is_json_and_templated(self):
        seen = {}

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _open(req, timeout=None):
            seen["data"] = req.data
            seen["headers"] = dict(req.header_items())
            return _Resp()

        step = models.Step(
            **{
                "id": "w1",
                "type": "webhook",
                "method": "POST",
                "url": "https://example.com/hook",
                "headers": {"X-Source": "flows-{{lead.shop}}"},
                "variables": {"body": {"email": "{{lead.email}}", "n": 3}},
            }
        )
        with (
            patch("app.flows.executor.socket.getaddrinfo") as gai,
            patch("app.flows.executor.urllib.request.build_opener") as bop,
        ):
            gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
            bop.return_value.open.side_effect = _open
            out = executor._step_webhook(
                step, {"lead": {"email": "o@s.com", "shop": "Acme"}}
            )
        import json

        self.assertEqual(json.loads(seen["data"]), {"email": "o@s.com", "n": 3})
        self.assertEqual(seen["headers"]["X-source"], "flows-Acme")
        self.assertEqual(out["url_host"], "example.com")


class DefinitionHardeningTests(unittest.TestCase):
    """Majors: cron validation, nested-delay rejection."""

    def test_cron_range_rejected_at_definition(self):
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(
                {
                    "name": "x",
                    "trigger": {"type": "schedule", "cron": "0 9 * * 1-5"},
                    "steps": [{"id": "s", "type": "set", "var": "x", "value": "1"}],
                }
            )

    def test_cron_valid_ok(self):
        d = models.validate_definition(
            {
                "name": "x",
                "trigger": {"type": "schedule", "cron": "0 9 * * *"},
                "steps": [{"id": "s", "type": "set", "var": "x", "value": "1"}],
            }
        )
        self.assertEqual(d.trigger.cron, "0 9 * * *")

    def test_delay_inside_branch_rejected(self):
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(
                {
                    "name": "x",
                    "trigger": {"type": "manual"},
                    "steps": [
                        {
                            "id": "b",
                            "type": "branch",
                            "condition": "{{x}} == 1",
                            "then": [{"id": "d", "type": "delay", "duration": "1d"}],
                            "else": [],
                        }
                    ],
                }
            )

    def test_delay_inside_loop_rejected(self):
        with self.assertRaises(models.DefinitionError):
            models.validate_definition(
                {
                    "name": "x",
                    "trigger": {"type": "manual"},
                    "steps": [
                        {
                            "id": "lp",
                            "type": "loop",
                            "over": "{{l}}",
                            "as": "i",
                            "steps": [{"id": "d", "type": "delay", "duration": "1d"}],
                        }
                    ],
                }
            )


class ZapierFilterTests(unittest.TestCase):
    """Major: filter wraps remaining actions (Zapier semantic)."""

    def test_filter_wraps_rest(self):
        flow = migration.from_zapier(
            {
                "trigger": {"app": "webhook", "event": "catch"},
                "actions": [
                    {
                        "app": "filter",
                        "action": "only_continue",
                        "field": "replied",
                        "op": "equals",
                        "value": "false",
                    },
                    {
                        "app": "email",
                        "action": "send",
                        "to": "a@b.c",
                        "subject": "s",
                        "body": "b",
                    },
                ],
            }
        )
        branch = flow["steps"][0]
        self.assertEqual(branch["type"], "branch")
        self.assertEqual(len(branch["then"]), 1)
        self.assertEqual(branch["then"][0]["type"], "notify")
        models.validate_definition({**flow, "name": "imp"})


class BudgetAutoResumeTests(unittest.TestCase):
    """Major: a budget pause must not outlive the budget day."""

    def test_auto_resume_on_day_rollover(self):
        wf_row = _wf_row(
            {
                "name": "Touch",
                "trigger": {"type": "manual"},
                "steps": [{"id": "s1", "type": "set", "var": "x", "value": "1"}],
            },
            status="paused",
            paused_reason="task budget exhausted (100%)",
        )
        db = MagicMock()
        db.table.side_effect = lambda n: _table_for(n, wf_row)
        with patch("app.flows.service.executor.run_workflow") as rw:
            rw.return_value = {"run_id": "r", "status": "completed"}
            out = service.trigger(db, "org-1", "wf-1", {})
        self.assertEqual(out["status"], "completed")
        rw.assert_called_once()

    def test_stays_paused_when_budget_still_spent(self):
        wf_row = _wf_row(
            {
                "name": "Touch",
                "trigger": {"type": "manual"},
                "steps": [{"id": "s1", "type": "set", "var": "x", "value": "1"}],
                "budget": {
                    "max_steps_per_run": 100,
                    "max_runs_per_day": 1,
                    "max_cost_per_day_cents": 100,
                },
            },
            status="paused",
            paused_reason="task budget exhausted (100%)",
        )
        db = MagicMock()

        def _table(n):
            tbl = MagicMock()
            if n == "flw_workflows":
                sel = MagicMock()
                sel.execute.return_value = MagicMock(data=[wf_row])
                sel.eq.return_value = sel
                tbl.select.return_value = sel
                tbl.eq.return_value = sel
            if n == "flw_budget_counters":
                sel = MagicMock()
                sel.execute.return_value = MagicMock(
                    data=[{"runs": 1, "steps": 1, "cost_cents": 1, "warned_80": True}]
                )
                sel.eq.return_value = sel
                tbl.select.return_value = sel
            return tbl

        db.table.side_effect = _table
        with self.assertRaises(PermissionError):
            service.trigger(db, "org-1", "wf-1", {})


def _table_for(name, wf_row):
    tbl = MagicMock()
    if name == "flw_workflows":
        sel = MagicMock()
        sel.execute.return_value = MagicMock(data=[wf_row])
        sel.eq.return_value = sel
        tbl.select.return_value = sel
        upd = MagicMock()
        upd.eq.return_value = upd
        upd.execute.return_value = MagicMock(data=[dict(wf_row, status="active")])
        tbl.update.return_value = upd
        tbl.eq.return_value = tbl
    if name == "flw_budget_counters":
        sel = MagicMock()
        sel.execute.return_value = MagicMock(data=[])
        sel.eq.return_value = sel
        tbl.select.return_value = sel
        ins = MagicMock()
        ins.execute.return_value = MagicMock(data=[{"id": "c"}])
        tbl.upsert.return_value = ins
    return tbl


if __name__ == "__main__":
    unittest.main()
