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
from sqlalchemy import Connection, RowMapping, func, insert, select, update

from backend import agent_service, db, documents, payout, scoring
from backend.db import (
    claims,
    eval_claims,
    eval_decisions,
    model_calls,
    records,
    run_claims,
    runs,
    steps,
    work_time,
)

Row = RowMapping

INSTRUCTIONS = Path(__file__).resolve().parent / "instructions"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init()
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
        "run_id": row["run_id"],
        "evaluation_id": row["evaluation_id"],
        "source_id": row["source_id"],
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
            connection.execute(
                select(claims)
                .where(claims.c.evaluation_id.is_(None))
                .order_by(claims.c.claim_id)
            )
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


def claim_totals(connection: Connection, claim_id: str) -> dict[str, float]:
    cost = connection.execute(
        select(func.coalesce(func.sum(model_calls.c.cost), 0.0)).where(
            model_calls.c.claim_id == claim_id
        )
    ).scalar()
    seconds = connection.execute(
        select(func.coalesce(func.sum(work_time.c.seconds), 0.0)).where(
            work_time.c.claim_id == claim_id
        )
    ).scalar()
    return {"cost": float(cost or 0), "seconds": float(seconds or 0)}


@app.get("/api/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str) -> dict[str, Any]:
    """Get one claim in full, with its decision, sign-off, payment, cost and time."""
    with db.connect() as connection:
        view = claim_view(claim_row(connection, claim_id))
        return view | claim_totals(connection, claim_id)


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
    """Get the claim agent's instructions."""
    return {"claim": (INSTRUCTIONS / "claim.md").read_text(encoding="utf-8")}


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


class ModelCall(BaseModel):
    run_id: str | None
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)


class WorkTime(BaseModel):
    run_id: str | None
    started_at: str = Field(min_length=1)
    seconds: float = Field(ge=0)


class Payment(BaseModel):
    amount: float = Field(gt=0)


class SignOff(BaseModel):
    handler_id: str


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def now_ms() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


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


@app.post("/api/claims/{claim_id}/model-calls", operation_id="record_model_call")
def record_model_call(claim_id: str, call: ModelCall) -> dict[str, str]:
    """Record the tokens and cost of one model call on a claim, with its run."""
    with db.connect() as connection:
        claim_row(connection, claim_id)
        connection.execute(
            insert(model_calls).values(
                claim_id=claim_id,
                created_at=now_ms(),
                **call.model_dump(exclude={"run_id"}),
                run_id=usage_run(connection, call.run_id),
            )
        )
    return {"status": "recorded"}


@app.post("/api/claims/{claim_id}/work-time", operation_id="record_work_time")
def record_work_time(claim_id: str, work: WorkTime) -> dict[str, str]:
    """Record one stretch of the agent's work on a claim, with its run."""
    with db.connect() as connection:
        claim_row(connection, claim_id)
        connection.execute(
            insert(work_time).values(
                claim_id=claim_id,
                **work.model_dump(exclude={"run_id"}),
                run_id=usage_run(connection, work.run_id),
            )
        )
    return {"status": "recorded"}


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
def sign_off(
    claim_id: str, sign_off: SignOff, background: BackgroundTasks
) -> dict[str, Any]:
    """Sign off an approval over the limit as a handler, so the agent can pay it."""
    with db.connect() as connection:
        handler = record(connection, "handler", sign_off.handler_id)
        if not handler["can_approve_over_limit"]:
            raise HTTPException(403, f"{handler['name']} cannot sign off this claim.")
        row = claim_row(connection, claim_id)
        if row["evaluation_id"] is not None:
            raise HTTPException(
                409, f"Claim {claim_id} is an evaluation copy and cannot be signed off."
            )
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
        view = claim_view(claim_row(connection, claim_id))
    background.add_task(agent_service.tell, claim_id)
    return view


MAX_ATTEMPTS = 2
RUN_KINDS = ["dispatch", "evaluation"]


class NewDispatch(BaseModel):
    concurrency: int = Field(ge=1, le=25)
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)


class RunEnd(BaseModel):
    status: Literal["finished", "failed"]


class DispatchStart(BaseModel):
    concurrency: int = Field(ge=1, le=25)


EvaluationSet = Literal["evaluation", "held_out"]


class EvaluationStart(BaseModel):
    concurrency: int = Field(ge=1, le=25)
    evaluation_set: EvaluationSet
    tiny: bool


class NewEvaluation(BaseModel):
    concurrency: int = Field(ge=1, le=25)
    evaluation_set: EvaluationSet
    tiny: bool
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)


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


def worked_ids(connection: Connection, run_id: str) -> list[str]:
    return list(
        connection.execute(
            select(run_claims.c.claim_id)
            .where(run_claims.c.run_id == run_id)
            .order_by(run_claims.c.id)
        ).scalars()
    )


