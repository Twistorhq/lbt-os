"""Politeness primitives for the harvester: rate limiting and robots.txt.

Prepared by Twistor Holdings LLC.
"""
from __future__ import annotations

import threading
import time
import urllib.request
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

DEFAULT_USER_AGENT = "TwistorResearchHarvester/0.1 (mailto:holla@twistor.co)"


class RateLimiter:
    """Per-host minimum-interval gate. Thread-safe."""

    def __init__(self, min_interval_s: float = 2.0):
        self.min_interval_s = float(min_interval_s)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, host: str) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(host, 0.0)
            if now - last >= self.min_interval_s:
                self._last[host] = now
                return True
            return False

    def wait_s(self, host: str) -> float:
        with self._lock:
            last = self._last.get(host, 0.0)
        remaining = self.min_interval_s - (time.monotonic() - last)
        return max(0.0, remaining)


def is_fetch_allowed(url: str, user_agent: str = DEFAULT_USER_AGENT,
                     timeout_s: int = 10) -> bool:
    """Check robots.txt before fetching. Fail-closed: if robots.txt cannot
    be retrieved, the fetch is NOT allowed (skip rather than guess)."""
    try:
        parts = urlparse(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        req = urllib.request.Request(robots_url,
                                     headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return False
    rp = RobotFileParser()
    rp.parse(body)
    try:
        return bool(rp.can_fetch(user_agent, url))
    except Exception:
        return False
