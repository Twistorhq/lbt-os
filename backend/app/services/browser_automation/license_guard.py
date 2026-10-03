"""AGPL boundary guard for the cua reference layer (TW-178).

cua itself is MIT (verified live 2026-09-23), but its optional ``omni``
extra pulls ``cua-som`` (PyPI: AGPL-3.0-or-later), which Twistor forbids.
This module fails loud if that package ever becomes importable or
installed, so a stray ``pip install cua-agent[omni]`` can never silently
enter the fleet.

**Prepared by Twistor Holdings LLC**
"""

import importlib.metadata
import importlib.util
import sys

from app.services.browser_automation.computer import AutomationError

FORBIDDEN_MODULES = ("cua_som",)
FORBIDDEN_DISTRIBUTIONS = ("cua-som",)


def _module_present(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # A sys.modules entry with __spec__=None still means "present".
        return name in sys.modules


def _distribution_installed(name: str) -> bool:
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def assert_no_agpl() -> None:
    """Raise AutomationError if any AGPL-forbidden package is present."""
    found = [m for m in FORBIDDEN_MODULES if _module_present(m)]
    found += [d for d in FORBIDDEN_DISTRIBUTIONS if _distribution_installed(d)]
    if found:
        raise AutomationError(
            "Refusing to run: forbidden AGPL-licensed package(s) detected: "
            + ", ".join(sorted(set(found)))
            + ". Twistor only uses the MIT-licensed cua packages; the "
            "'omni' extra (cua-som) is never allowed."
        )


def verify_install_clean() -> dict:
    """Return a license-boundary report for ticket receipts."""
    return {
        "agpl_free": not any(_module_present(m) for m in FORBIDDEN_MODULES)
        and not any(_distribution_installed(d) for d in FORBIDDEN_DISTRIBUTIONS),
        "forbidden": sorted(FORBIDDEN_DISTRIBUTIONS),
        "checked_modules": sorted(FORBIDDEN_MODULES),
    }