def run_claims_view(connection: Connection, run_id: str) -> list[dict[str, Any]]:
    ids = worked_ids(connection, run_id)
    rows = {
        row["claim_id"]: row
        for row in connection.execute(
            select(
                claims.c.claim_id, claims.c.status, claims.c.decision, claims.c.payout
            ).where(claims.c.claim_id.in_(ids))
        ).mappings()
    }
    costs = {
        claim_id: cost
        for claim_id, cost in connection.execute(
            select(model_calls.c.claim_id, func.sum(model_calls.c.cost))
            .where(model_calls.c.run_id == run_id)
            .group_by(model_calls.c.claim_id)
        ).all()
    }
    times = {
        claim_id: (started, seconds)
        for claim_id, started, seconds in connection.execute(
            select(
                work_time.c.claim_id,
                func.min(work_time.c.started_at),
                func.sum(work_time.c.seconds),
            )
            .where(work_time.c.run_id == run_id)
            .group_by(work_time.c.claim_id)
        ).all()
    }
    return [
        {
            "claim_id": claim_id,
            "status": rows[claim_id]["status"],
            "decision": rows[claim_id]["decision"],
            "payout": rows[claim_id]["payout"],
            "started_at": times.get(claim_id, (None, 0.0))[0],
            "seconds": float(times.get(claim_id, (None, 0.0))[1] or 0),
            "cost": float(costs.get(claim_id) or 0),
        }
        for claim_id in ids
    ]


def elapsed_seconds(row: Row) -> float:
    started = datetime.fromisoformat(row["started_at"])
    ended = datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
    return ((ended or datetime.now(UTC)) - started).total_seconds()


def run_view(connection: Connection, run_id: str) -> dict[str, Any]:
    row = run_row(connection, run_id)
    worked = run_claims_view(connection, run_id)
    tokens = connection.execute(
        select(
            func.coalesce(func.sum(model_calls.c.prompt_tokens), 0),
            func.coalesce(func.sum(model_calls.c.completion_tokens), 0),
        ).where(model_calls.c.run_id == run_id)
    ).one()
    return {
        "run_id": row["run_id"],
        "kind": row["kind"],
        "concurrency": row["concurrency"],
        "status": row["status"],
        "summary": json.loads(row["summary"]) if row["summary"] else None,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "model": row["model"],
        "reasoning_effort": row["reasoning_effort"],
        "evaluation_set": row["evaluation_set"],
        "tiny": None if row["tiny"] is None else bool(row["tiny"]),
        "claims_worked": [item["claim_id"] for item in worked],
        "claims": worked,
        "prompt_tokens": int(tokens[0]),
        "completion_tokens": int(tokens[1]),
        "cost": sum(item["cost"] for item in worked),
        "work_seconds": sum(item["seconds"] for item in worked),
        "elapsed_seconds": elapsed_seconds(row),
    }


def new_run(
    connection: Connection,
    kind: str,
    concurrency: int,
    model: str = "",
    reasoning_effort: str = "",
    **evaluation: Any,
) -> str:
    if (
        kind in RUN_KINDS
        and connection.execute(
            select(runs.c.id).where(
                runs.c.kind.in_(RUN_KINDS), runs.c.status == "running"
            )
        ).first()
    ):
        raise HTTPException(409, "A dispatch or evaluation is already running.")
    prefix = "EVAL" if kind == "evaluation" else "RUN"
    run_id = f"{prefix}-{uuid4().hex[:8]}"
    connection.execute(
        insert(runs).values(
            run_id=run_id,
            kind=kind,
            concurrency=concurrency,
            status="running",
            started_at=now_ms(),
            model=model,
            reasoning_effort=reasoning_effort,
            **evaluation,
        )
    )
    return run_id


def join_run(connection: Connection, run_id: str, claim_id: str) -> None:
    if claim_id not in worked_ids(connection, run_id):
        connection.execute(insert(run_claims).values(run_id=run_id, claim_id=claim_id))


def usage_run(connection: Connection, run_id: str | None) -> str | None:
    """Keep a run id only for a dispatch or an evaluation."""
    if run_id is None:
        return None
    kind = connection.execute(
        select(runs.c.kind).where(runs.c.run_id == run_id)
    ).scalar()
    return run_id if kind in RUN_KINDS else None


def start(connection: Connection, run_id: str, claim_id: str) -> None:
    run = run_row(connection, run_id)
    if run["status"] != "running":
        raise HTTPException(409, f"Run {run_id} has finished.")
    row = claim_row(connection, claim_id)
    if row["status"] == "in_progress":
        join_run(connection, run_id, claim_id)
        return
    if row["status"] == "needs_handler":
        raise HTTPException(409, f"Claim {claim_id} is left for a handler.")
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
    join_run(connection, run_id, claim_id)


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


