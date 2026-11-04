# Prepared by Twistor Holdings LLC
"""TW-924 tests: registry lifecycle and registry-driven board rendering."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from board_registry import render_agent_detail, render_board
from registry import (
    RegistryError,
    create_agent,
    discover,
    get_agent,
    load_registry,
    retire_agent,
    update_agent,
    validate_registry,
)

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
