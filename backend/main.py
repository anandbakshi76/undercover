"""The UnderCover backend."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Connection, RowMapping, insert, select, update

from agent import claim as claim_agent
from agent import queue_agent
from backend import db, documents, payout
from backend.db import claims, records, run_claims, runs, steps

Row = RowMapping

INSTRUCTIONS = Path(__file__).resolve().parent / "instructions"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init()
    recover()
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


def record(connection: Connection, kind: str, record_id: str) -> Any:
    data = connection.execute(
        select(records.c.data).where(records.c.kind == kind, records.c.id == record_id)
    ).scalar()
    if data is None:
        raise HTTPException(404, f"There is no {kind} {record_id}.")
    return json.loads(data)


def claim_row(connection: Connection, claim_id: str) -> Row:
    row = (
        connection.execute(select(claims).where(claims.c.claim_id == claim_id))
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, f"There is no claim {claim_id}.")
    return row


def claim_view(row: Row) -> dict[str, Any]:
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
        payment = {
            "amount": row["paid_amount"],
            "paid_at": row["paid_at"],
            "signed_off_by": row["signed_off_by"],
            "signed_off_at": row["signed_off_at"],
        }
    return {
        **json.loads(row["data"]),
        "status": row["status"],
        "decision": decision,
        "sign_off": sign_off,
        "payment": payment,
        "attempts": row["attempts"],
        "thread_id": row["thread_id"],
    }


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def list_claims() -> list[dict[str, Any]]:
    """List the claims queue with where each claim stands."""
    with db.connect() as connection:
        rows = (
            connection.execute(select(claims).order_by(claims.c.claim_id))
            .mappings()
            .all()
        )
    keys = ["claim_id", "claim_type", "summary", "amount_claimed", "submitted_on"]
    queue = []
    for row in rows:
        data = json.loads(row["data"])
        queue.append(
            {key: data[key] for key in keys}
            | {key: row[key] for key in ["status", "decision", "payout"]}
        )
    return queue


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


@app.post("/api/policies/{policy_id}/payout", operation_id="calculate_payout")
def calculate_payout(policy_id: str, request: payout.PayoutRequest) -> dict[str, Any]:
    """Work out the payout on an approved claim with UnderCover's payout rules."""
    with db.connect() as connection:
        policy = record(connection, "policy", policy_id)
        plans = record(connection, "settings", "plans")
        limit = approval_limit(connection)
    return dict(payout.work_out_payout(policy, plans, limit, request))


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


def document_text(connection: Connection, document: str) -> str:
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
    """Get the UnderCover Agent's instructions for running the queue and a claim."""
    return {
        kind: (INSTRUCTIONS / f"{kind}.md").read_text(encoding="utf-8")
        for kind in ["queue", "claim"]
    }


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers() -> list[dict[str, Any]]:
    """List the claims handlers."""
    with db.connect() as connection:
        rows = connection.execute(
            select(records.c.data)
            .where(records.c.kind == "handler")
            .order_by(records.c.id)
        ).scalars()
        return [json.loads(row) for row in rows]


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


def approval_limit(connection: Connection) -> float:
    return record(connection, "settings", "company")["payout_approval_limit"]


def add_step_row(connection: Connection, claim_id: str, step: Step) -> None:
    connection.execute(
        insert(steps).values(
            claim_id=claim_id, kind=step.kind, text=step.text, created_at=now()
        )
    )


