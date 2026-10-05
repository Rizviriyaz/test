import os


def _positive_int_from_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


CONN_DELAYED_SECONDS = _positive_int_from_env("CONN_DELAYED_SECONDS", 3600)
