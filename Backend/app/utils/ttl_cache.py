"""Small thread-safe TTL cache with per-key single-flight computation, and retention-free in-flight sharing."""

import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from contextlib import contextmanager
from typing import Any, Callable, Dict, Hashable, Optional, Tuple


class InFlight:
    """
    Concurrent calls for the same key share one running computation and its result (or exception).
    Nothing is retained once it finishes, so a later call always computes afresh: no staleness window.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._calls: Dict[Hashable, Future] = {}

    def run(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        with self._lock:
            future = self._calls.get(key)
            owner = future is None
            if owner:
                future = self._calls[key] = Future()
        if not owner:
            return future.result()
        try:
            value = compute()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(value)
            return value
        finally:
            with self._lock:
                if self._calls.get(key) is future:
                    del self._calls[key]


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

    @contextmanager
    def _key_state(self, key: Hashable):
        """Hold key's single-flight lock; the per-key state is dropped once no caller is waiting."""
        with self._lock:
            state = self._states.setdefault(key, _KeyState())
            state.waiters += 1
        try:
            with state.lock:
                yield state
        finally:
            with self._lock:
                state.waiters -= 1
                if state.waiters == 0 and self._states.get(key) is state:
                    del self._states[key]

    def _compute_and_set(self, state: _KeyState, key: Hashable, compute: Callable[[], Any],
                         ttl_for: Callable[[Any], float]) -> Any:
        try:
            value = compute()
        except BaseException as exc:
            state.error, state.error_at = exc, self._now()
            raise
        state.error = None
        self.set(key, value, ttl_for(value))
        return value

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
        with self._key_state(key) as state:
            hit = self.get(key)
            if hit is not None:
                return hit
            if state.error is not None and self._now() - state.error_at < self.error_share_seconds:
                raise state.error
            return self._compute_and_set(state, key, compute, ttl_for)

    def refresh(
        self,
        key: Hashable,
        compute: Callable[[], Any],
        ttl_for: Callable[[Any], float],
    ) -> Any:
        """
        Recompute key now and cache the result for ttl_for(value) seconds, keeping the current entry served until it
        is replaced. It runs under the key's single-flight lock, so a get_or_compute miss meanwhile (the entry expired
        during the refresh) waits for this result instead of computing its own. A failure is not cached: the current
        entry stays until its own expiry, and callers already queued receive the same exception, as in get_or_compute.
        """
        with self._key_state(key) as state:
            return self._compute_and_set(state, key, compute, ttl_for)


class FailureBackoff:
    """
    Bounded per-key backoff after consecutive failures: a key that failed is not retried for base_seconds, doubling
    per consecutive failure up to max_seconds; a success clears it. State is kept no longer than retention_seconds.
    """

    def __init__(self, base_seconds: float = 15, max_seconds: float = 120, max_entries: int = 128,
                 retention_seconds: float = 600, clock: Callable[[], float] = time.time):
        self._base = base_seconds
        self._max = max_seconds
        self._retention = retention_seconds
        self._clock = clock
        self._state = TTLCache(max_entries=max_entries)
        self._lock = threading.Lock()

    def active(self, key: Hashable) -> bool:
        state = self._state.get(key)
        return state is not None and self._clock() < state[1]

    def record_failure(self, key: Hashable) -> float:
        """Register a failure; returns the seconds until the key may be retried."""
        with self._lock:
            failures = (self._state.get(key) or (0, 0))[0] + 1
            delay = min(self._max, self._base * 2 ** (failures - 1))
            self._state.set(key, (failures, self._clock() + delay), self._retention)
            return delay

    def record_success(self, key: Hashable) -> None:
        with self._lock:
            self._state.pop(key)


class CategorizedTTLCache:
    """
    Several bounded TTLCaches behind one TTLCache-like interface, chosen per key by `category_of(key)`: a category
    whose keys multiply (e.g. one per vehicle and minute) evicts only its own entries, never another category's.
    Keys never collide across categories because each key maps to exactly one category.
    """

    def __init__(self, category_of: Callable[[Hashable], str], max_entries: Dict[str, int]):
        self._category_of = category_of
        self._caches = {name: TTLCache(max_entries=size) for name, size in max_entries.items()}

    def _for(self, key: Hashable) -> "TTLCache":
        return self._caches[self._category_of(key)]

    def get(self, key: Hashable) -> Optional[Any]:
        return self._for(key).get(key)

    def set(self, key: Hashable, value: Any, ttl_seconds: float) -> None:
        self._for(key).set(key, value, ttl_seconds)

    def pop(self, key: Hashable) -> None:
        self._for(key).pop(key)

    def clear(self) -> None:
        for cache in self._caches.values():
            cache.clear()

    def _now(self) -> float:
        return next(iter(self._caches.values()))._now()

    def size(self, category: str) -> int:
        return len(self._caches[category]._data)

    @property
    def _data(self) -> Dict[Hashable, Any]:
        """Every entry of every category (a merged view; the entry objects are shared, not copied)."""
        merged: Dict[Hashable, Any] = {}
        for cache in self._caches.values():
            merged.update(cache._data)
        return merged
