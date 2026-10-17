"""Shared HTTP plumbing for adapters: sessions, User-Agent, rate limiting.

Prepared by Twistor Holdings LLC.
"""

from __future__ import annotations

import requests

from ..politeness import DEFAULT_USER_AGENT, RateLimiter, is_fetch_allowed

_limiters: dict[str, RateLimiter] = {}


def limiter_for(host: str, interval_s: float = 2.0) -> RateLimiter:
    if host not in _limiters:
        _limiters[host] = RateLimiter(interval_s)
    return _limiters[host]


def make_session(user_agent: str = DEFAULT_USER_AGENT) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent})
    return s


def fetch_pdf_bytes(
    url: str, session: requests.Session, max_bytes: int = 20_000_000
) -> bytes | None:
    """Best-effort PDF fetch honoring robots.txt. None when not allowed."""
    if not url or not is_fetch_allowed(url):
        return None
    try:
        with session.get(url, stream=True, timeout=30) as resp:
            if resp.status_code != 200:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "pdf" not in ctype.lower():
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(65536):
                total += len(chunk)
                if total > max_bytes:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except Exception:
        return None
