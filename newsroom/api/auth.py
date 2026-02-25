"""
Shared Bearer-token authentication dependency.

Extracted from app.py into its own module to prevent circular imports
when config_router.py needs to import verify_token.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import Settings, get_settings

_bearer = HTTPBearer(auto_error=True)


def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.api_secret_key.get_secret_value()
    if credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
