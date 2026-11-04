# Prepared by Twistor Holdings LLC
"""Twistor agent registry (TW-924).

Pattern source: LibreChat Agent Management API (danny-avila/LibreChat, MIT,
score 76 in Diego's TW-919 brief, section 7). Patterns and small original
pieces only. No LibreChat code, no MongoDB, no Meilisearch.

LibreChat shape applied to the Twistor family board:
- agents: one record per agent (id, name, role)
- archetype and skills: the loadable shape on the agent (Twistor TW-788)
- files: the agent's skill files, listed on the record
- access scopes: channels plus read and write scopes per agent
- lifecycle: create, discover, update, retire (soft, never hard delete,
  so the board keeps its audit trail)

The registry is one JSON file. The board renderer reads only this file,
so adding or retiring an agent changes the board with no code edit. That
replaces the hardcoded ROSTER and FAMILY dicts in the TW-485 board.
"""

import json
import re
from pathlib import Path

ARCHETYPES = (
    "generalist",
    "sales",
    "marketing",
    "ads",
    "seo",
    "frontend-developer",
    "backend-developer",
    "c-suite",
    "payroll",
    "inbox",
    "hr",
)
STATUSES = ("active", "paused", "retired")
HANDLE_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
REQUIRED = ("handle", "name", "role", "archetype", "skills", "tools", "access")

DEFAULT_PATH = Path(__file__).resolve().parent / "registry.json"


class RegistryError(ValueError):
    """Raised when a registry record or lifecycle step is invalid."""


def load_registry(path=DEFAULT_PATH):
    """Read the registry file. Returns the parsed dict."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_registry(data)
    if errors:
        raise RegistryError("; ".join(errors))
    return data


def save_registry(data, path=DEFAULT_PATH):
    """Write the registry back, sorted keys, one trailing newline."""
    errors = validate_registry(data)
    if errors:
        raise RegistryError("; ".join(errors))
    Path(path).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def validate_agent(agent):
    """Return a list of problems with one agent record (empty = valid)."""
    errors = []
    for field in REQUIRED:
        if field not in agent:
            errors.append(f"{agent.get('handle', '?')}: missing {field}")
    handle = agent.get("handle", "")
    if not HANDLE_RE.match(str(handle)):
        errors.append(f"{handle or '?'}: bad handle")
    if agent.get("archetype") not in ARCHETYPES:
        errors.append(f"{handle}: unknown archetype {agent.get('archetype')}")
    if agent.get("status", "active") not in STATUSES:
        errors.append(f"{handle}: unknown status {agent.get('status')}")
    if agent.get("id") != f"agent:{handle}":
        errors.append(f"{handle}: id must be agent:{handle}")
    access = agent.get("access") or {}
    for scope in ("channels", "read", "write"):
        if scope not in access:
            errors.append(f"{handle}: access missing {scope}")
    return errors


def validate_registry(data):
    """Return a list of problems with the whole registry (empty = valid)."""
    errors = []
    seen = set()
    for agent in data.get("agents", []):
        errors.extend(validate_agent(agent))
        handle = agent.get("handle")
        if handle in seen:
            errors.append(f"{handle}: duplicate handle")
        seen.add(handle)
    return errors


def discover(data, archetype=None, status=None, tool=None):
    """Discover agents by archetype, status, or tool scope (LibreChat list)."""
    out = []
    for agent in data.get("agents", []):
        if archetype and agent["archetype"] != archetype:
            continue
        if status and agent.get("status", "active") != status:
            continue
        if tool and tool not in agent.get("tools", []):
            continue
        out.append(agent)
    return sorted(out, key=lambda a: a["handle"])


def get_agent(data, handle):
    """Get one agent by handle, or None."""
    for agent in data.get("agents", []):
        if agent["handle"] == handle:
            return agent
    return None


def create_agent(data, record, today="2026-11-04"):
    """Create lifecycle step. Validates, stamps id and dates, appends."""
    record = dict(record)
    record.setdefault("status", "active")
    record["id"] = f"agent:{record.get('handle', '')}"
    record.setdefault("registered_at", today)
    record["updated_at"] = today
    errors = validate_agent(record)
    if errors:
        raise RegistryError("; ".join(errors))
    if get_agent(data, record["handle"]) is not None:
        raise RegistryError(f"{record['handle']}: already registered")
    data["agents"].append(record)
    return record


def update_agent(data, handle, patch, today="2026-11-04"):
    """Update lifecycle step. Handle and id are immutable."""
    agent = get_agent(data, handle)
    if agent is None:
        raise RegistryError(f"{handle}: not registered")
    if "handle" in patch or "id" in patch:
        raise RegistryError("handle and id are immutable")
    agent.update(patch)
    agent["updated_at"] = today
    errors = validate_agent(agent)
    if errors:
        raise RegistryError("; ".join(errors))
    return agent


def retire_agent(data, handle, today="2026-11-04"):
    """Retire lifecycle step. Soft: status flips, record stays for audit."""
    agent = get_agent(data, handle)
    if agent is None:
        raise RegistryError(f"{handle}: not registered")
    if agent.get("status") == "retired":
        raise RegistryError(f"{handle}: already retired")
    agent["status"] = "retired"
    agent["updated_at"] = today
    return agent
