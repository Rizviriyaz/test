"""Server-side Supabase authentication for operational API requests."""

import logging
import os
import time

import requests
from fastapi import HTTPException
from urllib3.util import Retry

_retry_strategy = Retry(
    total=2,
    backoff_factor=0.3,
    status_forcelist=[500, 502, 503, 504],
    raise_on_status=False
)
_auth_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=30, max_retries=_retry_strategy)
_auth_session.mount("https://", _adapter)
_auth_session.mount("http://", _adapter)

# In-memory cache for validated tokens: token -> {"user": dict, "timestamp": float}
_token_cache = {}


def authenticate_access_token(token: str) -> dict:
    """Validate a bearer token with Supabase and load its database profile."""
    now = time.time()
    cached = _token_cache.get(token)
    if cached and (now - cached["timestamp"]) < 300:
        return cached["user"]

    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon_key:
        raise HTTPException(status_code=503, detail="Authentication service is not configured")

    headers = {"apikey": anon_key, "Authorization": f"Bearer {token}"}
    try:
        response = _auth_session.get(f"{supabase_url}/auth/v1/user", headers=headers, timeout=15)
    except requests.RequestException as exc:
        if cached and (now - cached["timestamp"]) < 3600:
            logging.warning("Supabase auth transient network error; using cached user: %s", exc)
            return cached["user"]
        raise HTTPException(status_code=503, detail="Authentication service is unavailable") from exc
    if response.status_code in (400, 401, 403):
        _token_cache.pop(token, None)
        raise HTTPException(status_code=401, detail="Invalid or expired access token")
    if not response.ok:
        if cached and (now - cached["timestamp"]) < 3600:
            logging.warning("Supabase auth non-OK response (%s); using cached user", response.status_code)
            return cached["user"]
        raise HTTPException(status_code=503, detail="Authentication service could not validate the token")

    try:
        user = response.json()
        user_id = user["id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Authentication service returned an invalid response") from exc

    # The profile request uses the user's own token, so Supabase RLS remains active.
    try:
        profile_response = _auth_session.get(
            f"{supabase_url}/rest/v1/profiles",
            params={"id": f"eq.{user_id}", "select": "id,role"},
            headers={**headers, "Accept": "application/json"},
            timeout=15,
        )
    except requests.RequestException as exc:
        if cached and (now - cached["timestamp"]) < 3600:
            logging.warning("Supabase profile transient network error; using cached user: %s", exc)
            return cached["user"]
        raise HTTPException(status_code=503, detail="Profile service is unavailable") from exc
    if not profile_response.ok:
        if cached and (now - cached["timestamp"]) < 3600:
            logging.warning("Supabase profile non-OK response (%s); using cached user", profile_response.status_code)
            return cached["user"]
        raise HTTPException(status_code=503, detail="Could not load the authenticated user profile")
    try:
        profiles = profile_response.json()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Profile service returned an invalid response") from exc

    if not isinstance(profiles, list) or not profiles or not isinstance(profiles[0], dict):
        raise HTTPException(status_code=403, detail="User profile was not found")

    role = profiles[0].get("role")
    allowed_roles = {"admin", "manager", "fleet_manager", "viewer"}
    if role not in allowed_roles:
        raise HTTPException(status_code=403, detail="User profile has no permitted fleet role")

    result = {"id": user_id, "email": user.get("email"), "role": role}
    _token_cache[token] = {"user": result, "timestamp": now}
    return result
