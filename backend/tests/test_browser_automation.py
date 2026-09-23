"""RED tests for TW-178: cua browser-automation reference architecture.

Fake drivers only. No real browser, no cua package, no network.
"""

import asyncio
import sys
import types
from dataclasses import FrozenInstanceError

import pytest


def drive(coro):
    return asyncio.run(coro)


class TestComputerContract:
    def test_abc_cannot_be_instantiated(self):
        from app.services.browser_automation.computer import Computer

        with pytest.raises(TypeError):
            Computer()

    def test_cua_adapter_refuses_without_cua(self):
        from app.services.browser_automation.computer import (
            AutomationError,
            CuaComputer,
        )

        with pytest.raises(AutomationError, match="pip install"):
            CuaComputer(computer=None)

    def test_cua_adapter_delegates_to_wrapped_driver(self):
        from app.services.browser_automation.computer import CuaComputer

        calls = []

        class StubCuaDriver:
            async def screenshot(self):
                calls.append("screenshot")
                return b"png-bytes"

            async def left_click(self, x, y):
                calls.append(("left_click", x, y))

            async def type_text(self, text):
                calls.append(("type_text", text))

        adapter = CuaComputer(computer=StubCuaDriver())
        assert drive(adapter.screenshot()) == b"png-bytes"
        drive(adapter.click(10, 20))
        drive(adapter.type_text("hello"))
        assert calls == [
            "screenshot",
            ("left_click", 10, 20),
            ("type_text", "hello"),
        ]


class TestLicenseGuard:
    def test_raises_when_agpl_module_importable(self):
        from app.services.browser_automation.license_guard import (
            AutomationError,
            assert_no_agpl,
        )

        fake = types.ModuleType("cua_som")
        sys.modules["cua_som"] = fake
        try:
            with pytest.raises(AutomationError, match="AGPL"):
                assert_no_agpl()
        finally:
            del sys.modules["cua_som"]

    def test_clean_report_names_mit_boundary(self):
        from app.services.browser_automation.license_guard import (
            verify_install_clean,
        )

        report = verify_install_clean()
        assert report["agpl_free"] is True
        assert "cua-som" in report["forbidden"]

    def test_adapter_checks_guard_on_init(self):
        from app.services.browser_automation.computer import CuaComputer
        from app.services.browser_automation.license_guard import (
            AutomationError,
        )

        sys.modules["cua_som"] = types.ModuleType("cua_som")
        try:
            with pytest.raises(AutomationError, match="AGPL"):
                CuaComputer(computer=object())
        finally:
            del sys.modules["cua_som"]


class FakeComputer:
    def __init__(self):
        self.calls = []

    async def screenshot(self):
        self.calls.append(("screenshot",))
        return b"shot"

    async def click(self, x, y):
        self.calls.append(("click", x, y))

    async def double_click(self, x, y):
        self.calls.append(("double_click", x, y))

    async def right_click(self, x, y):
        self.calls.append(("right_click", x, y))

    async def move_cursor(self, x, y):
        self.calls.append(("move_cursor", x, y))

    async def type_text(self, text):
        self.calls.append(("type_text", text))

    async def press_key(self, key):
        self.calls.append(("press_key", key))

    async def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))

    async def scroll(self, x, y, clicks=1):
        self.calls.append(("scroll", x, y, clicks))

    async def get_screen_size(self):
        return {"width": 1280, "height": 800}

    async def close(self):
        self.calls.append(("close",))


class TestRunner:
    def test_actions_execute_in_order(self):
        from app.services.browser_automation.runner import (
            ActionType,
            AutomationPlan,
            BrowserAction,
            run_plan,
        )

        driver = FakeComputer()
        plan = AutomationPlan(
            name="portal-export",
            allowed_hosts=["portal.example.com"],
            actions=[
                BrowserAction(
                    ActionType.NAVIGATE,
                    url="https://portal.example.com/login",
                    description="open portal",
                ),
                BrowserAction(
                    ActionType.CLICK, x=100, y=200, description="click export"
                ),
                BrowserAction(ActionType.SCREENSHOT, description="evidence"),
            ],
        )
        result = drive(run_plan(plan, driver, lambda action: True))
        assert result.completed is True
        assert result.steps_completed == 3
        assert [c[0] for c in driver.calls] == [
            "screenshot",  # navigate captures a screenshot
            "click",
            "screenshot",
        ]

    def test_denied_approval_halts(self):
        from app.services.browser_automation.runner import (
            ActionType,
            AutomationPlan,
            BrowserAction,
            run_plan,
        )

        driver = FakeComputer()
        plan = AutomationPlan(
            name="risky",
            allowed_hosts=[],
            actions=[
                BrowserAction(
                    ActionType.PRESS_KEY,
                    key="Enter",
                    description="submit form",
                    requires_approval=True,
                ),
                BrowserAction(ActionType.CLICK, x=1, y=1, description="should not run"),
            ],
        )
        result = drive(run_plan(plan, driver, lambda action: False))
        assert result.completed is False
        assert result.halt_reason == "denied"
        assert driver.calls == []

    def test_off_allowlist_navigation_raises(self):
        from app.services.browser_automation.runner import (
            ActionType,
            AutomationError,
            AutomationPlan,
            BrowserAction,
            run_plan,
        )

        driver = FakeComputer()
        plan = AutomationPlan(
            name="evil",
            allowed_hosts=["portal.example.com"],
            actions=[
                BrowserAction(
                    ActionType.NAVIGATE,
                    url="https://evil.example.net/steal",
                    description="off allowlist",
                ),
            ],
        )
        with pytest.raises(AutomationError, match="allowlist"):
            drive(run_plan(plan, driver, lambda action: True))

    def test_actions_are_immutable(self):
        from app.services.browser_automation.runner import (
            ActionType,
            BrowserAction,
        )

        action = BrowserAction(ActionType.CLICK, x=1, y=2, description="immutable")
        with pytest.raises(FrozenInstanceError):
            action.x = 99
