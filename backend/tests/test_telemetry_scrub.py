"""TW-622 Twistor Telemetry — PII scrubbing at ingestion (TDD RED).

Doctrine TW-552: PII hashed at ingestion, never raw in gold.
"""

from app.telemetry import scrub


def test_hashes_email_and_phone_values():
    props = {"email": "a@b.com", "phone": "+13035551212", "plan": "pro"}
    out = scrub.scrub_properties(props, org_id="org-1")
    assert out["email"] != "a@b.com"
    assert out["phone"] != "+13035551212"
    assert out["plan"] == "pro"
    # hashed values are 64-hex (sha256)
    assert len(out["email"]) == 64
    assert all(c in "0123456789abcdef" for c in out["email"])


def test_scrub_is_recursive():
    props = {"user": {"email": "a@b.com", "tags": ["x", {"phone": "123"}]}}
    out = scrub.scrub_properties(props, org_id="org-1")
    assert out["user"]["email"] != "a@b.com"
    assert out["user"]["tags"][1]["phone"] != "123"


def test_scrub_is_deterministic_per_org():
    props = {"email": "a@b.com"}
    a = scrub.scrub_properties(props, org_id="org-1")
    b = scrub.scrub_properties(props, org_id="org-1")
    assert a["email"] == b["email"]


def test_scrub_differs_across_orgs():
    props = {"email": "a@b.com"}
    a = scrub.scrub_properties(props, org_id="org-1")
    b = scrub.scrub_properties(props, org_id="org-2")
    assert a["email"] != b["email"]


def test_distinct_id_always_hashed():
    h1 = scrub.hash_distinct_id("user-123", org_id="org-1")
    h2 = scrub.hash_distinct_id("user-123", org_id="org-1")
    assert h1 == h2
    assert h1 != "user-123"
    assert len(h1) == 64


def test_custom_pii_keys():
    props = {"employee_id": "E-99", "plan": "pro"}
    out = scrub.scrub_properties(props, org_id="org-1", extra_pii_keys=["employee_id"])
    assert out["employee_id"] != "E-99"
    assert out["plan"] == "pro"


def test_non_string_values_under_pii_keys_hashed():
    props = {"phone": 13035551212}
    out = scrub.scrub_properties(props, org_id="org-1")
    assert out["phone"] != 13035551212


def test_original_dict_not_mutated():
    props = {"email": "a@b.com", "nested": {"phone": "1"}}
    scrub.scrub_properties(props, org_id="org-1")
    assert props["email"] == "a@b.com"
    assert props["nested"]["phone"] == "1"


def test_segment_matching_no_overmatch():
    # hostname/filename must NOT match the "name" hint; shipping/skip must
    # NOT match "ip"; adobe must NOT match "dob".
    props = {
        "hostname": "web-1",
        "filename": "a.csv",
        "shipping": "fast",
        "adobe": "suite",
        "user_email": "a@b.com",
        "emailAddress": "c@d.com",
        "ip_address": "1.2.3.4",
        "dob": "1990-01-01",
    }
    out = scrub.scrub_properties(props, org_id="org-1")
    assert out["hostname"] == "web-1"
    assert out["filename"] == "a.csv"
    assert out["shipping"] == "fast"
    assert out["adobe"] == "suite"
    assert out["user_email"] != "a@b.com"
    assert out["emailAddress"] != "c@d.com"
    assert out["ip_address"] != "1.2.3.4"
    assert out["dob"] != "1990-01-01"
