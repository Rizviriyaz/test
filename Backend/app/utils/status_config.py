import os


def _positive_int_from_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


# Default offline threshold: 12 hours (12 * 3600 = 43200 seconds)
CONN_DELAYED_SECONDS = _positive_int_from_env("CONN_DELAYED_SECONDS", 43200)