def build_summary(connection: Connection, run_id: str) -> dict[str, Any]:
    """Count a run's decisions and list what still needs people, from the records."""
    worked = run_claims_view(connection, run_id)
    decisions = [item["decision"] for item in worked]
    return {
        "approved": decisions.count("approve"),
        "declined": decisions.count("decline"),
        "referred": decisions.count("refer"),
        "awaiting_sign_off": [
            {"claim_id": item["claim_id"], "payout": item["payout"]}
            for item in worked
            if item["status"] == "awaiting_sign_off"
        ],
        "needs_handler": [
            item["claim_id"] for item in worked if item["status"] == "needs_handler"
        ],
        "in_progress": [
            item["claim_id"] for item in worked if item["status"] == "in_progress"
        ],
    }


def end_run(connection: Connection, run_id: str, status: str) -> None:
    connection.execute(
        update(runs)
        .where(runs.c.run_id == run_id)
        .values(
            status=status,
            summary=json.dumps(build_summary(connection, run_id)),
            finished_at=now_ms(),
        )
    )


@app.post("/api/runs/dispatch", operation_id="create_dispatch")
def create_dispatch(new: NewDispatch) -> dict[str, Any]:
    """Create a dispatch of the queue, refused while another run is going."""
    with db.connect() as connection:
        run_id = new_run(
            connection,
            "dispatch",
            new.concurrency,
            new.model,
            new.reasoning_effort,
        )
        return run_view(connection, run_id)


@app.post("/api/runs/evaluation", operation_id="create_evaluation")
def create_evaluation(new: NewEvaluation) -> dict[str, Any]:
    """Create an evaluation with its own copies of a labelled set of claims."""
    with db.connect() as connection:
        run_id = new_run(
            connection,
            "evaluation",
            new.concurrency,
            new.model,
            new.reasoning_effort,
            evaluation_set=new.evaluation_set,
            tiny=int(new.tiny),
        )
        wanted = select(eval_claims.c.claim_id, eval_claims.c.data).where(
            eval_claims.c.eval_set == new.evaluation_set
        )
        if new.tiny:
            wanted = wanted.where(eval_claims.c.tiny == 1)
        for source_id, data in connection.execute(
            wanted.order_by(eval_claims.c.claim_id)
        ):
            copy_id = f"{run_id}-{source_id}"
            record = json.loads(data) | {"claim_id": copy_id}
            connection.execute(
                insert(claims).values(
                    claim_id=copy_id,
                    status="waiting",
                    data=json.dumps(record),
                    evaluation_id=run_id,
                    source_id=source_id,
                )
            )
            join_run(connection, run_id, copy_id)
        return run_view(connection, run_id)


def evaluation_rows(connection: Connection, run: Row) -> list[dict[str, Any]]:
    """One row for each copy: the agent's answer beside the handler's."""
    copies = {
        row["claim_id"]: row
        for row in connection.execute(
            select(claims).where(claims.c.evaluation_id == run["run_id"])
        ).mappings()
    }
    key = {
        claim_id: {"decision": decision, "payout": payout}
        for claim_id, decision, payout in connection.execute(
            select(
                eval_decisions.c.claim_id,
                eval_decisions.c.decision,
                eval_decisions.c.payout,
            ).where(eval_decisions.c.eval_set == run["evaluation_set"])
        ).all()
    }
    rows = []
    for item in run_claims_view(connection, run["run_id"]):
        copy = copies[item["claim_id"]]
        handler = key[copy["source_id"]]
        agent = scoring.answer(copy["decision"], copy["payout"])
        rows.append(
            {
                "claim_id": item["claim_id"],
                "source_id": copy["source_id"],
                "status": item["status"],
                "handler": handler,
                "agent": agent,
                "reason": copy["reason"],
                "right": scoring.is_right(agent, handler),
                "mistake_cost": scoring.mistake_cost(agent, handler),
                "cost": item["cost"],
                "seconds": item["seconds"],
                "started_at": item["started_at"],
            }
        )
    return rows


