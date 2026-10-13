# Prepared by Twistor Holdings LLC.
"""Tests for the Morning Brief action-queue wire-in (TW-212, TW-470 surge).

Covers: ranking order, one-action-per-entity dedupe, empty-on-scan-error
(self-healing), HTML escaping (no markup injection via entity names),
SMS line format, empty rendering.
"""

from unittest.mock import patch

from app.services import brief_actions as ba


def _finding(detector, entities, recommended="Do the thing.", title="Leak"):
    return {
        "detector": detector,
        "title": title,
        "severity": "watch",
        "recommended_action": recommended,
        "entities": entities,
    }


def _brief(findings):
    return {"what_should_we_do": findings}


def test_ranking_by_expected_recovery_desc():
    findings = [
        _finding(
            "quote-resurrection",
            [{"id": "e1", "name": "Small", "total": 1000.0}],
        ),
        _finding(
            "plan-churn-risk",
            [{"id": "e2", "name": "Big", "total": 1000.0}],
        ),
    ]
    with patch("app.leak_engine.run_leak_scan", return_value=_brief(findings)):
        out = ba.top_brief_actions(db=object(), org_id="o1", top_n=5)
    # plan-churn-risk weight 0.50 > quote-resurrection 0.35
    assert [a["rank"] for a in out] == [1, 2]
    assert out[0]["entity_name"] == "Big"
    assert out[0]["expected_recovery"] == 500.0
    assert out[0]["next_move"] == "Do the thing."


def test_one_action_per_entity_dedupe():
    findings = [
        _finding("quote-resurrection", [{"id": "e1", "name": "A", "total": 5000.0}]),
        _finding("plan-churn-risk", [{"id": "e1", "name": "A", "total": 100.0}]),
    ]
    with patch("app.leak_engine.run_leak_scan", return_value=_brief(findings)):
        out = ba.top_brief_actions(db=object(), org_id="o1", top_n=5)
    assert len(out) == 1
    # highest expected recovery wins the dedupe
    assert out[0]["detector"] == "quote-resurrection"


def test_top_n_respected():
    findings = [
        _finding("quote-resurrection", [{"id": f"e{i}", "name": f"N{i}", "total": 100.0 * i}])
        for i in range(1, 6)
    ]
    with patch("app.leak_engine.run_leak_scan", return_value=_brief(findings)):
        out = ba.top_brief_actions(db=object(), org_id="o1", top_n=3)
    assert len(out) == 3


def test_empty_on_scan_error_never_raises():
    with patch("app.leak_engine.run_leak_scan", side_effect=RuntimeError("db down")):
        assert ba.top_brief_actions(db=object(), org_id="o1") == []


def test_empty_when_no_findings():
    with patch("app.leak_engine.run_leak_scan", return_value=_brief([])):
        assert ba.top_brief_actions(db=object(), org_id="o1") == []


def test_email_section_escapes_markup():
    actions = [
        {
            "rank": 1,
            "title": "Leak",
            "entity_name": "<script>alert(1)</script>",
            "next_move": "Call <b>now</b>",
            "expected_recovery": 250.0,
            "dollars": 500.0,
            "detector": "quote-resurrection",
        }
    ]
    html_out = ba.render_email_section(actions)
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "Your top money moves" in html_out
    assert "$250 expected" in html_out
    assert "heuristic priors" in html_out


def test_email_section_empty_when_no_actions():
    assert ba.render_email_section([]) == ""


def test_sms_line_format():
    actions = [
        {
            "rank": 1,
            "title": "Leak",
            "entity_name": "Acme Corp",
            "next_move": "x",
            "expected_recovery": 1200.0,
            "dollars": 2400.0,
            "detector": "plan-churn-risk",
        }
    ]
    line = ba.render_sms_line(actions)
    assert line == "Top move: Acme Corp ($1,200 expected)"
    assert ba.render_sms_line([]) == ""
