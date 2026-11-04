# Prepared by Twistor Holdings LLC
"""TW-924 tests: registry lifecycle and registry-driven board rendering."""

import copy
import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from board_registry import render_agent_detail, render_board
from registry import (
    ARCHETYPES,
    RegistryError,
    create_agent,
    discover,
    get_agent,
    load_registry,
    retire_agent,
    update_agent,
    validate_registry,
)
from serve_registry import RegistryHandler

REGISTRY = Path(__file__).resolve().parents[1] / "registry.json"


@pytest.fixture()
def data():
    return load_registry(REGISTRY)


def test_registry_has_fifteen_family_agents(data):
    assert len(data["agents"]) == 15
    handles = {a["handle"] for a in data["agents"]}
    assert handles == {
        "zeke",
        "rosa",
        "dre",
        "sofia",
        "dez",
        "priya",
        "tommy",
        "angela",
        "rex",
        "imani",
        "nia",
        "maya",
        "diego",
        "jalen",
        "kofi",
    }


def test_every_agent_has_archetype_skills_files_access(data):
    for agent in data["agents"]:
        assert agent["archetype"]
        assert agent["skills"] and agent["files"] and agent["tools"]
        assert set(agent["access"]) == {"channels", "read", "write"}
        assert agent["id"] == f"agent:{agent['handle']}"


def test_discover_by_archetype_and_status(data):
    frontend = discover(data, archetype="frontend-developer")
    assert {a["handle"] for a in frontend} == {"sofia", "jalen"}
    assert discover(data, status="retired") == []
    assert len(discover(data, status="active")) == 15
    assert {a["handle"] for a in discover(data, tool="git")} >= {"zeke", "sofia"}


def test_create_update_retire_lifecycle(data):
    draft = {
        "handle": "testagent",
        "name": "Test Agent",
        "role": "Prototype Role",
        "archetype": "inbox",
        "skills": ["twistor-inbox"],
        "files": ["custom-skills/example/SKILL.md"],
        "tools": ["inbox"],
        "access": {"channels": ["board"], "read": ["board"], "write": ["board"]},
    }
    created = create_agent(data, draft)
    assert created["id"] == "agent:testagent"
    assert created["status"] == "active"
    with pytest.raises(RegistryError):
        create_agent(data, draft)
    updated = update_agent(data, "testagent", {"role": "Inbox Agent"})
    assert updated["role"] == "Inbox Agent"
    with pytest.raises(RegistryError):
        update_agent(data, "testagent", {"handle": "renamed"})
    retired = retire_agent(data, "testagent")
    assert retired["status"] == "retired"
    assert get_agent(data, "testagent") is not None
    with pytest.raises(RegistryError):
        retire_agent(data, "testagent")
    assert validate_registry(data) == []


def test_create_rejects_unknown_archetype(data):
    bad = {
        "handle": "badagent",
        "name": "Bad",
        "role": "Bad",
        "archetype": "wizard",
        "skills": ["x"],
        "files": [],
        "tools": [],
        "access": {"channels": [], "read": [], "write": []},
    }
    with pytest.raises(RegistryError):
        create_agent(data, bad)


def test_board_renders_from_registry(data):
    page = render_board(data["agents"], len(data["agents"]))
    for agent in data["agents"]:
        assert agent["name"] in page
        assert agent["archetype"] in page
    assert "15 of 15 agents shown" in page
    detail = render_agent_detail(get_agent(data, "sofia"))
    assert "Sofia Reyes" in detail
    assert "frontend-developer" in detail
    assert "local-twistor-frontend-engineer/SKILL.md" in detail


def test_board_reflects_retirement_without_code_change(data):
    changed = copy.deepcopy(data)
    retire_agent(changed, "kofi")
    page = render_board(
        discover(changed, status="retired"), len(changed["agents"]), status="retired"
    )
    assert "Kofi Mensah" in page
    assert "Retired" in page


# --- TW-924 fix round 1 (Rosa gate) ---


def _draft(**overrides):
    record = {
        "handle": "newagent",
        "name": "New Agent",
        "role": "Prototype Role",
        "archetype": "inbox",
        "skills": ["twistor-inbox"],
        "files": ["custom-skills/example/SKILL.md"],
        "tools": ["inbox"],
        "access": {"channels": ["board"], "read": ["board"], "write": ["board"]},
    }
    record.update(overrides)
    return record


def test_failed_update_leaves_record_byte_identical(data):
    before = copy.deepcopy(get_agent(data, "sofia"))
    before_json = json.dumps(before, sort_keys=True)
    with pytest.raises(RegistryError):
        update_agent(data, "sofia", {"archetype": "wizard"})
    after = get_agent(data, "sofia")
    assert after == before
    assert json.dumps(after, sort_keys=True) == before_json
    with pytest.raises(RegistryError):
        update_agent(data, "sofia", {"skills": []})
    assert json.dumps(get_agent(data, "sofia"), sort_keys=True) == before_json


def test_update_cannot_patch_status(data):
    before = copy.deepcopy(get_agent(data, "sofia"))
    for target in ("retired", "paused"):
        with pytest.raises(RegistryError):
            update_agent(data, "sofia", {"status": target})
    assert get_agent(data, "sofia") == before
    assert get_agent(data, "sofia")["status"] == "active"


