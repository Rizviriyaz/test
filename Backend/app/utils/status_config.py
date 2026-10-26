import os


def _positive_int_from_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


# Telemetry older than this is DELAYED (default 10 minutes); live and health views share it
CONN_STABLE_SECONDS = _positive_int_from_env("CONN_STABLE_SECONDS", 600)

# Default offline threshold: 12 hours (12 * 3600 = 43200 seconds)
CONN_DELAYED_SECONDS = _positive_int_from_env("CONN_DELAYED_SECONDS", 43200)

