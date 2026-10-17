"""RED: politeness primitives — rate limiting and robots.txt handling."""
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from research.harvester.politeness import RateLimiter, is_fetch_allowed


class TestRateLimiter:
    def test_first_call_allowed(self):
        rl = RateLimiter(min_interval_s=60)
        assert rl.acquire("example.com") is True

    def test_immediate_second_call_blocked(self):
        rl = RateLimiter(min_interval_s=60)
        rl.acquire("example.com")
        assert rl.acquire("example.com") is False

    def test_call_allowed_after_interval(self):
        rl = RateLimiter(min_interval_s=0.05)
        rl.acquire("example.com")
        time.sleep(0.06)
        assert rl.acquire("example.com") is True

    def test_hosts_are_independent(self):
        rl = RateLimiter(min_interval_s=60)
        rl.acquire("a.example.com")
        assert rl.acquire("b.example.com") is True

    def test_wait_time_reported(self):
        rl = RateLimiter(min_interval_s=60)
        rl.acquire("example.com")
        assert rl.wait_s("example.com") > 0
        assert rl.wait_s("fresh.example.com") == 0

    def test_thread_safe(self):
        rl = RateLimiter(min_interval_s=0.01)
        results = []
        def worker():
            for _ in range(20):
                results.append(rl.acquire("t.example.com"))
                time.sleep(0.002)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # at most a handful should have been allowed given the interval
        assert sum(results) < len(results)


class RobotsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body = b"User-agent: *\nDisallow: /private/\n"
        else:
            body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def robots_server():
    server = HTTPServer(("127.0.0.1", 0), RobotsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


class TestRobots:
    def test_allowed_path(self, robots_server):
        assert is_fetch_allowed(robots_server + "/papers/a.pdf",
                                user_agent="TestBot/1.0") is True

    def test_disallowed_path(self, robots_server):
        assert is_fetch_allowed(robots_server + "/private/b.pdf",
                                user_agent="TestBot/1.0") is False

    def test_unreachable_robots_means_allowed(self):
        # No server on this port: fail-open would hammer; fail-closed is
        # wrong too for dead hosts. Spec: treat fetch error as NOT allowed
        # so the harvester skips rather than guesses.
        assert is_fetch_allowed("http://127.0.0.1:9/nope.pdf",
                                user_agent="TestBot/1.0") is False
