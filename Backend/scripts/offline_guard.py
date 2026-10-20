"""Network guard for offline test scripts: any outbound HTTP via `requests` is blocked and recorded.

The blocking error subclasses BaseException so that broad `except Exception` handlers in app code
cannot swallow it and quietly turn a live call into an empty result.
"""

from unittest.mock import patch

import requests


class LiveCallBlocked(BaseException):
    """Raised when code under test attempts a real network request."""


class OfflineGuard:
    def __init__(self):
        self.attempts = []
        self._patcher = None

    def _blocked(self, session, method, url, *args, **kwargs):
        self.attempts.append(f"{method} {url}")
        raise LiveCallBlocked(f"Live network call blocked in offline test: {method} {url}")

    def start(self) -> "OfflineGuard":
        guard = self

        def fake_request(session, method, url, *args, **kwargs):
            return guard._blocked(session, method, url, *args, **kwargs)

        # requests.get/post/patch and every requests.Session (Wialon, Supabase auth) route through here.
        self._patcher = patch.object(requests.sessions.Session, "request", fake_request)
        self._patcher.start()
        return self

    def stop(self) -> None:
        if self._patcher:
            self._patcher.stop()
            self._patcher = None

    def assert_no_live_calls(self) -> None:
        assert not self.attempts, f"Offline test attempted live calls: {self.attempts}"
        print("[PASS] Network guard: zero live network calls attempted.")