def pay(connection: Connection, row: Row, amount: float) -> None:
    if row["decision"] != "approve":
        raise HTTPException(409, "Only an approved claim can be paid.")
    if row["paid_amount"] is not None:
        if round(amount, 2) == round(row["paid_amount"], 2):
            return
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
        update(claims)
        .where(claims.c.claim_id == row["claim_id"], claims.c.paid_amount.is_(None))
        .values(paid_amount=round(amount, 2), paid_at=now(), status="decided")
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
        rows = (
            connection.execute(
                select(steps.c.id, steps.c.kind, steps.c.text, steps.c.created_at)
                .where(steps.c.claim_id == claim_id)
                .order_by(steps.c.id)
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def same_decision(row: Row, decision: Decision) -> bool:
    return row["decision"] == decision.decision and round(row["payout"], 2) == round(
        decision.payout, 2
    )


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def record_decision(claim_id: str, decision: Decision) -> dict[str, Any]:
    """Approve, decline or refer a claim, with the payout and the reason."""
    if decision.decision != "approve" and decision.payout != 0:
        raise HTTPException(422, "A decline or a refer has a payout of 0.")
    with db.connect() as connection:
        row = claim_row(connection, claim_id)
        if row["decision"] is None:
            approved = decision.decision == "approve"
            over_limit = approved and decision.payout > approval_limit(connection)
            if over_limit:
                status = "awaiting_sign_off"
            else:
                status = "in_progress" if approved else "decided"
            recorded = connection.execute(
                update(claims)
                .where(claims.c.claim_id == claim_id, claims.c.decision.is_(None))
                .values(
                    status=status,
                    decision=decision.decision,
                    payout=round(decision.payout, 2),
                    reason=decision.reason,
                    policy_section=decision.policy_section,
                    decided_at=now(),
                )
            ).rowcount
            row = claim_row(connection, claim_id)
            if recorded:
                return claim_view(row)
        if not same_decision(row, decision):
            raise HTTPException(409, f"Claim {claim_id} has already been decided.")
        return claim_view(row)


@app.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
def pay_claim(claim_id: str, payment: Payment) -> dict[str, Any]:
    """Pay an approved claim the given amount."""
    with db.connect() as connection:
        pay(connection, claim_row(connection, claim_id), payment.amount)
        return claim_view(claim_row(connection, claim_id))


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off")
def sign_off(claim_id: str, sign_off: SignOff) -> dict[str, Any]:
    """Sign off an approval over the limit as a handler, so the agent can pay it."""
    with db.connect() as connection:
        handler = record(connection, "handler", sign_off.handler_id)
        if not handler["can_approve_over_limit"]:
            raise HTTPException(403, f"{handler['name']} cannot sign off this claim.")
        row = claim_row(connection, claim_id)
        if row["status"] != "awaiting_sign_off":
            raise HTTPException(409, f"Claim {claim_id} is not waiting for sign-off.")
        connection.execute(
            update(claims)
            .where(claims.c.claim_id == claim_id)
            .values(
                status="in_progress",
                signed_off_by=sign_off.handler_id,
                signed_off_at=now(),
            )
        )
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


def run_row(connection: Connection, run_id: str) -> Row:
    row = (
        connection.execute(select(runs).where(runs.c.run_id == run_id))
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, f"There is no run {run_id}.")
    return row


def waiting_ids(connection: Connection) -> list[str]:
    return list(
        connection.execute(
            select(claims.c.claim_id)
            .where(claims.c.status == "waiting")
            .order_by(claims.c.claim_id)
        ).scalars()
    )


def worked_ids(connection: Connection, run_id: str) -> list[str]:
    return list(
        connection.execute(
            select(run_claims.c.claim_id)
            .where(run_claims.c.run_id == run_id)
            .order_by(run_claims.c.id)
        ).scalars()
    )


def run_view(connection: Connection, run_id: str) -> dict[str, Any]:
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


def new_run(connection: Connection, kind: str, limit: int) -> str:
    run_id = f"RUN-{uuid4().hex[:8]}"
    connection.execute(
        insert(runs).values(
            run_id=run_id,
            kind=kind,
            claim_limit=limit,
            status="running",
            started_at=now(),
        )
    )
    return run_id


def start(connection: Connection, run_id: str, claim_id: str) -> None:
    run = run_row(connection, run_id)
    if run["status"] != "running":
        raise HTTPException(409, f"Run {run_id} has finished.")
    row = claim_row(connection, claim_id)
    if row["status"] == "in_progress" and row["run_id"] == run_id:
        return
    if row["status"] == "needs_handler":
        raise HTTPException(409, f"Claim {claim_id} is left for a handler.")
    worked = worked_ids(connection, run_id)
    if claim_id not in worked and len(worked) >= run["claim_limit"]:
        limit = run["claim_limit"]
        raise HTTPException(
            409,
            f"This run has reached its cap of {limit} "
            f"{'claim' if limit == 1 else 'claims'}.",
        )
    started = connection.execute(
        update(claims)
        .where(claims.c.claim_id == claim_id, claims.c.status == "waiting")
        .values(
            status="in_progress",
            run_id=run_id,
            thread_id=f"{claim_id}-{run_id}-{row['attempts'] + 1}",
        )
    ).rowcount
    if not started:
        raise HTTPException(409, f"Claim {claim_id} is not waiting.")
    if claim_id not in worked:
        connection.execute(insert(run_claims).values(run_id=run_id, claim_id=claim_id))


def release(connection: Connection, claim_id: str, problem: str) -> None:
    row = claim_row(connection, claim_id)
    if row["status"] != "in_progress" or row["decision"] is not None:
        return
    attempts = row["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        status = "needs_handler"
        outcome = "It has failed twice and is left for a handler."
    else:
        status, outcome = "waiting", "The claim is back in the queue."
    connection.execute(
        update(claims)
        .where(claims.c.claim_id == claim_id)
        .values(status=status, attempts=attempts)
    )
    add_step_row(connection, claim_id, Step(kind="error", text=f"{problem} {outcome}"))


def end_run(connection: Connection, run_id: str, end: RunEnd) -> None:
    connection.execute(
        update(runs)
        .where(runs.c.run_id == run_id)
        .values(status=end.status, summary=end.summary, finished_at=now())
    )


def recover() -> None:
    """Release claims and fail runs left behind by a backend that stopped."""
    with db.connect() as connection:
        stuck = connection.execute(
            select(claims.c.claim_id).where(claims.c.status == "in_progress")
        ).scalars()
        for claim_id in list(stuck):
            release(connection, claim_id, RESTARTED)
        connection.execute(
            update(runs)
            .where(runs.c.status == "running")
            .values(status="failed", summary=RESTARTED, finished_at=now())
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
        run_id = connection.execute(
            select(runs.c.run_id)
            .where(runs.c.kind == "queue")
            .order_by(runs.c.id.desc())
            .limit(1)
        ).scalar()
        return run_view(connection, run_id) if run_id else None


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
            update(runs)
            .where(runs.c.run_id == run_id)
            .values(todos=json.dumps([todo.model_dump() for todo in todos.todos]))
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
        rows = (
            connection.execute(
                select(claims.c.data, claims.c.attempts)
                .where(claims.c.status == "waiting")
                .order_by(claims.c.claim_id)
            )
            .mappings()
            .all()
        )
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
    claim_agent.carry_on(claim_id)
    with db.connect() as connection:
        end_run(connection, run_id, RunEnd(status="finished", summary=""))


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
    background.add_task(queue_agent.run_queue, run_id)
    return run
