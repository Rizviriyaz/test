"""Server-side Supabase authentication for operational API requests."""

import os

import requests
from fastapi import HTTPException


def authenticate_access_token(token: str) -> dict:
    """Validate a bearer token with Supabase and load its database profile."""
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon_key:
        raise HTTPException(status_code=503, detail="Authentication service is not configured")

    headers = {"apikey": anon_key, "Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{supabase_url}/auth/v1/user", headers=headers, timeout=8)
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Authentication service is unavailable") from exc
    if response.status_code in (400, 401, 403):
        raise HTTPException(status_code=401, detail="Invalid or expired access token")
    if not response.ok:
        raise HTTPException(status_code=503, detail="Authentication service could not validate the token")

    try:
        user = response.json()
        user_id = user["id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Authentication service returned an invalid response") from exc

    # The profile request uses the user's own token, so Supabase RLS remains active.
    try:
        profile_response = requests.get(
            f"{supabase_url}/rest/v1/profiles",
            params={"id": f"eq.{user_id}", "select": "id,role"},
            headers={**headers, "Accept": "application/json"},
            timeout=8,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Profile service is unavailable") from exc
    if not profile_response.ok:
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
    return {"id": user_id, "email": user.get("email"), "role": role}
