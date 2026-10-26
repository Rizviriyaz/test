"""
Per-user sliding-window rate limits for expensive API routes (AI chat, raw-history fan-outs, forced report refreshes).

LIMITATION: the counters live in this process's memory. They protect one backend process only: several workers or
serverless instances each keep their own counters, and a restart resets them. They bound accidental or abusive load
from one user on one process; they are not a global quota.
"""

import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Dict, Optional

from fastapi import HTTPException, Request

from app.utils.config import get_env_int

# Distinct (bucket, user) keys kept; the least recently used key is dropped beyond this
MAX_TRACKED_KEYS = 4096


class SlidingWindowLimiter:
    """At most `limit` calls per `window_seconds` for each key; check() records a call or returns the wait in seconds."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = MAX_TRACKED_KEYS):
        self._clock = clock
        self._max_keys = max_keys
        self._calls: "OrderedDict[str, deque]" = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float) -> Optional[float]:
        """None when the call is allowed (and recorded), else the seconds until the oldest call leaves the window."""
        if limit <= 0:
            return None  # a limit of 0 disables the check
        now = self._clock()
        with self._lock:
            calls = self._calls.get(key)
            if calls is None:
                calls = self._calls[key] = deque()
            self._calls.move_to_end(key)
            while calls and now - calls[0] >= window_seconds:
                calls.popleft()
            if len(calls) >= limit:
                return max(0.0, window_seconds - (now - calls[0]))
            calls.append(now)
            while len(self._calls) > self._max_keys:
                self._calls.popitem(last=False)
            return None

    def clear(self) -> None:
        with self._lock:
            self._calls.clear()


_limiter = SlidingWindowLimiter()

# bucket -> (env var with the per-minute limit, default)
BUCKETS: Dict[str, tuple] = {
    "ai_chat": ("AI_CHAT_RATE_LIMIT_PER_MINUTE", 10),
    "heavy": ("HEAVY_ENDPOINT_RATE_LIMIT_PER_MINUTE", 12),
}
WINDOW_SECONDS = 60


def enforce_rate_limit(request: Optional[Request], bucket: str) -> None:
    """
    Raise HTTP 429 (with Retry-After) when the authenticated user exceeded the bucket's per-minute limit.
    `request` is None when a route function is called internally (another service, the AI tools): not limited.
    """
    if request is None:
        return
    env_name, default = BUCKETS[bucket]
    limit = get_env_int(env_name, default)
    user = getattr(request.state, "user", None)
    user_id = user.get("id") if isinstance(user, dict) else None
    key = f"{bucket}:{user_id or (request.client.host if request.client else 'anonymous')}"
    wait = _limiter.check(key, limit, WINDOW_SECONDS)
    if wait is not None:
        retry_after = max(1, int(wait + 0.999))
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests for this resource; try again in {retry_after} s.",
            headers={"Retry-After": str(retry_after)},
        )


def reset_rate_limits() -> None:
    """Forget every counter (tests)."""
    _limiter.clear()
