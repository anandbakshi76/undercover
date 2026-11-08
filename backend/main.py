"""The UnderCover backend."""

import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent import claim as claim_agent
from backend import db, documents

INSTRUCTIONS = Path(__file__).resolve().parent / "instructions"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init()
    recover()
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


def record(connection: sqlite3.Connection, kind: str, record_id: str) -> Any:
    row = connection.execute(
        "SELECT data FROM records WHERE kind = ? AND id = ?", (kind, record_id)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"There is no {kind} {record_id}.")
    return json.loads(row["data"])


def claim_row(connection: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM claims WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"There is no claim {claim_id}.")
    return row


def claim_view(row: sqlite3.Row) -> dict[str, Any]:
    decision = None
    if row["decision"] is not None:
        decision = {
            "decision": row["decision"],
            "payout": row["payout"],
            "reason": row["reason"],
            "policy_section": row["policy_section"],
            "decided_at": row["decided_at"],
        }
    sign_off = None
    if row["signed_off_by"] is not None:
        sign_off = {
            "handler_id": row["signed_off_by"],
            "signed_off_at": row["signed_off_at"],
        }
    payment = None
    if row["paid_amount"] is not None:
        payment = {"amount": row["paid_amount"], "paid_at": row["paid_at"]}
    return {
        **json.loads(row["data"]),
        "status": row["status"],
        "decision": decision,
        "sign_off": sign_off,
        "payment": payment,
        "attempts": row["attempts"],
    }


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def list_claims() -> list[dict[str, Any]]:
    """List the claims queue with where each claim stands."""
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM claims ORDER BY claim_id").fetchall()
    keys = ["claim_id", "claim_type", "summary", "amount_claimed", "submitted_on"]
    claims = []
    for row in rows:
        data = json.loads(row["data"])
        claims.append(
            {key: data[key] for key in keys}
            | {key: row[key] for key in ["status", "decision", "payout"]}
        )
    return claims


@app.get("/api/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str) -> dict[str, Any]:
    """Get one claim in full, with its decision, sign-off and payment."""
    with db.connect() as connection:
        return claim_view(claim_row(connection, claim_id))


@app.get("/api/policies/{policy_id}", operation_id="get_policy")
def get_policy(policy_id: str) -> dict[str, Any]:
    """Get a policy with its plan's limits and its add-ons."""
    with db.connect() as connection:
        policy = record(connection, "policy", policy_id)
        plans = record(connection, "settings", "plans")
    plan = next(plan for plan in plans["plans"] if plan["plan"] == policy["plan"])
    add_ons = [a for a in plans["add_ons"] if a["add_on"] in policy["add_ons"]]
    return policy | {
        "plan_details": plan,
        "deductible_applies_to": plans["deductible_applies_to"],
        "add_on_details": add_ons,
    }


@app.get("/api/customers/{customer_id}/claims", operation_id="get_claim_history")
def get_claim_history(customer_id: str) -> dict[str, Any]:
    """Get a customer's earlier claims, without their personal details."""
    with db.connect() as connection:
        customer = record(connection, "customer", customer_id)
    return {
        "customer_id": customer_id,
        "customer_since": customer["customer_since"],
        "previous_claims": customer["previous_claims"],
    }


def document_text(connection: sqlite3.Connection, document: str) -> str:
    if document not in documents.DOCUMENTS:
        raise HTTPException(404, f"There is no document {document}.")
    return record(connection, "settings", document)


@app.get("/api/documents/{document}/sections", operation_id="list_sections")
def list_sections(document: str) -> dict[str, Any]:
    """List the numbered sections of the policy wording or the claims manual."""
    with db.connect() as connection:
        return documents.contents(document_text(connection, document))


@app.get("/api/documents/{document}/sections/{section}", operation_id="read_section")
def read_section(document: str, section: str) -> dict[str, str]:
    """Read one section of the policy wording or the claims manual."""
    with db.connect() as connection:
        found = documents.find(document_text(connection, document), section)
    if found is None:
        raise HTTPException(404, f"There is no section {section} in {document}.")
    return {"document": document, "name": documents.DOCUMENTS[document]} | found


@app.get("/api/search/{text}", operation_id="search_documents")
def search_documents(text: str) -> list[dict[str, str]]:
    """Find the sections of the wording and the manual that mention a phrase."""
    with db.connect() as connection:
        return [
            match
            for document in documents.DOCUMENTS
            for match in documents.search(
                document, document_text(connection, document), text
            )
        ]


@app.get("/api/instructions", operation_id="get_instructions")
def get_instructions() -> dict[str, str]:
    """Get the UnderCover Agent's instructions for working a claim."""
    return {"claim": (INSTRUCTIONS / "claim.md").read_text(encoding="utf-8")}


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers() -> list[dict[str, Any]]:
    """List the claims handlers."""
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT data FROM records WHERE kind = 'handler' ORDER BY id"
        ).fetchall()
    return [json.loads(row["data"]) for row in rows]


