# Prepared by Twistor Holdings LLC

# Agent Registry Board (TW-924)

LibreChat Agent Management API patterns applied to the Twistor family
board. Pattern source: danny-avila/LibreChat, MIT, score 76 in Diego's
TW-919 brief (section 7). Patterns and small original pieces only. No
LibreChat code is copied, and there are no MongoDB or Meilisearch imports.

## Where the board lives

The family board Justynn watches is not in a git repo. It lives at
`~/workspace/twistor-lab/agent-ops/agentbus/board/` (`serve.py` plus
`board.py`, TW-485, built 2026-10-13, with the TW-581 boss-reply path).
That tree has no `.git`, so this prototype lives in the lbt-os worktree
for TW-924, shaped as a drop-in registry layer for that board: same
palette, same data (the 15 family agents), same localhost-only posture.

Before TW-924, the board hardcoded the roster twice in `board.py`: a
`ROSTER` tuple and a `FAMILY` name-and-title dict. No archetype, no tool
scope, no access scope, no lifecycle, no API. Adding an agent meant
editing code in two places.

## What the registry adds

- `registry.json` is the single source of truth: 15 agents, each with
  id, handle, name, role, archetype, skills, files, tool scope, access
  scopes (channels, read, write), status, and dates.
- `registry.py` is the lifecycle: create, discover (filter by archetype,
  status, or tool), get, update (handle and id immutable), retire (soft,
  the record stays for audit). Validation rejects unknown archetypes,
  unknown statuses, duplicate handles, and missing access scopes.
- `board_registry.py` renders the board and agent detail pages from the
  registry only. Retire an agent in the JSON and the board shows it, no
  code change. That is the immediate-use property, and a test proves it.
- `serve_registry.py` serves the board plus a JSON API in the LibreChat
  management shape: `GET /api/agents`, `GET /api/agents/<handle>`,
  `POST /api/agents`, `POST /api/agents/<handle>/retire`.

## Run it

```bash
cd agent-registry-board
python3 serve_registry.py                # http://127.0.0.1:8766/
python3 -m pytest tests/ -q
python3 -m ruff check registry.py board_registry.py serve_registry.py tests/
python3 -m ruff format --check registry.py board_registry.py serve_registry.py tests/
```

Browser-first: wide registry table on desktop. Mobile view below 700px:
rows stack into one column, every field stays reachable, filter controls
are at least 44px tall, nothing depends on hover.

## Evidence

`evidence/before/` holds the original board as it was: file state, the
rendered HTML, and screenshots at 1440 and 375 from the live TW-485
server. `evidence/after/` holds the prototype running on the registry:
screenshots at 1440 and 375, the board HTML, an agent detail page, the
API responses, and the gate audit note.

## Frontend gate

Impeccable file scan plus manual 15-tell audit and the unslop pass are
recorded in `evidence/after/GATE.md`. No gradients, no gradient text, no
pill badges (tags use 4px corners), no glassmorphism, no decorative
animation, no em-dashes in copy.
