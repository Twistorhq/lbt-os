# Prepared by Twistor Holdings LLC
"""Registry-driven family board renderer (TW-924).

Same trades-rugged surface as the TW-485 board (bone, concrete, safety
orange, near-black header), but every name, role, archetype, skill, file,
tool scope, access scope, and status on the page comes from registry.json
through registry.py. Nothing about an agent is hardcoded here.

Browser-first layout: a wide registry table on desktop. Mobile view: the
same rows stack into a single column with every field still reachable,
touch targets at least 44px, no hover-only behavior.
"""

import html

STATUS_LABEL = {"active": "Active", "paused": "Paused", "retired": "Retired"}


def _e(value):
    return html.escape(str(value))


def _tags(values):
    return "".join(f'<span class="tag">{_e(v)}</span>' for v in values)


def agent_row(agent):
    """One registry row. Links to the agent detail view."""
    status = agent.get("status", "active")
    return f"""
<tr class="agent-row" data-status="{_e(status)}">
  <td class="cell-name"><a href="/agents/{_e(agent["handle"])}">{_e(agent["name"])}</a>
    <span class="handle">@{_e(agent["handle"])}</span></td>
  <td>{_e(agent["role"])}</td>
  <td>{_e(agent["archetype"])}</td>
  <td>{_tags(agent.get("skills", []))}</td>
  <td>{_tags(agent.get("tools", []))}</td>
  <td><span class="status status-{_e(status)}">{_e(STATUS_LABEL.get(status, status))}</span></td>
</tr>"""


