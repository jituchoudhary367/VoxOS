"""
JWT Authentication & Authorization.

Provides utility functions to issue and verify JWT tokens
for securing the WebSocket and API endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt, JWTError

from voxos.config import get_settings
from voxos.observability.logger import get_logger

log = get_logger(__name__)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create a new JWT access token."""
    settings = get_settings().security
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
        
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, 
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm
    )
    return encoded_jwt


def verify_token(token: str) -> dict[str, Any] | None:
    """Verify a JWT token and return its payload."""
    settings = get_settings().security
    try:
        payload = jwt.decode(
            token, 
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError as e:
        log.warning("token_verification_failed", error=str(e))
        return None