def evaluation_view(
    connection: Connection, run_id: str, with_claims: bool
) -> dict[str, Any]:
    run = run_row(connection, run_id)
    if run["kind"] != "evaluation":
        raise HTTPException(404, f"There is no evaluation {run_id}.")
    rows = evaluation_rows(connection, run)
    view = {
        "run_id": run_id,
        "evaluation_set": run["evaluation_set"],
        "tiny": bool(run["tiny"]),
        "concurrency": run["concurrency"],
        "model": run["model"],
        "reasoning_effort": run["reasoning_effort"],
        "status": run["status"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
    } | scoring.measures(rows, elapsed_seconds(run))
    return view | {"claims": rows} if with_claims else view


@app.get("/api/evaluations", operation_id="list_evaluations")
def list_evaluations() -> list[dict[str, Any]]:
    """List the evaluations, newest first, with their measures."""
    with db.connect() as connection:
        ids = list(
            connection.execute(
                select(runs.c.run_id)
                .where(runs.c.kind == "evaluation")
                .order_by(runs.c.id.desc())
            ).scalars()
        )
        return [evaluation_view(connection, run_id, False) for run_id in ids]


@app.get("/api/evaluations/{run_id}", operation_id="get_evaluation")
def get_evaluation(run_id: str) -> dict[str, Any]:
    """Get an evaluation with each claim's answer beside the handler's."""
    with db.connect() as connection:
        return evaluation_view(connection, run_id, True)


@app.post("/api/evaluations", operation_id="start_evaluation")
def start_evaluation(evaluation: EvaluationStart) -> dict[str, Any]:
    """Ask the agent's service to start an evaluation of a labelled set."""
    return agent_service.forward("/api/evaluations", evaluation.model_dump())


@app.post("/api/runs/stop-running", operation_id="stop_running_runs")
def stop_running_runs() -> list[str]:
    """Mark every dispatch or evaluation still running as stopped."""
    with db.connect() as connection:
        ids = list(
            connection.execute(
                select(runs.c.run_id).where(
                    runs.c.kind.in_(RUN_KINDS), runs.c.status == "running"
                )
            ).scalars()
        )
        for run_id in ids:
            end_run(connection, run_id, "stopped")
    return ids


@app.get("/api/dispatches/latest", operation_id="get_latest_dispatch")
def get_latest_dispatch() -> dict[str, Any] | None:
    """Get the latest dispatch, or null if there has been none."""
    with db.connect() as connection:
        run_id = connection.execute(
            select(runs.c.run_id)
            .where(runs.c.kind == "dispatch")
            .order_by(runs.c.id.desc())
            .limit(1)
        ).scalar()
        return run_view(connection, run_id) if run_id else None


@app.post("/api/dispatches", operation_id="start_dispatch")
def start_dispatch(dispatch: DispatchStart) -> dict[str, Any]:
    """Ask the agent's service to start a dispatch with N claim agents at once."""
    return agent_service.forward(
        "/api/dispatches", {"concurrency": dispatch.concurrency}
    )


@app.get("/api/runs/{run_id}", operation_id="get_run")
def get_run(run_id: str) -> dict[str, Any]:
    """Get a run with the claims it has worked."""
    with db.connect() as connection:
        return run_view(connection, run_id)


@app.get("/api/runs/{run_id}/claims-to-work", operation_id="list_claims_to_work")
def list_claims_to_work(run_id: str) -> list[str]:
    """List the claims a run should work: those in progress, then those waiting."""
    with db.connect() as connection:
        run = run_row(connection, run_id)
        rows = (
            connection.execute(
                select(claims.c.claim_id, claims.c.status, claims.c.data).where(
                    claims.c.status.in_(["in_progress", "waiting"]),
                    claims.c.evaluation_id.is_(None)
                    if run["kind"] != "evaluation"
                    else claims.c.evaluation_id == run_id,
                )
            )
            .mappings()
            .all()
        )
    ordered = sorted(
        rows,
        key=lambda r: (
            r["status"] != "in_progress",
            json.loads(r["data"])["submitted_on"],
            r["claim_id"],
        ),
    )
    return [row["claim_id"] for row in ordered]


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: str, end: RunEnd) -> dict[str, Any]:
    """Record that a run has finished or failed, with a summary from the records."""
    with db.connect() as connection:
        run_row(connection, run_id)
        end_run(connection, run_id, end.status)
        return run_view(connection, run_id)


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


@app.post("/api/claims/{claim_id}/start-run", operation_id="start_claim_run")
def start_claim_run(claim_id: str) -> dict[str, str]:
    """Start a run on one waiting claim, without starting an agent."""
    with db.connect() as connection:
        row = claim_row(connection, claim_id)
        if row["evaluation_id"] is not None:
            raise HTTPException(
                409, f"Claim {claim_id} is an evaluation copy and cannot be run."
            )
        if row["status"] != "waiting":
            raise HTTPException(409, f"Claim {claim_id} is not waiting.")
        run_id = new_run(connection, "claim", 1)
        start(connection, run_id, claim_id)
    return {"status": "in_progress", "run_id": run_id}


@app.post("/api/claims/{claim_id}/run", operation_id="run_agent")
def run_agent(claim_id: str) -> dict[str, Any]:
    """Start the UnderCover Agent on one waiting claim."""
    return agent_service.forward(f"/api/claims/{claim_id}/run")