def test_update_cannot_patch_registered_at(data):
    original = get_agent(data, "sofia")["registered_at"]
    with pytest.raises(RegistryError):
        update_agent(data, "sofia", {"registered_at": "1999-01-01"})
    assert get_agent(data, "sofia")["registered_at"] == original


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "   "},
        {"name": 42},
        {"role": ""},
        {"role": None},
        {"skills": []},
        {"files": []},
        {"tools": []},
        {"skills": "twistor-inbox"},
        {"skills": [123]},
        {"files": [""]},
        {"tools": [None]},
        {"access": {"channels": [], "read": ["board"], "write": ["board"]}},
        {"access": {"channels": ["board"], "read": "board", "write": ["board"]}},
        {"access": {"channels": ["board"], "read": ["board"], "write": [7]}},
    ],
)
def test_create_rejects_empty_or_mistyped_fields(data, overrides):
    count = len(data["agents"])
    with pytest.raises(RegistryError):
        create_agent(data, _draft(**overrides))
    assert len(data["agents"]) == count
    assert get_agent(data, "newagent") is None


def test_lifecycle_default_today_is_the_real_current_date(data):
    created = create_agent(data, _draft())
    assert created["registered_at"] == datetime.now().astimezone().date().isoformat()
    assert created["updated_at"] == datetime.now().astimezone().date().isoformat()
    updated = update_agent(data, "newagent", {"role": "Inbox Agent"})
    assert updated["updated_at"] == datetime.now().astimezone().date().isoformat()
    retired = retire_agent(data, "newagent")
    assert retired["updated_at"] == datetime.now().astimezone().date().isoformat()


def test_all_registry_skill_paths_exist_on_disk(data):
    workspace = Path.home() / "workspace"
    for agent in data["agents"]:
        assert agent["files"], agent["handle"]
        for rel in agent["files"]:
            assert (workspace / rel).is_file(), f"{agent['handle']}: {rel}"


def test_board_filter_dropdown_lists_all_archetypes(data):
    page = render_board(data["agents"], len(data["agents"]))
    for archetype in ARCHETYPES:
        assert f'value="{archetype}"' in page


def test_board_agent_links_have_44px_touch_target(data):
    page = render_board(data["agents"], len(data["agents"]))
    assert ".cell-name a" in page
    assert "min-height: 44px" in page


@pytest.fixture()
def http_base(tmp_path):
    registry_copy = tmp_path / "registry.json"
    registry_copy.write_text(
        Path(REGISTRY).read_text(encoding="utf-8"), encoding="utf-8"
    )
    previous = RegistryHandler.registry_path
    RegistryHandler.registry_path = str(registry_copy)
    server = HTTPServer(("127.0.0.1", 0), RegistryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", registry_copy
    finally:
        server.shutdown()
        server.server_close()
        RegistryHandler.registry_path = previous


def _http(base, method, path, body=None, raw=None):
    data = raw
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        text = err.read().decode("utf-8")
        try:
            return err.code, json.loads(text)
        except json.JSONDecodeError:
            return err.code, {"raw": text}


def test_http_update_route_persists(http_base):
    base, registry_copy = http_base
    code, payload = _http(
        base, "PATCH", "/api/agents/sofia", body={"role": "Frontend Lead"}
    )
    assert code == 200
    assert payload["role"] == "Frontend Lead"
    stored = load_registry(registry_copy)
    assert get_agent(stored, "sofia")["role"] == "Frontend Lead"


def test_http_update_unknown_agent_is_404(http_base):
    base, _registry_copy = http_base
    code, payload = _http(base, "PATCH", "/api/agents/ghost", body={"role": "X"})
    assert code == 404
    assert "error" in payload


def test_http_update_invalid_patch_is_400_and_record_unchanged(http_base):
    base, registry_copy = http_base
    code, payload = _http(
        base, "PATCH", "/api/agents/sofia", body={"archetype": "wizard"}
    )
    assert code == 400
    assert "error" in payload
    code, payload = _http(
        base, "PATCH", "/api/agents/sofia", body={"status": "retired"}
    )
    assert code == 400
    stored = load_registry(registry_copy)
    assert get_agent(stored, "sofia")["archetype"] == "frontend-developer"
    assert get_agent(stored, "sofia")["status"] == "active"


def test_http_retire_accepts_no_body(http_base):
    base, registry_copy = http_base
    code, payload = _http(base, "POST", "/api/agents/kofi/retire")
    assert code == 200
    assert payload["status"] == "retired"
    stored = load_registry(registry_copy)
    assert get_agent(stored, "kofi")["status"] == "retired"


def test_http_create_valid_record_is_201(http_base):
    base, registry_copy = http_base
    code, payload = _http(base, "POST", "/api/agents", body=_draft(handle="httpagent"))
    assert code == 201
    assert payload["id"] == "agent:httpagent"
    stored = load_registry(registry_copy)
    assert get_agent(stored, "httpagent") is not None


def test_http_malformed_json_is_400_not_500(http_base):
    base, _registry_copy = http_base
    code, payload = _http(base, "POST", "/api/agents", raw=b"{not json")
    assert code == 400
    assert "error" in payload
    code, payload = _http(base, "PATCH", "/api/agents/sofia", raw=b"{not json")
    assert code == 400
    assert "error" in payload


def test_http_non_object_json_is_400_not_500(http_base):
    base, _registry_copy = http_base
    code, payload = _http(base, "POST", "/api/agents", raw=b'["not", "an", "object"]')
    assert code == 400
    assert "error" in payload
    code, payload = _http(base, "PATCH", "/api/agents/sofia", raw=b'"just a string"')
    assert code == 400
    assert "error" in payload
