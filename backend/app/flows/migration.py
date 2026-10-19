"""TW-625 Twistor Flows — Zapier-concept importer.

Drop-in migration PATH from Zapier/n8n workflow CONCEPTS (not their code —
n8n is fair-code and was never touched). Converts a Zapier-shaped JSON
(trigger + action list) into a Flows definition, which must then pass
models.validate_definition.

Concept mapping:
  zap trigger app "webhook"/"schedule" -> flows trigger webhook/schedule
  action app "email" send              -> step notify (email)
  action app "sms" send                -> step notify (sms)
  action app "delay" for               -> step delay
  action app "filter" only_continue    -> step branch
  action app "webhook" post           -> step webhook (outbound)
  action app "code" *                  -> REFUSED (UnsupportedStepError)
  anything else                        -> REFUSED (UnsupportedStepError)

Refusals are loud by design: a silently dropped code step is how
migrations corrupt businesses.
"""

from __future__ import annotations

import uuid


class UnsupportedStepError(ValueError):
    """A Zapier concept Flows V1 cannot represent. Fails loud."""


def _step_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _convert_action(action: dict) -> dict:
    app = (action.get("app") or "").lower()
    act = (action.get("action") or "").lower()

    if app == "email" and act == "send":
        return {
            "id": _step_id("import"),
            "type": "notify",
            "channel": "email",
            "to": action.get("to") or "",
            "subject": action.get("subject"),
            "body": action.get("body") or "",
        }
    if app == "sms" and act == "send":
        return {
            "id": _step_id("import"),
            "type": "notify",
            "channel": "sms",
            "to": action.get("to") or "",
            "body": action.get("body") or "",
        }
    if app == "delay" and act in ("for", "until"):
        return {
            "id": _step_id("import"),
            "type": "delay",
            "duration": action.get("duration") or "1d",
        }
    if app == "filter" and act in ("only_continue", "continue"):
        field = action.get("field") or "ok"
        op = {"equals": "==", "not_equals": "!="}.get(
            (action.get("op") or "equals").lower(), "=="
        )
        value = action.get("value", "")
        return {
            "id": _step_id("import"),
            "type": "branch",
            "condition": f"{{{{{field}}}}} {op} {value}",
            "then": [],
            "else": [],
        }
    if app == "webhook" and act in ("post", "get", "custom"):
        return {
            "id": _step_id("import"),
            "type": "webhook",
            "method": act.upper() if act in ("post", "get") else "POST",
            "url": action.get("url") or "",
            "variables": {"body": action.get("body") or {}},
        }
    if app == "code":
        raise UnsupportedStepError(
            "code steps cannot migrate to Flows V1 — rewrite the logic as "
            "notify/branch/webhook steps (documented, never silent)"
        )
    raise UnsupportedStepError(f"no Flows equivalent for app={app!r} action={act!r}")


def _convert_trigger(trigger: dict) -> dict:
    app = (trigger.get("app") or "").lower()
    if app == "webhook":
        return {"type": "webhook", "key": f"whsec-{uuid.uuid4().hex}"}
    if app == "schedule":
        return {"type": "schedule", "cron": "0 9 * * *"}
    return {"type": "manual"}


def from_zapier(zap: dict) -> dict:
    """Convert Zapier-concept JSON to a Flows definition dict.

    A filter ("only continue if") wraps all REMAINING actions in its
    then-branch — the Zapier semantic. Anything V1 cannot represent
    raises UnsupportedStepError (loud, never silently dropped).
    The result is NOT yet validated — call models.validate_definition.
    """
    if not isinstance(zap, dict):
        raise UnsupportedStepError("zap must be a JSON object")
    actions = zap.get("actions") or []
    if not actions:
        raise UnsupportedStepError("zap has no actions to migrate")
    steps: list[dict] = []
    i = 0
    while i < len(actions):
        action = actions[i]
        app = (action.get("app") or "").lower()
        act = (action.get("action") or "").lower()
        if app == "filter" and act in ("only_continue", "continue"):
            branch = _convert_action(action)
            # everything after the filter runs only when it passes
            branch["then"] = [_convert_action(a) for a in actions[i + 1 :]]
            steps.append(branch)
            break
        steps.append(_convert_action(action))
        i += 1
    return {
        "name": zap.get("name") or "imported zap",
        "trigger": _convert_trigger(zap.get("trigger") or {}),
        "steps": steps,
    }
