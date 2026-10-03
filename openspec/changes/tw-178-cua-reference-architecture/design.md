# TW-178 design

**Prepared by Twistor Holdings LLC**

## Driver contract (`computer.py`)
```python
class Computer(ABC):
    async def screenshot(self) -> bytes: ...
    async def click(self, x: int, y: int) -> None: ...
    async def double_click(self, x: int, y: int) -> None: ...
    async def right_click(self, x: int, y: int) -> None: ...
    async def move_cursor(self, x: int, y: int) -> None: ...
    async def type_text(self, text: str) -> None: ...
    async def press_key(self, key: str) -> None: ...
    async def hotkey(self, *keys: str) -> None: ...
    async def scroll(self, x: int, y: int, clicks: int = 1) -> None: ...
    async def get_screen_size(self) -> dict: ...
    async def close(self) -> None: ...
```
Subset of cua's `BaseComputerInterface` (verified live against the cua
repo 2026-09-23); names and shapes mirror cua so the adapter is trivial.
`CuaComputer(Computer)` takes an already-constructed `cua.computer.Computer`
(or constructs one from kwargs) and delegates every method; the cua import
happens inside the constructor only, so importing our module never requires
cua. If cua is missing, the error names the clean install command
(`pip install cua-agent` — WITHOUT the `omni` extra).

## License guard (`license_guard.py`)
- `FORBIDDEN_MODULES = ("cua_som",)`,
  `FORBIDDEN_DISTRIBUTIONS = ("cua-som",)`.
- `assert_no_agpl()` raises `AutomationError` if `importlib.util.find_spec`
  resolves any forbidden module or `importlib.metadata` lists a forbidden
  distribution. Called by `CuaComputer.__init__` and importable by CI.
- `verify_install_clean()` returns a dict report for ticket receipts.

## Agent loop (`runner.py`)
- `ActionType`: NAVIGATE, CLICK, DOUBLE_CLICK, RIGHT_CLICK, TYPE,
  PRESS_KEY, HOTKEY, SCROLL, SCREENSHOT, WAIT.
- `BrowserAction`: `action`, optional `url`/`x`/`y`/`text`/`key`/`keys`,
  `description` (human-readable, required for consequential actions),
  `requires_approval: bool = False`.
- `AutomationPlan`: `name`, `actions: list[BrowserAction]`,
  `allowed_hosts: list[str]` (empty = navigation forbidden).
- `run_plan(plan, computer, decide)` (async): for each action, if
  `requires_approval` → `await decide(action)` must be truthy or the run
  halts with `AutomationResult(..., halted=True, halt_reason="denied")`;
  NAVIGATE validates `urlparse(url).hostname` against `allowed_hosts`
  (exact or subdomain match) or raises. Returns `AutomationResult`
  (completed steps, screenshots taken, halt info). `decide` defaults to a
  callback that denies everything (safe default).

## Error handling
Fail loud. No silent skips, no retries inside the loop, no swallowing
`AutomationError`. Timeouts are the caller's (asyncio.wait_for), not the
runner's.

## What this deliberately is NOT
No VM provisioning, no cua cloud, no credential storage, no session
persistence, no scheduler. Those are follow-up tickets with their own
threat models.
