"""A handler's sign-in: a Supabase Auth token, checked against the public keys."""

import json
import os
from functools import cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from backend import db
from backend.db import records

AUDIENCE = "authenticated"
bearer = HTTPBearer(auto_error=False)


def make_keys() -> jwt.PyJWKClient:
    """Make the client for the project's public keys."""
    url = os.getenv("VITE_SUPABASE_URL")
    if not url:
        raise HTTPException(503, "Sign-in isn't set up: VITE_SUPABASE_URL is missing.")
    return jwt.PyJWKClient(f"{url.rstrip('/')}/auth/v1/.well-known/jwks.json")


keys = cache(make_keys)


def email_of(token: str) -> str:
    """Check a token's signature, expiry and audience, and return its email."""
    try:
        key = keys().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=[key.algorithm_name], audience=AUDIENCE
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        raise HTTPException(401, "Sign in to do this.") from None
    return str(claims.get("email", ""))


def current_handler(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    """Find the signed-in handler from the access token."""
    if credentials is None:
        raise HTTPException(401, "Sign in to do this.")
    email = email_of(credentials.credentials).lower()
    with db.connect() as connection:
        rows = (
            connection.execute(
                select(records.c.data).where(records.c.kind == "handler")
            )
            .scalars()
            .all()
        )
    for row in rows:
        handler = json.loads(row)
        if handler["email"].lower() == email:
            return handler
    raise HTTPException(401, "Sign in as a claims handler to do this.")


def senior_handler(
    handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> dict[str, Any]:
    """Find the signed-in handler, who must be able to sign off over the limit."""
    if not handler["can_approve_over_limit"]:
        raise HTTPException(
            403,
            f"{handler['name']} can't do this. Only a handler who can sign off "
            "payouts over the limit may.",
        )
    return handler


SignedIn = Annotated[dict[str, Any], Depends(current_handler)]
Senior = Annotated[dict[str, Any], Depends(senior_handler)]
SIGNED_IN = Depends(current_handler)
SENIOR = Depends(senior_handler)
