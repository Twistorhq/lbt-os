#!/usr/bin/env python3
# Prepared by Twistor Holdings LLC
"""Registry-driven family board server (TW-924).

    python3 serve_registry.py [--port 8766] [--registry registry.json]

Routes, following the LibreChat Agent Management API pattern:
- GET   /                            board, driven by the registry
- GET   /agents/<handle>             one agent, files and access scopes
- GET   /api/agents                  discover (query: archetype, status, tool)
- GET   /api/agents/<handle>         get one agent as JSON
- POST  /api/agents                  create (JSON body), persists to the file
- PATCH /api/agents/<handle>         update (JSON body), persists to the file
- PUT   /api/agents/<handle>         update alias, same handler as PATCH
- POST  /api/agents/<handle>/retire  retire (soft), persists; body optional

Bad JSON bodies and non-object JSON bodies get a clean 400, never a 500.
Standard library only. Localhost only. No accounts, $0.
"""

import argparse
import html
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from board_registry import render_agent_detail, render_board
from registry import (
    RegistryError,
    create_agent,
    discover,
    get_agent,
    load_registry,
    retire_agent,
    save_registry,
    update_agent,
)

REGISTRY_PATH = str(HERE / "registry.json")
MAX_BODY = 65536


class BadBodyError(ValueError):
    """Raised when a request body cannot be used; maps to HTTP 400."""


class RegistryHandler(BaseHTTPRequestHandler):
    registry_path = REGISTRY_PATH

    def _send(self, body, code=200, content_type="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload, code=200):
        self._send(
            json.dumps(payload, indent=2, sort_keys=True),
            code,
            "application/json; charset=utf-8",
        )

    def _content_length(self):
        """Parse Content-Length; anything unusable is a 400, not a 500."""
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        try:
            length = int(raw)
        except ValueError:
            raise BadBodyError("bad Content-Length") from None
        if length < 0 or length > MAX_BODY:
            raise BadBodyError("bad request body")
        return length

    def _json_body(self):
        """Read the request body as a JSON object, or raise BadBodyError."""
        length = self._content_length()
        if length <= 0:
            raise BadBodyError("bad request body")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadBodyError("malformed JSON body") from exc
        if not isinstance(payload, dict):
            raise BadBodyError("JSON body must be an object")
        return payload

    def _drain_optional_body(self):
        """Retire takes no body; read and ignore one if a client sent it."""
        length = self._content_length()
        if length > 0:
            self.rfile.read(length)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            data = load_registry(self.registry_path)
            qs = parse_qs(parsed.query)
            if parsed.path in ("/", "/index.html"):
                archetype = (qs.get("archetype") or [None])[0] or None
                status = (qs.get("status") or [None])[0] or None
                agents = discover(data, archetype=archetype, status=status)
                self._send(render_board(agents, len(data["agents"]), archetype, status))
                return
            if parsed.path == "/api/agents":
                agents = discover(
                    data,
                    archetype=(qs.get("archetype") or [None])[0] or None,
                    status=(qs.get("status") or [None])[0] or None,
                    tool=(qs.get("tool") or [None])[0] or None,
                )
                self._send_json({"agents": agents, "count": len(agents)})
                return
            if parsed.path.startswith("/agents/") or parsed.path.startswith(
                "/api/agents/"
            ):
                handle = parsed.path.rsplit("/", 1)[-1]
                agent = get_agent(data, handle)
                if agent is None:
                    if parsed.path.startswith("/api/"):
                        self._send_json({"error": "unknown agent"}, 404)
                    else:
                        self._send("<h1>404</h1><p>unknown agent</p>", 404)
                    return
                if parsed.path.startswith("/api/"):
                    self._send_json(agent)
                else:
                    self._send(render_agent_detail(agent))
                return
            self._send("<h1>404</h1><p><a href='/'>board</a></p>", 404)
        except Exception as exc:  # noqa: BLE001, deliberate: no traceback to browser
            self._send(f"<h1>500</h1><p>{html.escape(type(exc).__name__)}</p>", 500)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if (
                parts[:2] == ["api", "agents"]
                and len(parts) == 4
                and parts[3] == "retire"
            ):
                self._drain_optional_body()
                data = load_registry(self.registry_path)
                agent = retire_agent(data, parts[2])
                save_registry(data, self.registry_path)
                self._send_json(agent)
                return
            payload = self._json_body()
            data = load_registry(self.registry_path)
            if parsed.path == "/api/agents":
                agent = create_agent(data, payload)
                save_registry(data, self.registry_path)
                self._send_json(agent, 201)
                return
            self._send_json({"error": "unknown route"}, 404)
        except BadBodyError as exc:
            self._send_json({"error": str(exc)}, 400)
        except RegistryError as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001, deliberate: no traceback to browser
            self._send_json({"error": type(exc).__name__}, 500)

    def _do_update(self):
        try:
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if parts[:2] != ["api", "agents"] or len(parts) != 3:
                self._send_json({"error": "unknown route"}, 404)
                return
            payload = self._json_body()
            data = load_registry(self.registry_path)
            if get_agent(data, parts[2]) is None:
                self._send_json({"error": "unknown agent"}, 404)
                return
            agent = update_agent(data, parts[2], payload)
            save_registry(data, self.registry_path)
            self._send_json(agent)
        except BadBodyError as exc:
            self._send_json({"error": str(exc)}, 400)
        except RegistryError as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001, deliberate: no traceback to browser
            self._send_json({"error": type(exc).__name__}, 500)

    do_PATCH = _do_update
    do_PUT = _do_update

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description="Twistor registry-driven family board")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--registry", default=REGISTRY_PATH)
    args = ap.parse_args()
    RegistryHandler.registry_path = args.registry
    srv = HTTPServer(("127.0.0.1", args.port), RegistryHandler)
    print(f"Twistor Registry Board on http://127.0.0.1:{args.port}/")
    print("Ctrl-C to stop.")
    srv.serve_forever()


if __name__ == "__main__":
    main()
