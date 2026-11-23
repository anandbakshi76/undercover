"""Asking the agent's service to start work, and telling it about a sign-off."""

import logging
import os
from typing import Any

import httpx
from fastapi import HTTPException

LOG = logging.getLogger("uvicorn.error")


def client() -> httpx.Client:
    """Return a client for the agent's service."""
    url = os.getenv("UNDERCOVER_AGENT") or "http://127.0.0.1:8002"
    return httpx.Client(base_url=url, timeout=30)


def forward(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pass a request on to the agent's service, or say that it isn't running."""
    try:
        with client() as service:
            response = service.post(path, json=body)
    except httpx.TransportError:
        raise HTTPException(503, "The agent isn't running.") from None
    if response.is_error:
        raise HTTPException(response.status_code, response.json()["detail"])
    return response.json()


def tell(claim_id: str) -> None:
    """Tell the agent's service that a claim is signed off, if it can be reached."""
    try:
        with client() as service:
            service.post(f"/api/claims/{claim_id}/resume")
    except httpx.HTTPError as error:
        LOG.warning(
            "The agent was not told about the sign-off on %s: %s", claim_id, error
        )
