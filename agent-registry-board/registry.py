# Prepared by Twistor Holdings LLC
"""Twistor agent registry (TW-924).

Pattern source: LibreChat Agent Management API (danny-avila/LibreChat, MIT,
score 76 in Diego's TW-919 brief, section 7). Patterns and small original
pieces only. No LibreChat code, no MongoDB, no Meilisearch.

LibreChat shape applied to the Twistor agent registry:
- agents: one record per agent (id, name, role)
- archetype and skills: the loadable shape on the agent (Twistor TW-788)
- files: the agent's skill files, listed on the record
- access scopes: channels plus read and write scopes per agent
- lifecycle: create, discover, update, retire (soft, never hard delete,
  so the board keeps its audit trail)

The registry is one JSON file and the data layer this prototype stands
on: it can replace the hardcoded ROSTER and FAMILY dicts in the live
TW-485 board only behind a separate adapter (see README). The live board
itself is a message board and is not wired to this file.
"""

import copy
import json
import re
from datetime import datetime
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
REQUIRED = ("handle", "name", "role", "archetype", "skills", "files", "tools", "access")
LIST_FIELDS = ("skills", "files", "tools")
ACCESS_SCOPES = ("channels", "read", "write")
# Fields an update patch may never touch. Status changes only through the
# create and retire lifecycle steps; registered_at is stamped at create.
IMMUTABLE_PATCH_FIELDS = ("handle", "id", "status", "registered_at")

DEFAULT_PATH = Path(__file__).resolve().parent / "registry.json"


class RegistryError(ValueError):
    """Raised when a registry record or lifecycle step is invalid."""


def _resolve_today(today):
    """Return the caller's date stamp, or the real current local date."""
    return (
        today if today is not None else datetime.now().astimezone().date().isoformat()
    )


def _check_string_list(errors, handle, label, value):
    """Append problems for a field that must be a non-empty list of strings."""
    if not isinstance(value, list):
        errors.append(f"{handle}: {label} must be a list")
        return
    if not value:
        errors.append(f"{handle}: {label} must not be empty")
        return
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{handle}: {label} entries must be non-empty strings")
            return


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
    if not isinstance(agent, dict):
        return ["agent record must be an object"]
    errors = []
    for field in REQUIRED:
        if field not in agent:
            errors.append(f"{agent.get('handle', '?')}: missing {field}")
    handle = agent.get("handle", "")
    if not HANDLE_RE.match(str(handle)):
        errors.append(f"{handle or '?'}: bad handle")
    for field in ("name", "role"):
        value = agent.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{handle}: {field} must be a non-empty string")
    if agent.get("archetype") not in ARCHETYPES:
        errors.append(f"{handle}: unknown archetype {agent.get('archetype')}")
    if agent.get("status", "active") not in STATUSES:
        errors.append(f"{handle}: unknown status {agent.get('status')}")
    if agent.get("id") != f"agent:{handle}":
        errors.append(f"{handle}: id must be agent:{handle}")
    for field in LIST_FIELDS:
        if field in agent:
            _check_string_list(errors, handle, field, agent.get(field))
    access = agent.get("access")
    if "access" in agent:
        if not isinstance(access, dict):
            errors.append(f"{handle}: access must be an object")
        else:
            for scope in ACCESS_SCOPES:
                if scope not in access:
                    errors.append(f"{handle}: access missing {scope}")
                else:
                    _check_string_list(
                        errors, handle, f"access.{scope}", access.get(scope)
                    )
    return errors


def validate_registry(data):
    """Return a list of problems with the whole registry (empty = valid)."""
    errors = []
    seen = set()
    for agent in data.get("agents", []):
        errors.extend(validate_agent(agent))
        handle = agent.get("handle") if isinstance(agent, dict) else None
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


def create_agent(data, record, today=None):
    """Create lifecycle step. Validates, stamps id and dates, appends."""
    if not isinstance(record, dict):
        raise RegistryError("agent record must be an object")
    today = _resolve_today(today)
    record = copy.deepcopy(record)
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


def update_agent(data, handle, patch, today=None):
    """Update lifecycle step. Transactional: a failed patch changes nothing.

    The patch is applied to a copy and validated there first; the stored
    record is replaced only after the patched copy passes validation, so
    a rejected update leaves the record byte-identical. Handle, id,
    status, and registered_at are immutable through this step: status
    moves only through the create and retire lifecycle steps.
    """
    agent = get_agent(data, handle)
    if agent is None:
        raise RegistryError(f"{handle}: not registered")
    if not isinstance(patch, dict):
        raise RegistryError("patch must be an object")
    protected = [field for field in IMMUTABLE_PATCH_FIELDS if field in patch]
    if protected:
        raise RegistryError(
            f"{', '.join(protected)} cannot be changed by update; "
            "status moves only through create and retire, "
            "registered_at is stamped at create"
        )
    patched = copy.deepcopy(agent)
    patched.update(copy.deepcopy(patch))
    patched["updated_at"] = _resolve_today(today)
    errors = validate_agent(patched)
    if errors:
        raise RegistryError("; ".join(errors))
    agent.clear()
    agent.update(patched)
    return agent


def retire_agent(data, handle, today=None):
    """Retire lifecycle step. Soft: status flips, record stays for audit."""
    agent = get_agent(data, handle)
    if agent is None:
        raise RegistryError(f"{handle}: not registered")
    if agent.get("status") == "retired":
        raise RegistryError(f"{handle}: already retired")
    agent["status"] = "retired"
    agent["updated_at"] = _resolve_today(today)
    return agent
