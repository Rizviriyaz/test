"""Small thread-safe TTL cache with per-key single-flight computation."""

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Hashable, Optional, Tuple


class _KeyState:
    __slots__ = ("lock", "waiters", "error", "error_at")

    def __init__(self):
        self.lock = threading.Lock()
        self.waiters = 0
        self.error: Optional[BaseException] = None
        self.error_at = 0.0


class TTLCache:
    """
    Bounded in-process cache. `get_or_compute` runs `compute` at most once per key at a time:
    concurrent callers for the same cold key wait for the first caller and share its result.
    A failure is never cached as a value; callers already queued behind that computation receive the
    same exception (if within `error_share_seconds`) instead of each repeating a slow failing
    computation. Per-key state is dropped once no caller is waiting, so the lock map stays bounded.
    """

    def __init__(self, max_entries: int = 128, clock: Callable[[], float] = time.monotonic,
                 error_share_seconds: float = 5.0):
        self.max_entries = max_entries
        self.error_share_seconds = error_share_seconds
        self._clock = clock
        self._data: "OrderedDict[Hashable, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self._states: Dict[Hashable, _KeyState] = {}

    def _now(self) -> float:
        return self._clock()

    def get(self, key: Hashable) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._now() >= expires_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: Hashable, value: Any, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._data[key] = (self._now() + ttl_seconds, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def pop(self, key: Hashable) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def get_or_compute(
        self,
        key: Hashable,
        compute: Callable[[], Any],
        ttl_for: Callable[[Any], float],
    ) -> Any:
        """Return the cached value for key, or compute it once and cache it for ttl_for(value) seconds."""
        hit = self.get(key)
        if hit is not None:
            return hit
        with self._lock:
            state = self._states.setdefault(key, _KeyState())
            state.waiters += 1
        try:
            with state.lock:
                hit = self.get(key)
                if hit is not None:
                    return hit
                if state.error is not None and self._now() - state.error_at < self.error_share_seconds:
                    raise state.error
                try:
                    value = compute()
                except BaseException as exc:
                    state.error, state.error_at = exc, self._now()
                    raise
                state.error = None
                self.set(key, value, ttl_for(value))
                return value
        finally:
            with self._lock:
                state.waiters -= 1
                if state.waiters == 0 and self._states.get(key) is state:
                    del self._states[key]
