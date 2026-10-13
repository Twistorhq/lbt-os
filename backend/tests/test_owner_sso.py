# Prepared by Twistor Holdings LLC
# TW-484: owner SSO — is_owner_email allowlist matching.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import is_owner_email
from app import config


def _set_allowlist(value):
    config.settings.owner_emails = value


def test_owner_email_match_case_insensitive():
    _set_allowlist("Twistorhq@gmail.com, Twistorhq@twistor.co, holla@twistor.co")
    assert is_owner_email("twistorhq@gmail.com") is True
    assert is_owner_email("TWISTORHQ@GMAIL.COM") is True
    assert is_owner_email("  holla@twistor.co  ") is True


def test_owner_email_rejects_non_owner():
    _set_allowlist("Twistorhq@gmail.com, Twistorhq@twistor.co, holla@twistor.co")
    assert is_owner_email("someone@else.com") is False
    assert is_owner_email("") is False
    assert is_owner_email(None) is False


def test_owner_email_empty_allowlist():
    _set_allowlist("")
    assert is_owner_email("twistorhq@gmail.com") is False
