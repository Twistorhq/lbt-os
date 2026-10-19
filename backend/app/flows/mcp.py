"""TW-625 Twistor Flows — MCP tool surface.

The agent workforce orchestrates through its own automation layer (the
TW-617 forward play). Three tools, org-scoped by the caller's Identity
JWT, served over POST /flows/mcp as {"tool": ..., "args": {...}}.
"""

from __future__ import annotations

from supabase import Client

from . import models, service

TOOLS = [
    {
        "name": "flows.list",
        "description": "List the caller's workflows (id, name, status).",
        "args": {},
    },
    {
        "name": "flows.trigger",
        "description": "Trigger a workflow by id with an input object. "
        "Returns the run receipt.",
        "args": {"workflow_id": "string", "input": "object"},
    },
    {
        "name": "flows.run_status",
        "description": "Get a run's status and per-step ledger.",
        "args": {"run_id": "string"},
    },
    {
        "name": "flows.preview",
        "description": "Preview a workflow's cost upper bound before triggering it.",
        "args": {"workflow_id": "string"},
    },
]


def call_tool(db: Client, org_id: str, tool: str, args: dict) -> dict:
    """Dispatch one MCP tool call. Unknown tools fail loud."""
    args = args or {}
    if tool == "flows.list":
        return {"workflows": service.list_workflows(db, org_id)}
    if tool == "flows.trigger":
        wid = args.get("workflow_id")
        if not wid:
            raise models.DefinitionError("flows.trigger needs workflow_id")
        return service.trigger(db, org_id, wid, args.get("input") or {})
    if tool == "flows.run_status":
        rid = args.get("run_id")
        if not rid:
            raise models.DefinitionError("flows.run_status needs run_id")
        run = service.get_run(db, org_id, rid)
        if not run:
            raise KeyError(f"run {rid} not found")
        return run
    if tool == "flows.preview":
        wid = args.get("workflow_id")
        if not wid:
            raise models.DefinitionError("flows.preview needs workflow_id")
        return service.preview(db, org_id, wid)
    raise models.DefinitionError(
        f"unknown tool {tool!r}; available: " + ", ".join(t["name"] for t in TOOLS)
    )