class Step(BaseModel):
    kind: Literal["tool_call", "tool_result", "section", "reply", "error"]
    text: str


class Decision(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float = Field(ge=0)
    reason: str = Field(min_length=1)
    policy_section: str = Field(min_length=1)


class Payment(BaseModel):
    amount: float = Field(gt=0)


class SignOff(BaseModel):
    handler_id: str


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def approval_limit(connection: sqlite3.Connection) -> float:
    return record(connection, "settings", "company")["payout_approval_limit"]


def add_step_row(connection: sqlite3.Connection, claim_id: str, step: Step) -> None:
    connection.execute(
        "INSERT INTO steps (claim_id, kind, text, created_at) VALUES (?, ?, ?, ?)",
        (claim_id, step.kind, step.text, now()),
    )


def pay(connection: sqlite3.Connection, row: sqlite3.Row, amount: float) -> None:
    if row["decision"] != "approve":
        raise HTTPException(409, "Only an approved claim can be paid.")
    if row["paid_amount"] is not None:
        raise HTTPException(409, "This claim has already been paid.")
    if round(amount, 2) != round(row["payout"], 2):
        raise HTTPException(
            409, f"The amount must equal the approved payout of {money(row['payout'])}."
        )
    limit = approval_limit(connection)
    if amount > limit and row["signed_off_by"] is None:
        raise HTTPException(
            409,
            f"A payout over {money(limit)} needs a handler's sign-off "
            "before it can be paid.",
        )
    connection.execute(
        "UPDATE claims SET paid_amount = ?, paid_at = ? WHERE claim_id = ?",
        (round(amount, 2), now(), row["claim_id"]),
    )


@app.post("/api/claims/{claim_id}/steps", operation_id="add_step")
def add_step(claim_id: str, step: Step) -> dict[str, str]:
    """Record one step of the agent's work on a claim."""
    with db.connect() as connection:
        claim_row(connection, claim_id)
        add_step_row(connection, claim_id, step)
    return {"status": "recorded"}


@app.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
def list_steps(claim_id: str) -> list[dict[str, Any]]:
    """List every step of the agent's work on a claim, in order."""
    with db.connect() as connection:
        claim_row(connection, claim_id)
        rows = connection.execute(
            "SELECT id, kind, text, created_at FROM steps WHERE claim_id = ? "
            "ORDER BY id",
            (claim_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def record_decision(claim_id: str, decision: Decision) -> dict[str, Any]:
    """Approve, decline or refer a claim, with the payout and the reason."""
    if decision.decision != "approve" and decision.payout != 0:
        raise HTTPException(422, "A decline or a refer has a payout of 0.")
    with db.connect() as connection:
        row = claim_row(connection, claim_id)
        if row["decision"] is not None:
            raise HTTPException(409, f"Claim {claim_id} has already been decided.")
        over_limit = (
            decision.decision == "approve"
            and decision.payout > approval_limit(connection)
        )
        connection.execute(
            "UPDATE claims SET status = ?, decision = ?, payout = ?, reason = ?, "
            "policy_section = ?, decided_at = ? WHERE claim_id = ?",
            (
                "awaiting_sign_off" if over_limit else "decided",
                decision.decision,
                round(decision.payout, 2),
                decision.reason,
                decision.policy_section,
                now(),
                claim_id,
            ),
        )
        return claim_view(claim_row(connection, claim_id))


@app.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
def pay_claim(claim_id: str, payment: Payment) -> dict[str, Any]:
    """Pay an approved claim the given amount."""
    with db.connect() as connection:
        pay(connection, claim_row(connection, claim_id), payment.amount)
        return claim_view(claim_row(connection, claim_id))


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off")
def sign_off(claim_id: str, sign_off: SignOff) -> dict[str, Any]:
    """Sign off an approval over the limit as a handler, which pays the claim."""
    with db.connect() as connection:
        handler = record(connection, "handler", sign_off.handler_id)
        if not handler["can_approve_over_limit"]:
            raise HTTPException(403, f"{handler['name']} cannot sign off this claim.")
        row = claim_row(connection, claim_id)
        if row["status"] != "awaiting_sign_off":
            raise HTTPException(409, f"Claim {claim_id} is not waiting for sign-off.")
        connection.execute(
            "UPDATE claims SET status = 'decided', signed_off_by = ?, "
            "signed_off_at = ? WHERE claim_id = ?",
            (sign_off.handler_id, now(), claim_id),
        )
        row = claim_row(connection, claim_id)
        pay(connection, row, row["payout"])
        return claim_view(claim_row(connection, claim_id))


MAX_ATTEMPTS = 2
RESTARTED = "The backend restarted during the run."

TodoStatus = Literal["to_do", "in_progress", "done", "left_for_handler"]


class Todo(BaseModel):
    content: str = Field(min_length=1)
    status: TodoStatus


class Todos(BaseModel):
    todos: list[Todo]


class RunLimit(BaseModel):
    limit: int = Field(ge=1)


class RunEnd(BaseModel):
    status: Literal["finished", "failed"]
    summary: str


class ClaimStart(BaseModel):
    run_id: str


class Release(BaseModel):
    problem: str = Field(min_length=1)


def run_row(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"There is no run {run_id}.")
    return row


def waiting_ids(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT claim_id FROM claims WHERE status = 'waiting' ORDER BY claim_id"
    ).fetchall()
    return [row["claim_id"] for row in rows]


def worked_ids(connection: sqlite3.Connection, run_id: str) -> list[str]:
    rows = connection.execute(
        "SELECT claim_id FROM run_claims WHERE run_id = ? ORDER BY rowid", (run_id,)
    ).fetchall()
    return [row["claim_id"] for row in rows]


def run_view(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = run_row(connection, run_id)
    worked = worked_ids(connection, run_id)
    waiting = waiting_ids(connection)
    claims_left = max(row["claim_limit"] - len(worked), 0)
    retries = [claim_id for claim_id in worked if claim_id in waiting]
    others = [claim_id for claim_id in waiting if claim_id not in worked]
    return {
        "run_id": row["run_id"],
        "kind": row["kind"],
        "claim_limit": row["claim_limit"],
        "status": row["status"],
        "todos": json.loads(row["todos"]),
        "summary": row["summary"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "claims_worked": worked,
        "claims_left": claims_left,
        "waiting_in_reach": retries + (others if claims_left else []),
    }


def new_run(connection: sqlite3.Connection, kind: str, limit: int) -> str:
    run_id = f"RUN-{uuid4().hex[:8]}"
    connection.execute(
        "INSERT INTO runs (run_id, kind, claim_limit, status, started_at) "
        "VALUES (?, ?, ?, 'running', ?)",
        (run_id, kind, limit, now()),
    )
    return run_id


def start(connection: sqlite3.Connection, run_id: str, claim_id: str) -> None:
    run = run_row(connection, run_id)
    if run["status"] != "running":
        raise HTTPException(409, f"Run {run_id} has finished.")
    row = claim_row(connection, claim_id)
    if row["status"] == "needs_handler":
        raise HTTPException(409, f"Claim {claim_id} is left for a handler.")
    worked = worked_ids(connection, run_id)
    if claim_id not in worked and len(worked) >= run["claim_limit"]:
        raise HTTPException(
            409, f"This run has reached its cap of {run['claim_limit']} claims."
        )
    started = connection.execute(
        "UPDATE claims SET status = 'in_progress', run_id = ? "
        "WHERE claim_id = ? AND status = 'waiting'",
        (run_id, claim_id),
    ).rowcount
    if not started:
        raise HTTPException(409, f"Claim {claim_id} is not waiting.")
    connection.execute(
        "INSERT OR IGNORE INTO run_claims (run_id, claim_id) VALUES (?, ?)",
        (run_id, claim_id),
    )


def release(connection: sqlite3.Connection, claim_id: str, problem: str) -> None:
    row = claim_row(connection, claim_id)
    if row["status"] != "in_progress":
        return
    attempts = row["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        status = "needs_handler"
        outcome = "It has failed twice and is left for a handler."
    else:
        status, outcome = "waiting", "The claim is back in the queue."
    connection.execute(
        "UPDATE claims SET status = ?, attempts = ? WHERE claim_id = ?",
        (status, attempts, claim_id),
    )
    add_step_row(connection, claim_id, Step(kind="error", text=f"{problem} {outcome}"))


def end_run(connection: sqlite3.Connection, run_id: str, end: RunEnd) -> None:
    connection.execute(
        "UPDATE runs SET status = ?, summary = ?, finished_at = ? WHERE run_id = ?",
        (end.status, end.summary, now(), run_id),
    )


def recover() -> None:
    """Release claims and fail runs left behind by a backend that stopped."""
    with db.connect() as connection:
        stuck = connection.execute(
            "SELECT claim_id FROM claims WHERE status = 'in_progress'"
        ).fetchall()
        for row in stuck:
            release(connection, row["claim_id"], RESTARTED)
        connection.execute(
            "UPDATE runs SET status = 'failed', summary = ?, finished_at = ? "
            "WHERE status = 'running'",
            (RESTARTED, now()),
        )


@app.post("/api/runs", operation_id="start_run")
def start_run(run_limit: RunLimit) -> dict[str, Any]:
    """Start a queue run capped at a number of claims, without starting an agent."""
    with db.connect() as connection:
        run_id = new_run(connection, "queue", run_limit.limit)
        return run_view(connection, run_id)


@app.get("/api/queue/latest-run", operation_id="get_latest_run")
def get_latest_run() -> dict[str, Any] | None:
    """Get the latest run of the queue, or null if there has been none."""
    with db.connect() as connection:
        row = connection.execute(
            "SELECT run_id FROM runs WHERE kind = 'queue' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return run_view(connection, row["run_id"]) if row else None


@app.get("/api/runs/{run_id}", operation_id="get_run")
def get_run(run_id: str) -> dict[str, Any]:
    """Get a run with its to-do list, its claims and the waiting claims in reach."""
    with db.connect() as connection:
        return run_view(connection, run_id)


@app.put("/api/runs/{run_id}/todos", operation_id="update_todos")
def update_todos(run_id: str, todos: Todos) -> dict[str, Any]:
    """Replace a run's to-do list."""
    with db.connect() as connection:
        run_row(connection, run_id)
        connection.execute(
            "UPDATE runs SET todos = ? WHERE run_id = ?",
            (json.dumps([todo.model_dump() for todo in todos.todos]), run_id),
        )
        return run_view(connection, run_id)


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: str, end: RunEnd) -> dict[str, Any]:
    """Record that a run has finished or failed, with its summary."""
    with db.connect() as connection:
        run_row(connection, run_id)
        end_run(connection, run_id, end)
        return run_view(connection, run_id)


@app.get("/api/queue/waiting", operation_id="list_waiting_claims")
def list_waiting_claims() -> list[dict[str, Any]]:
    """List the waiting claims with how many times each has been tried."""
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT data, attempts FROM claims WHERE status = 'waiting' "
            "ORDER BY claim_id"
        ).fetchall()
    keys = ["claim_id", "claim_type", "summary", "amount_claimed", "submitted_on"]
    return [
        {key: json.loads(row["data"])[key] for key in keys}
        | {"attempts": row["attempts"]}
        for row in rows
    ]


@app.post("/api/claims/{claim_id}/start", operation_id="start_claim")
def start_claim(claim_id: str, claim_start: ClaimStart) -> dict[str, Any]:
    """Start work on a waiting claim in a run, within the run's cap."""
    with db.connect() as connection:
        start(connection, claim_start.run_id, claim_id)
        return claim_view(claim_row(connection, claim_id))


@app.post("/api/claims/{claim_id}/release", operation_id="release_claim")
def release_claim(claim_id: str, release_input: Release) -> dict[str, Any]:
    """Put a claim the agent failed to decide back in the queue, or to a handler."""
    with db.connect() as connection:
        release(connection, claim_id, release_input.problem)
        return claim_view(claim_row(connection, claim_id))


def work_one(run_id: str, claim_id: str) -> None:
    claim_agent.work_started(run_id, claim_id)
    with db.connect() as connection:
        end_run(connection, run_id, RunEnd(status="finished", summary=""))


def work_queue(run_id: str) -> None:
    with db.connect() as connection:
        claim_ids = waiting_ids(connection)
    for claim_id in claim_ids:
        try:
            with db.connect() as connection:
                start(connection, run_id, claim_id)
        except HTTPException:
            continue
        claim_agent.work_started(run_id, claim_id)
    with db.connect() as connection:
        worked = len(worked_ids(connection, run_id))
        end_run(
            connection,
            run_id,
            RunEnd(status="finished", summary=f"Worked {worked} claims."),
        )


@app.post("/api/claims/{claim_id}/run", operation_id="run_agent")
def run_agent(claim_id: str, background: BackgroundTasks) -> dict[str, str]:
    """Start the UnderCover Agent on one waiting claim."""
    with db.connect() as connection:
        claim_row(connection, claim_id)
        run_id = new_run(connection, "claim", 1)
        start(connection, run_id, claim_id)
    background.add_task(work_one, run_id, claim_id)
    return {"status": "in_progress", "run_id": run_id}


@app.post("/api/queue/run", operation_id="run_queue")
def run_queue(run_limit: RunLimit, background: BackgroundTasks) -> dict[str, Any]:
    """Start the UnderCover Agent on the queue, capped at a number of claims."""
    with db.connect() as connection:
        run_id = new_run(connection, "queue", run_limit.limit)
        run = run_view(connection, run_id)
    background.add_task(work_queue, run_id)
    return run
