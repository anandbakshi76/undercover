"""The agent's service: the agent's entry point, a small web service of its own."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent import claim, dispatcher, main

BACKEND_WAIT = 30


def stop_running_runs() -> None:
    """Mark the runs an earlier service left running as stopped.

    The backend may still be starting, so wait for it a while.
    """
    for _ in range(BACKEND_WAIT):
        try:
            main.call("POST", "/api/runs/stop-running")
            return
        except httpx.TransportError:
            time.sleep(1)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(stop_running_runs)
    yield


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)


class DispatchStart(BaseModel):
    concurrency: int = Field(ge=1, le=25)


def start(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Call the backend to start a run, and pass on its refusal."""
    with main.backend() as client:
        response = client.request(method, path, json=body)
    if response.is_error:
        raise HTTPException(response.status_code, response.json()["detail"])
    return response.json()


def work_one(run_id: str, claim_id: str) -> None:
    claim.work_claim(claim_id, run_id)
    main.call("POST", f"/api/runs/{run_id}/finish", {"status": "finished"})


def work_dispatch(run_id: str, concurrency: int) -> None:
    """Work a dispatch with the dispatcher, then record how it ended."""
    status = "finished"
    try:
        dispatcher.dispatch(run_id, concurrency)
    except Exception:
        status = "failed"
    main.call("POST", f"/api/runs/{run_id}/finish", {"status": status})


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the agent's service is running."""
    return {"status": "ok"}


@app.post("/api/dispatches", operation_id="run_dispatch")
def run_dispatch(
    dispatch: DispatchStart, background: BackgroundTasks
) -> dict[str, Any]:
    """Start a dispatch of the queue with N claim agents at once, and work it."""
    model, effort = main.model_settings()
    run = start(
        "POST",
        "/api/runs/dispatch",
        {
            "concurrency": dispatch.concurrency,
            "model": model,
            "reasoning_effort": effort,
        },
    )
    background.add_task(work_dispatch, run["run_id"], dispatch.concurrency)
    return run


@app.post("/api/claims/{claim_id}/run", operation_id="run_claim")
def run_claim(claim_id: str, background: BackgroundTasks) -> dict[str, Any]:
    """Start a run on one waiting claim, and work it."""
    run = start("POST", f"/api/claims/{claim_id}/start-run")
    background.add_task(work_one, run["run_id"], claim_id)
    return run


@app.post("/api/claims/{claim_id}/resume", operation_id="resume_claim")
def resume_claim(claim_id: str, background: BackgroundTasks) -> dict[str, str]:
    """Carry on a claim that is in progress, from where it stopped."""
    found = start("GET", f"/api/claims/{claim_id}")
    if found["status"] != "in_progress":
        raise HTTPException(409, f"Claim {claim_id} is not in progress.")
    background.add_task(claim.carry_on, claim_id)
    return {"status": "resuming"}
