"""The agent's service: the agent's entry point, a small web service of its own."""

from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from agent import claim, main

app = FastAPI(title="UnderCover Agent")


def start(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Call the backend to start a run, and pass on its refusal."""
    with main.backend() as client:
        response = client.request(method, path, json=body)
    if response.is_error:
        raise HTTPException(response.status_code, response.json()["detail"])
    return response.json()


def work_one(run_id: str, claim_id: str) -> None:
    claim.work_claim(claim_id, run_id)
    main.call(
        "POST", f"/api/runs/{run_id}/finish", {"status": "finished", "summary": ""}
    )


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the agent's service is running."""
    return {"status": "ok"}


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
