import os
from typing import Any

def get_env_float(key: str, default: float) -> float:
    """Return a float environment variable or the default.
    Any parsing error falls back to the default to avoid crashes.
    """
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return default

def get_env_int(key: str, default: int) -> int:
    """Return an int environment variable or the default.
    """
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return default

# Example usage elsewhere:
# BATTERY_24V_CRITICAL = get_env_float('BATTERY_24V_CRITICAL', 23.0)
