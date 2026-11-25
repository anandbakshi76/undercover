"""The agent's service: the agent's entry point, a small web service of its own."""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent import claim, dispatcher, improver, main

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


class EvaluationStart(BaseModel):
    concurrency: int = Field(ge=1, le=25)
    evaluation_set: Literal["evaluation", "held_out"]
    tiny: bool


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
    configuration = start("GET", "/api/configurations/current")
    model, effort = main.model_settings(configuration)
    run = start(
        "POST",
        "/api/runs/dispatch",
        {
            "concurrency": dispatch.concurrency,
            "version": configuration["version"],
            "model": model,
            "reasoning_effort": effort,
        },
    )
    background.add_task(work_dispatch, run["run_id"], dispatch.concurrency)
    return run


@app.post("/api/evaluations", operation_id="run_evaluation")
def run_evaluation(
    evaluation: EvaluationStart, background: BackgroundTasks
) -> dict[str, Any]:
    """Start an evaluation of a labelled set with N claim agents, and work it."""
    configuration = start("GET", "/api/configurations/current")
    model, effort = main.model_settings(configuration)
    run = start(
        "POST",
        "/api/runs/evaluation",
        evaluation.model_dump()
        | {
            "version": configuration["version"],
            "round_id": None,
            "model": model,
            "reasoning_effort": effort,
        },
    )
    background.add_task(work_dispatch, run["run_id"], evaluation.concurrency)
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


class Decision(BaseModel):
    handler_id: str
    round_id: str
    reason: str


class RoundStart(BaseModel):
    started_by: str


def work_round(round_id: str, stretch_id: str, command: Command | None) -> None:
    """Work a stretch of a round, then let go of the one-run rule."""
    status = "finished"
    try:
        improver.drive_round(round_id, command)
    except Exception:
        status = "failed"
    main.call("POST", f"/api/runs/{stretch_id}/finish", {"status": status})


@app.post("/api/rounds", operation_id="run_round")
def run_round(round_start: RoundStart, background: BackgroundTasks) -> dict[str, Any]:
    """Start a round of the improver, and work it."""
    if os.getenv("MODEL_NAME"):
        raise HTTPException(409, "Rounds aren't run on a free model.")
    created = start("POST", "/api/rounds/create", round_start.model_dump())
    background.add_task(work_round, created["round_id"], created["stretch_id"], None)
    return created


def decide(
    proposal_id: int, decision: Decision, background: BackgroundTasks, kind: str
):
    """Take a stretch, record a handler's decision, and resume the improver."""
    stretch = start("POST", f"/api/rounds/{decision.round_id}/stretches")
    body = {
        "decision": kind,
        "handler_id": decision.handler_id,
        "reason": decision.reason,
    }
    try:
        recorded = start("POST", f"/api/proposals/{proposal_id}/decision", body)
    except HTTPException:
        main.call(
            "POST", f"/api/runs/{stretch['stretch_id']}/finish", {"status": "finished"}
        )
        raise
    answer = {"type": "approve"}
    if kind == "reject":
        answer = {"type": "reject", "message": decision.reason}
    resume = Command(resume={"decisions": [answer]})
    background.add_task(work_round, decision.round_id, stretch["stretch_id"], resume)
    return recorded


@app.post("/api/proposals/{proposal_id}/approve", operation_id="approve_proposal")
def approve_proposal(
    proposal_id: int, decision: Decision, background: BackgroundTasks
) -> dict[str, Any]:
    """Approve a proposal for a handler, and carry the improver on."""
    return decide(proposal_id, decision, background, "approve")


@app.post("/api/proposals/{proposal_id}/reject", operation_id="reject_proposal")
def reject_proposal(
    proposal_id: int, decision: Decision, background: BackgroundTasks
) -> dict[str, Any]:
    """Reject a proposal for a handler, with their reason for the improver."""
    return decide(proposal_id, decision, background, "reject")


@app.post("/api/rounds/{round_id}/stop", operation_id="stop_round")
def stop_round(round_id: str) -> dict[str, str]:
    """Note that a round was stopped. The backend refuses what it records."""
    return {"status": "stopped", "round_id": round_id}
