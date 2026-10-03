"""Browser-automation reference layer (TW-178).

**Prepared by Twistor Holdings LLC**
"""

from app.services.browser_automation.computer import (
    AutomationError,
    Computer,
    CuaComputer,
)
from app.services.browser_automation.license_guard import (
    assert_no_agpl,
    verify_install_clean,
)
from app.services.browser_automation.runner import (
    ActionType,
    AutomationPlan,
    AutomationResult,
    BrowserAction,
    run_plan,
)

__all__ = [
    "ActionType",
    "AutomationError",
    "AutomationPlan",
    "AutomationResult",
    "BrowserAction",
    "Computer",
    "CuaComputer",
    "assert_no_agpl",
    "run_plan",
    "verify_install_clean",
]
