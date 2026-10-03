"""Twistor-owned driver contract for browser automation (TW-178).

Mirrors the shape of cua's ``BaseComputerInterface`` (MIT, verified live
2026-09-23) so the ``CuaComputer`` adapter below is a thin delegation
layer. Nothing here imports cua at module scope: the cua dependency is
optional, operator-installed, and loaded lazily.

**Prepared by Twistor Holdings LLC**
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class AutomationError(Exception):
    """Anything the automation layer refuses to do or cannot do."""


class Computer(ABC):
    """Driver contract: a screen the agent loop can see and touch."""

    @abstractmethod
    async def screenshot(self) -> bytes: ...

    @abstractmethod
    async def click(self, x: int, y: int) -> None: ...

    @abstractmethod
    async def double_click(self, x: int, y: int) -> None: ...

    @abstractmethod
    async def right_click(self, x: int, y: int) -> None: ...

    @abstractmethod
    async def move_cursor(self, x: int, y: int) -> None: ...

    @abstractmethod
    async def type_text(self, text: str) -> None: ...

    @abstractmethod
    async def press_key(self, key: str) -> None: ...

    @abstractmethod
    async def hotkey(self, *keys: str) -> None: ...

    @abstractmethod
    async def scroll(self, x: int, y: int, clicks: int = 1) -> None: ...

    @abstractmethod
    async def get_screen_size(self) -> dict: ...

    @abstractmethod
    async def close(self) -> None: ...


class CuaComputer(Computer):
    """Adapter over a real ``cua.computer.Computer`` driver.

    Pass an already-constructed driver via ``computer=`` (tests, custom
    wiring), or let the adapter build one from ``**kwargs`` — which
    requires the operator to have installed the clean packages::

        pip install cua-agent          # WITHOUT the omni extra (AGPL)

    The AGPL guard runs before anything else: if ``cua_som`` is ever
    importable, construction fails loud.
    """

    def __init__(self, computer: Optional[Any] = None, **kwargs: Any) -> None:
        from app.services.browser_automation.license_guard import (
            assert_no_agpl,
        )

        assert_no_agpl()
        if computer is None:
            try:
                from cua.computer import Computer as CuaDriver
            except ImportError as exc:
                raise AutomationError(
                    "cua is not installed. Install the clean packages with "
                    "'pip install cua-agent' (WITHOUT the 'omni' extra — it "
                    "pulls the AGPL-licensed cua-som, which Twistor forbids)."
                ) from exc
            computer = CuaDriver(**kwargs)
        self._driver = computer

    async def screenshot(self) -> bytes:
        return await self._driver.screenshot()

    async def click(self, x: int, y: int) -> None:
        await self._driver.left_click(x, y)

    async def double_click(self, x: int, y: int) -> None:
        await self._driver.double_click(x, y)

    async def right_click(self, x: int, y: int) -> None:
        await self._driver.right_click(x, y)

    async def move_cursor(self, x: int, y: int) -> None:
        await self._driver.move_cursor(x, y)

    async def type_text(self, text: str) -> None:
        await self._driver.type_text(text)

    async def press_key(self, key: str) -> None:
        await self._driver.press_key(key)

    async def hotkey(self, *keys: str) -> None:
        await self._driver.hotkey(*keys)

    async def scroll(self, x: int, y: int, clicks: int = 1) -> None:
        await self._driver.scroll(x, y, clicks)

    async def get_screen_size(self) -> dict:
        return await self._driver.get_screen_size()

    async def close(self) -> None:
        await self._driver.close()
