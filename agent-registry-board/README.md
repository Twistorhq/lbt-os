# Prepared by Twistor Holdings LLC

# Agent Registry Board (TW-924)

LibreChat Agent Management API patterns applied to the Twistor family
board. Pattern source: danny-avila/LibreChat, MIT, score 76 in Diego's
TW-919 brief (section 7). Patterns and small original pieces only. No
LibreChat code is copied, and there are no MongoDB or Meilisearch imports.

## Where the board lives, and what this prototype is not

The family board Justynn watches is not in a git repo. It lives at
`~/workspace/twistor-lab/agent-ops/agentbus/board/` (`serve.py` plus
`board.py`, TW-485, built 2026-10-13, with the TW-581 boss-reply path).
That tree has no `.git`, so this prototype lives in the lbt-os worktree
for TW-924. It borrows the live board's palette and localhost-only
posture, and nothing more.

The live board is a message board, not a roster surface. Its
`render_board(messages, ...)` renders a message stream read from the
agentbus SQLite store, with threads and a TW-581 boss-reply form
(`POST /reply`). This prototype has no message stream, no threads, and
no boss reply, so it is not a drop-in replacement for the live board,
and nothing here is wired into it.

The live board also hardcodes its roster twice in `board.py`: a
`ROSTER` tuple (the 15 family agents) and a `FAMILY` name-and-title
dict. `FAMILY` carries two pseudo-entries this registry deliberately
omits, because they are not agents: `justynn` (the boss seat) and
`board` (the broadcast channel). No archetype, no tool scope, no access
scope, no lifecycle, no API exists on the live board today. Adding an
agent there still means editing code in two places.

The credible claim for TW-924 is narrower: this registry is a data
layer that could replace the `ROSTER` and `FAMILY` dicts as the source
of agent names, roles, archetypes, skills, files, tools, and access
scopes, after a separate adapter ticket teaches the live board to read
it (and to keep its `justynn` and `board` pseudo-entries). Until that
adapter exists, the registry and the live board are separate things.

## What the registry adds

- `registry.json` is the single source of truth: 15 agents, each with
  id, handle, name, role, archetype, skills, files, tool scope, access
  scopes (channels, read, write), status, and dates.
- `registry.py` is the lifecycle: create, discover (filter by archetype,
  status, or tool), get, update (handle, id, status, and registered_at
  immutable through update; status moves only through create and
  retire), retire (soft, the record stays for audit). Updates are
  transactional: a patch is validated on a copy and committed only on
  success, so a rejected update leaves the record byte-identical.
  Validation rejects unknown archetypes, unknown statuses, duplicate
  handles, missing access scopes, empty names and roles, and empty or
  mistyped skills, files, tools, and access lists.
- `board_registry.py` renders this prototype's board and agent detail
  pages from the registry only. Retire an agent in the JSON and the
  prototype board shows it, no code change. That is the immediate-use
  property, and a test proves it. It renders no message stream; see the
  section above for how it differs from the live board.
- `serve_registry.py` serves the prototype board plus a JSON API in the
  LibreChat management shape: `GET /api/agents`,
  `GET /api/agents/<handle>`, `POST /api/agents` (create),
  `PATCH /api/agents/<handle>` (update, `PUT` accepted as an alias),
  `POST /api/agents/<handle>/retire` (body optional). Malformed JSON and
  non-object JSON bodies get a clean 400.

## Run it

```bash
cd agent-registry-board
python3 serve_registry.py                # http://127.0.0.1:8766/
python3 -m pytest tests/ -q
python3 -m ruff check registry.py board_registry.py serve_registry.py tests/
python3 -m ruff format --check registry.py board_registry.py serve_registry.py tests/
```

Browser-first: wide registry table on desktop. Mobile view below 700px:
rows stack into one column, every field stays reachable, the filter
controls and the board-row agent links are at least 44px tall, nothing
depends on hover.

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
