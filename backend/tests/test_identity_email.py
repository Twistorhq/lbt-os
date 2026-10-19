"""TW-619 (Zeke) — identity email delivery tests (TDD)."""
import sys
import types

import pytest

from app import config
from app.services import email


def test_magic_link_email_body_has_link_and_no_emdash():
    html = email.identity_magic_link_html(
        link_url="https://portal.test/auth/magic-link?token=abc&purpose=login",
        purpose="login",
    )
    assert "https://portal.test/auth/magic-link?token=abc&amp;purpose=login" in html \
        or "https://portal.test/auth/magic-link?token=abc" in html
    assert "token=abc" in html
    assert "\u2014" not in html and "\u2013" not in html


def test_magic_link_email_copy_differs_by_purpose():
    login_html = email.identity_magic_link_html(
        link_url="https://x.test/?token=a", purpose="login")
    verify_html = email.identity_magic_link_html(
        link_url="https://x.test/?token=b", purpose="verify")
    assert login_html != verify_html
    assert "verify" in verify_html.lower()


def test_verify_email_body_no_emdash():
    html = email.identity_verify_email_html(link_url="https://x.test/?token=v")
    assert "token=v" in html
    assert "\u2014" not in html and "\u2013" not in html


def test_sso_invite_body_has_org_and_link_no_emdash():
    html = email.identity_sso_invite_html(
        org_name="Test University", link_url="https://x.test/sso/start")
    assert "Test University" in html
    assert "https://x.test/sso/start" in html
    assert "\u2014" not in html and "\u2013" not in html


def _fake_resend(monkeypatch):
    calls = []
    fake = types.ModuleType("resend")

    class _Emails:
        @staticmethod
        def send(payload):
            calls.append(payload)
            return {"id": "re_123"}

    fake.Emails = _Emails
    fake.api_key = None
    monkeypatch.setitem(sys.modules, "resend", fake)
    return calls


def test_send_magic_link_calls_resend_when_configured(monkeypatch):
    monkeypatch.setattr(config.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(config.settings, "identity_from_email",
                        "Twistor <noreply@twistor.co>")
    calls = _fake_resend(monkeypatch)
    email.send_identity_magic_link(
        to="Prof@Test.edu", token="tok123", purpose="login")
    assert len(calls) == 1
    assert calls[0]["to"] == ["Prof@Test.edu"]
    assert calls[0]["from"] == "Twistor <noreply@twistor.co>"
    assert "tok123" in calls[0]["html"]
    assert calls[0]["subject"]


def test_send_magic_link_skipped_without_api_key(monkeypatch):
    monkeypatch.setattr(config.settings, "resend_api_key", None)
    # Must not raise and must never store the token anywhere.
    email.send_identity_magic_link(
        to="u@example.com", token="tok123", purpose="verify")


def test_send_sso_invite_calls_resend(monkeypatch):
    monkeypatch.setattr(config.settings, "resend_api_key", "re_test_key")
    calls = _fake_resend(monkeypatch)
    email.send_identity_sso_invite(
        to="staff@test.edu", org_name="Test University",
        link_url="https://portal.test/sso")
    assert len(calls) == 1
    assert "Test University" in calls[0]["html"]


def test_identity_link_url_uses_portal_settings(monkeypatch):
    monkeypatch.setattr(config.settings, "frontend_url",
                        "https://portal.twistor.co/")
    monkeypatch.setattr(config.settings, "identity_magic_link_path",
                        "/auth/magic-link")
    url = email.identity_link_url("rawtok", purpose="verify")
    assert url.startswith("https://portal.twistor.co/auth/magic-link?")
    assert "token=rawtok" in url and "purpose=verify" in url