def render_board(agents, total_agents, archetype=None, status=None):
    """Full board page listing the agents the registry returned."""
    rows = "\n".join(agent_row(a) for a in agents) or (
        '<tr><td colspan="6" class="empty">No agents match this filter. '
        "Clear the filter to see the whole family.</td></tr>"
    )
    active = sum(1 for a in agents if a.get("status") == "active")
    archetypes = sorted({a["archetype"] for a in agents})
    summary = (
        f"{len(agents)} of {total_agents} agents shown, {active} active. "
        f"Archetypes in view: {', '.join(archetypes) if archetypes else 'none'}."
    )
    arch_options = "".join(
        f'<option value="{_e(a)}"{" selected" if a == archetype else ""}>{_e(a)}</option>'
        for a in [
            "",
            "generalist",
            "sales",
            "marketing",
            "frontend-developer",
            "backend-developer",
        ]
    )
    status_options = "".join(
        f'<option value="{_e(s)}"{" selected" if s == status else ""}>{_e(s)}</option>'
        for s in ["", "active", "paused", "retired"]
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Twistor Family Board, Agent Registry</title>
<style>
  :root {{
    --bone: #f4efe4; --concrete: #e9e3d3; --card: #fffdf7;
    --ink: #211d16; --muted: #6f6a5c; --orange: #e85d04; --link: #9a3a00; --line: #d8d0bc;
    --green: #2d6a2d; --red: #a31621;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bone); color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.45; }}
  header {{ background: #211d16; color: var(--bone); padding: 14px 20px;
    border-bottom: 3px solid var(--orange); }}
  header h1 {{ margin: 0; font-size: 1.15rem; }}
  header h1 .dot {{ color: var(--orange); }}
  header p {{ margin: 4px 0 0; color: #b9b2a0; font-size: .85rem; }}
  .filters {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
    padding: 12px 20px; background: var(--concrete); border-bottom: 1px solid var(--line); }}
  .filters select {{ min-height: 44px; padding: 6px 10px; border: 1px solid var(--line);
    border-radius: 4px; background: #fff; font: inherit; }}
  .filters a {{ color: var(--link); min-height: 44px; display: inline-flex; align-items: center; }}
  main {{ max-width: 1180px; margin: 0 auto; padding: 16px 16px 60px; }}
  .summary {{ color: var(--muted); margin: 4px 0 14px; }}
  table.registry {{ width: 100%; border-collapse: collapse; background: var(--card);
    border: 1px solid var(--line); }}
  .registry th {{ text-align: left; font-size: .82rem; padding: 10px 12px;
    background: var(--concrete); border-bottom: 2px solid var(--line); }}
  .registry td {{ padding: 10px 12px; border-bottom: 1px solid var(--line);
    vertical-align: top; font-size: .92rem; }}
  .cell-name a {{ color: var(--ink); font-weight: 700; }}
  .handle {{ display: block; color: var(--muted); font-size: .8rem; }}
  .tag {{ display: inline-block; border: 1px solid var(--line); border-radius: 4px;
    background: var(--bone); padding: 1px 7px; margin: 0 4px 4px 0; font-size: .8rem; }}
  .status {{ font-weight: 700; font-size: .85rem; }}
  .status-active {{ color: var(--green); }}
  .status-paused {{ color: var(--link); }}
  .status-retired {{ color: var(--red); }}
  .empty {{ color: var(--muted); }}
  footer {{ text-align: center; color: var(--muted); font-size: .78rem; padding: 20px; }}
  @media (max-width: 700px) {{
    .registry thead {{ display: none; }}
    .registry, .registry tbody, .registry tr, .registry td {{ display: block; width: 100%; }}
    .registry tr {{ border-bottom: 2px solid var(--line); padding: 8px 0; }}
    .registry td {{ border-bottom: none; padding: 4px 12px; }}
    .cell-name a {{ font-size: 1.05rem; }}
  }}
</style>
</head>
<body>
<header>
  <h1><span class="dot">Twistor</span> Family Board, Agent Registry</h1>
  <p>Registry pattern from LibreChat Agent Management API, applied to the family board. Prepared by Twistor Holdings LLC</p>
</header>
<div class="filters">
  <form method="get" action="/">
    <label>Archetype
      <select name="archetype" onchange="this.form.submit()">{arch_options}</select>
    </label>
    <label>Status
      <select name="status" onchange="this.form.submit()">{status_options}</select>
    </label>
    <noscript><button type="submit">Filter</button></noscript>
  </form>
  <a href="/">Clear filters</a>
  <a href="/api/agents">Registry JSON API</a>
</div>
<main>
  <p class="summary">{_e(summary)}</p>
  <table class="registry">
    <thead><tr><th>Agent</th><th>Role</th><th>Archetype</th><th>Skills</th><th>Tool scope</th><th>Status</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
</main>
<footer>agent registry, TW-924. Internal, family eyes only.</footer>
</body>
</html>"""


def render_agent_detail(agent):
    """Detail page for one agent: files and access scopes included."""
    access = agent.get("access", {})
    files = "".join(f"<li><code>{_e(f)}</code></li>" for f in agent.get("files", []))
    status = agent.get("status", "active")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(agent["name"])}, Twistor Agent Registry</title>
<style>
  :root {{ --bone: #f4efe4; --concrete: #e9e3d3; --card: #fffdf7;
    --ink: #211d16; --muted: #6f6a5c; --orange: #e85d04; --link: #9a3a00; --line: #d8d0bc; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bone); color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.5; }}
  header {{ background: #211d16; color: var(--bone); padding: 14px 20px;
    border-bottom: 3px solid var(--orange); }}
  header h1 {{ margin: 0; font-size: 1.15rem; }}
  main {{ max-width: 860px; margin: 0 auto; padding: 20px 16px 60px; }}
  .card {{ background: var(--card); border: 1px solid var(--line); padding: 16px; }}
  dt {{ font-weight: 700; margin-top: 12px; }}
  dd {{ margin: 2px 0 0 0; }}
  code {{ background: var(--concrete); padding: 0 5px; border-radius: 4px; }}
  a {{ color: var(--link); }}
  .back {{ display: inline-flex; min-height: 44px; align-items: center; }}
</style>
</head>
<body>
<header><h1>{_e(agent["name"])}</h1></header>
<main>
  <p><a class="back" href="/">Back to the board</a></p>
  <div class="card">
    <dl>
      <dt>Registry id</dt><dd><code>{_e(agent["id"])}</code></dd>
      <dt>Role</dt><dd>{_e(agent["role"])}</dd>
      <dt>Archetype</dt><dd>{_e(agent["archetype"])}</dd>
      <dt>Status</dt><dd>{_e(STATUS_LABEL.get(status, status))}</dd>
      <dt>Skills</dt><dd>{_e(", ".join(agent.get("skills", [])))}</dd>
      <dt>Tool scope</dt><dd>{_e(", ".join(agent.get("tools", [])))}</dd>
      <dt>Files</dt><dd><ul>{files}</ul></dd>
      <dt>Access, channels</dt><dd>{_e(", ".join(access.get("channels", [])))}</dd>
      <dt>Access, read</dt><dd>{_e(", ".join(access.get("read", [])))}</dd>
      <dt>Access, write</dt><dd>{_e(", ".join(access.get("write", [])))}</dd>
      <dt>Registered</dt><dd>{_e(agent.get("registered_at", ""))}</dd>
      <dt>Updated</dt><dd>{_e(agent.get("updated_at", ""))}</dd>
    </dl>
  </div>
</main>
</body>
</html>"""
