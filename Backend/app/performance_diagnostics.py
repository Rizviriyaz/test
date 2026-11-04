"""Temporary request-correlated timing diagnostics for fleet API investigation."""

import contextvars
import functools
import logging
import threading
import time
import uuid
from contextvars import copy_context


request_trace_id = contextvars.ContextVar("performance_request_trace_id", default="-")


# Wialon call counts of the current request ({"calls", "e1003", "e1004"}), set by the request middleware. Executor
# workers run in copies of the request's context, which share this same dict, so their calls count too.
request_wialon_stats = contextvars.ContextVar("performance_request_wialon_stats", default=None)
# Process-wide Wialon limit errors since startup (1003 api_concurrent, 1004 msgs_activity)
wialon_limit_errors = {"e1003": 0, "e1004": 0}
_stats_lock = threading.Lock()


def count_wialon_call(error_code=None) -> None:
    """Count one Wialon HTTP attempt for the current request (and limit errors process-wide)."""
    key = f"e{error_code}" if error_code in (1003, 1004) else None
    stats = request_wialon_stats.get()
    with _stats_lock:
        if key:
            wialon_limit_errors[key] += 1
        if stats is not None:
            stats["calls"] = stats.get("calls", 0) + 1
            if key:
                stats[key] = stats.get(key, 0) + 1


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def timed_calculation(endpoint: str):
    """Time synchronous endpoint calculation without logging request contents."""
    def decorate(func):
        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            logging.debug("PERF calculation start request_id=%s endpoint=%s", request_trace_id.get(), endpoint)
            try:
                return func(*args, **kwargs)
            finally:
                logging.debug(
                    "PERF calculation end request_id=%s endpoint=%s duration_ms=%.1f",
                    request_trace_id.get(), endpoint, (time.perf_counter() - started) * 1000,
                )
        return wrapped
    return decorate


def map_with_request_context(executor, function, values):
    """Preserve request correlation in executor workers used by fleet analytics."""
    def run_in_context(context_and_value):
        context, value = context_and_value
        return context.run(function, value)

    return executor.map(run_in_context, ((copy_context(), value) for value in values))


def log_cache_state(endpoint: str, hit: bool) -> None:
    logging.debug(
        "PERF analytics cache request_id=%s endpoint=%s state=%s",
        request_trace_id.get(), endpoint, "hit" if hit else "miss",
    )


def log_data_window(endpoint: str, from_time: int, to_time: int) -> None:
    logging.debug(
        "PERF data_window request_id=%s endpoint=%s from_utc=%s to_utc=%s duration_hours=%.3f",
        request_trace_id.get(), endpoint, from_time, to_time, max(0, to_time - from_time) / 3600,
    )
