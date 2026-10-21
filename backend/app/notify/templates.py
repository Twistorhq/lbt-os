"""TW-620 Twistor Notify — template rendering.

{{variable}} substitution. Missing variables fail loud — a notification
with a blank hole in it is worse than no notification.
"""

from __future__ import annotations

import re

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class TemplateError(ValueError):
    """Template references a variable the caller did not supply."""


def render_template(template: str, variables: dict) -> str:
    """Substitute {{var}} placeholders. Raises TemplateError on missing."""
    missing: list[str] = []

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            missing.append(name)
            return match.group(0)
        return str(variables[name])

    out = _VAR_RE.sub(_replace, template)
    if missing:
        raise TemplateError(
            f"template variables missing: {', '.join(sorted(set(missing)))}"
        )
    return out
