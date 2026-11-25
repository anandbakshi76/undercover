"""The UnderCover backend."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Connection, RowMapping, delete, func, insert, select, update

from backend import (
    agent_service,
    auth,
    db,
    documents,
    improvement,
    judge,
    payout,
    scoring,
    settings,
    versions,
)
from backend.db import (
    claims,
    configurations,
    eval_claims,
    eval_decisions,
    grades,
    improvement_state,
    improver_calls,
    model_calls,
    platform_settings,
    proposals,
    recipes,
    records,
    rounds,
    run_claims,
    runs,
    setting_changes,
    steps,
    work_time,
)

Row = RowMapping


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
        row = claim_row(connection, claim_id)
        version = connection.execute(
            select(runs.c.version).where(runs.c.run_id == row["run_id"])
        ).scalar()
        return (
            claim_view(row) | claim_totals(connection, claim_id) | {"version": version}
        )


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


@app.get("/api/me", operation_id="get_me")
def get_me(handler: auth.SignedIn) -> dict[str, Any]:
    """Get the signed-in handler."""
    return handler


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


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def now_ms() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def approval_limit(connection: Connection) -> float:
    return float(settings.read(connection, "approval_limit"))


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
    if row["sign_off_required"] and row["signed_off_by"] is None:
        raise HTTPException(
            409,
            f"A payout over {money(approval_limit(connection))} needs a handler's "
            "sign-off before it can be paid.",
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
                    sign_off_required=int(over_limit),
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
    claim_id: str, background: BackgroundTasks, handler: auth.SignedIn
) -> dict[str, Any]:
    """Sign off an approval over the limit as the signed-in handler."""
    if not handler["can_approve_over_limit"]:
        raise HTTPException(
            403, f"{handler['name']} can't sign off a payout over the limit."
        )
    with db.connect() as connection:
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
                signed_off_by=handler["handler_id"],
                signed_off_at=now(),
            )
        )
        view = claim_view(claim_row(connection, claim_id))
    background.add_task(agent_service.tell, claim_id)
    return view


MAX_ATTEMPTS = 2
RUN_KINDS = ["dispatch", "evaluation", "improver"]
RUNNING = {
    "dispatch": "A dispatch",
    "evaluation": "An evaluation",
    "improver": "The improver",
}


class NewDispatch(BaseModel):
    concurrency: int = Field(ge=1, le=25)
    version: int = Field(ge=1)
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
    version: int = Field(ge=1)
    round_id: str | None
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)


class SettingChange(BaseModel):
    value: Any


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
        "version": row["version"],
        "round_id": row["round_id"],
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


def refuse_if_busy(connection: Connection, kind: str, round_id: str | None) -> None:
    """Refuse a new run while another is going, naming it, as only one runs at a time.

    A round's own evaluations are part of the improver's stretch of work.
    """
    going = connection.execute(
        select(runs.c.run_id, runs.c.kind, runs.c.round_id).where(
            runs.c.kind.in_(RUN_KINDS), runs.c.status == "running"
        )
    ).all()
    if kind == "evaluation" and round_id:
        going = [
            r for r in going if not (r.kind == "improver" and r.round_id == round_id)
        ]
    if going:
        raise HTTPException(
            409, f"{RUNNING[going[0].kind]} ({going[0].run_id}) is running."
        )


def new_run(
    connection: Connection,
    kind: str,
    concurrency: int,
    **options: Any,
) -> str:
    """Start a run, with a version, a model and an effort, and any other columns."""
    version = options.pop("version", None)
    model = options.pop("model", "")
    reasoning_effort = options.pop("reasoning_effort", "")
    if kind in RUN_KINDS:
        refuse_if_busy(connection, kind, options.get("round_id"))
    version = version or versions.current(connection)
    config = versions.configuration(connection, version)
    if config is None:
        raise HTTPException(404, f"There is no version {version} of the configuration.")
    prefix = "EVAL" if kind == "evaluation" else "RUN"
    run_id = f"{prefix}-{uuid4().hex[:8]}"
    connection.execute(
        insert(runs).values(
            run_id=run_id,
            kind=kind,
            concurrency=concurrency,
            status="running",
            started_at=now_ms(),
            version=version,
            model=model or config["model"],
            reasoning_effort=reasoning_effort or config["reasoning_effort"],
            **options,
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
    if run["round_id"]:
        round_allows_claims(connection, run["round_id"])
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
            version=new.version,
            model=new.model,
            reasoning_effort=new.reasoning_effort,
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
            version=new.version,
            model=new.model,
            reasoning_effort=new.reasoning_effort,
            evaluation_set=new.evaluation_set,
            tiny=int(new.tiny),
            round_id=new.round_id,
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


def grade_view(mark: Row | None) -> dict[str, Any] | None:
    """A claim's grade with its two checks, or None when it wasn't graded."""
    if mark is None or not mark["graded"]:
        return None
    return {
        "score": mark["grade"],
        "letter": mark["letter"],
        "same_grounds": mark["same_grounds"],
        "same_section": mark["same_section"],
    }


def judge_items(connection: Connection, run: Row) -> list[dict[str, Any]]:
    """What the judge needs for each copy: the agent's explanation and the handler's."""
    copies = connection.execute(
        select(claims).where(claims.c.evaluation_id == run["run_id"])
    ).mappings()
    key = {
        claim_id: json.loads(data)
        for claim_id, data in connection.execute(
            select(eval_decisions.c.claim_id, eval_decisions.c.data).where(
                eval_decisions.c.eval_set == run["evaluation_set"]
            )
        ).all()
    }
    return [
        {
            "claim_id": copy["claim_id"],
            "decision": copy["decision"],
            "rationale": copy["reason"],
            "expected": key[copy["source_id"]],
        }
        for copy in copies
    ]


def store_grades(
    connection: Connection, run_id: str, results: dict[str, dict[str, Any] | None]
) -> None:
    connection.execute(delete(grades).where(grades.c.run_id == run_id))
    for claim_id, result in results.items():
        connection.execute(
            insert(grades).values(
                run_id=run_id,
                claim_id=claim_id,
                graded=int(result is not None),
                grade=result["grade"] if result else None,
                letter=result["letter"] if result else None,
                same_grounds=result["same_grounds"] if result else None,
                same_section=result["same_section"] if result else None,
                judge_cost=result["judge_cost"] if result else 0.0,
            )
        )


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
    graded = {
        row["claim_id"]: row
        for row in connection.execute(
            select(grades).where(grades.c.run_id == run["run_id"])
        ).mappings()
    }
    rows = []
    for item in run_claims_view(connection, run["run_id"]):
        copy = copies[item["claim_id"]]
        mark = graded.get(item["claim_id"])
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
                "grade": grade_view(mark),
                "judge_cost": mark["judge_cost"] if mark else 0.0,
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
        "version": run["version"],
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


@app.post(
    "/api/evaluations",
    operation_id="start_evaluation",
    dependencies=[auth.SIGNED_IN],
)
def start_evaluation(evaluation: EvaluationStart) -> dict[str, Any]:
    """Ask the agent's service to start an evaluation of a labelled set."""
    return agent_service.forward("/api/evaluations", evaluation.model_dump())


@app.post("/api/runs/stop-running", operation_id="stop_running_runs")
def stop_running_runs() -> list[str]:
    """Mark every dispatch, evaluation and improver stretch still running as stopped."""
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
        connection.execute(
            update(rounds)
            .where(rounds.c.status == "working")
            .values(status="stopped", stage="stopped", finished_at=now_ms())
        )
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


@app.post(
    "/api/dispatches",
    operation_id="start_dispatch",
    dependencies=[auth.SIGNED_IN],
)
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
    """Record that a run has finished or failed, grading an evaluation's claims."""
    with db.connect() as connection:
        run = run_row(connection, run_id)
        status = "stopped" if cut_short(connection, run, end.status) else end.status
        grading = (
            run["kind"] == "evaluation" and status == "finished" and judge.enabled()
        )
        items = judge_items(connection, run) if grading else []
    results = judge.grade_all(items)
    with db.connect() as connection:
        store_grades(connection, run_id, results)
        end_run(connection, run_id, status)
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


def handler_name(connection: Connection, handler_id: str | None) -> str | None:
    if handler_id is None:
        return None
    return record(connection, "handler", handler_id)["name"]


def setting_view(connection: Connection, key: str) -> dict[str, Any]:
    row = (
        connection.execute(
            select(platform_settings).where(platform_settings.c.key == key)
        )
        .mappings()
        .one()
    )
    return {
        "key": key,
        "value": json.loads(row["value"]),
        "changed_by": handler_name(connection, row["changed_by"]),
        "changed_at": row["changed_at"],
    }


@app.get("/api/settings", operation_id="get_settings")
def get_settings() -> list[dict[str, Any]]:
    """Get the platform's settings, with who last changed each and when."""
    with db.connect() as connection:
        return [setting_view(connection, key) for key in settings.VALIDATORS]


@app.get("/api/settings/changes", operation_id="list_setting_changes")
def list_setting_changes() -> list[dict[str, Any]]:
    """List every change to a setting, newest first."""
    with db.connect() as connection:
        rows = (
            connection.execute(
                select(setting_changes).order_by(setting_changes.c.id.desc())
            )
            .mappings()
            .all()
        )
        return [
            {
                "key": row["key"],
                "old": json.loads(row["old_value"]),
                "new": json.loads(row["new_value"]),
                "changed_by": handler_name(connection, row["changed_by"]),
                "changed_at": row["changed_at"],
            }
            for row in rows
        ]


@app.put("/api/settings/{key}", operation_id="change_setting")
def change_setting(
    key: str, change: SettingChange, handler: auth.Senior
) -> dict[str, Any]:
    """Change one setting as a senior handler, recording who and when."""
    value = settings.check(key, change.value)
    with db.connect() as connection:
        old = settings.read(connection, key)
        when = now()
        connection.execute(
            update(platform_settings)
            .where(platform_settings.c.key == key)
            .values(
                value=json.dumps(value),
                changed_by=handler["handler_id"],
                changed_at=when,
            )
        )
        connection.execute(
            insert(setting_changes).values(
                key=key,
                old_value=json.dumps(old),
                new_value=json.dumps(value),
                changed_by=handler["handler_id"],
                changed_at=when,
            )
        )
        return setting_view(connection, key)


@app.get("/api/models", operation_id="list_models")
def list_models() -> list[dict[str, Any]]:
    """List the models the claim agent may use, with their efforts and prices."""
    return versions.models_allowed()


def version_view(row: dict[str, Any], current_version: int) -> dict[str, Any]:
    keys = ["version", "model", "reasoning_effort", "reason", "proposed_by"]
    keys += ["approved_by", "approved_at", "round_id"]
    return {key: row[key] for key in keys} | {
        "is_current": row["version"] == current_version
    }


@app.get("/api/configurations", operation_id="list_configurations")
def list_configurations() -> list[dict[str, Any]]:
    """List every version of the claim agent's configuration, with its scores."""
    with db.connect() as connection:
        current = versions.current(connection)
        rows = (
            connection.execute(
                select(configurations).order_by(configurations.c.version)
            )
            .mappings()
            .all()
        )
        return [
            version_view(dict(row), current)
            | {"scores": version_scores(connection, row["version"])}
            for row in rows
        ]


@app.get("/api/configurations/current", operation_id="get_current_configuration")
def get_current_configuration() -> dict[str, Any]:
    """Get the version of the claim agent's configuration now in use."""
    with db.connect() as connection:
        found = versions.configuration(connection, versions.current(connection))
    return found or {}


def version_or_404(connection: Connection, version: int) -> dict[str, Any]:
    found = versions.configuration(connection, version)
    if found is None:
        raise HTTPException(404, f"There is no version {version} of the configuration.")
    return found


@app.get("/api/configurations/{version}", operation_id="get_claim_configuration")
def get_claim_configuration(version: int) -> dict[str, Any]:
    """Get a version's instructions, model and reasoning effort, with no scores."""
    with db.connect() as connection:
        found = version_or_404(connection, version)
    keys = ["version", "instructions", "model", "reasoning_effort"]
    return {key: found[key] for key in keys}


@app.get("/api/configurations/{version}/diff", operation_id="get_configuration_diff")
def get_configuration_diff(version: int) -> dict[str, Any]:
    """Show what a version of the configuration changed from the one before."""
    with db.connect() as connection:
        found = version_or_404(connection, version)
        before = versions.configuration(connection, version - 1)
        current = versions.current(connection)
    return version_view(found, current) | {
        "previous": before["version"] if before else None,
        "instructions": versions.diff_lines(
            before["instructions"] if before else found["instructions"],
            found["instructions"],
        ),
        "model": {
            "before": before["model"] if before else None,
            "after": found["model"],
        },
        "reasoning_effort": {
            "before": before["reasoning_effort"] if before else None,
            "after": found["reasoning_effort"],
        },
    }


@app.get("/api/recipes", operation_id="list_recipes")
def list_recipes() -> list[dict[str, Any]]:
    """List every version of the improver's recipe."""
    with db.connect() as connection:
        current = versions.current(connection, "current_recipe")
        rows = (
            connection.execute(select(recipes).order_by(recipes.c.version))
            .mappings()
            .all()
        )
    keys = [
        "version",
        "reason",
        "proposed_by",
        "approved_by",
        "approved_at",
        "round_id",
    ]
    return [
        {key: row[key] for key in keys} | {"is_current": row["version"] == current}
        for row in rows
    ]


@app.get("/api/recipes/{version}", operation_id="get_recipe")
def get_recipe(version: int) -> dict[str, Any]:
    """Get one version of the improver's recipe, with its text."""
    with db.connect() as connection:
        found = versions.recipe(connection, version)
    if found is None:
        raise HTTPException(404, f"There is no version {version} of the recipe.")
    return {"version": found["version"], "recipe": found["recipe"]}


@app.get("/api/recipes/{version}/diff", operation_id="get_recipe_diff")
def get_recipe_diff(version: int) -> dict[str, Any]:
    """Show what a version of the recipe changed from the one before."""
    with db.connect() as connection:
        found = versions.recipe(connection, version)
        if found is None:
            raise HTTPException(404, f"There is no version {version} of the recipe.")
        before = versions.recipe(connection, version - 1)
    return {
        "version": version,
        "previous": before["version"] if before else None,
        "reason": found["reason"],
        "proposed_by": found["proposed_by"],
        "approved_by": found["approved_by"],
        "approved_at": found["approved_at"],
        "lines": versions.diff_lines(
            before["recipe"] if before else found["recipe"], found["recipe"]
        ),
    }


# The improver: rounds, proposals, spending and the verdict.

ROUND_OPEN = ["working", "waiting"]
FIRST_RUNS = {"evaluation": 1, "held_out": 2}
YES = 0.5
STAGES = Literal[
    "first_evaluations",
    "improver_working",
    "change_waiting",
    "evaluating",
    "verdict",
    "recipe_waiting",
    "finished",
    "stopped",
]


class NewRound(BaseModel):
    started_by: str


class StageChange(BaseModel):
    stage: STAGES
    progress: int = Field(ge=0, le=3)


class ChangeProposal(BaseModel):
    instructions: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RecipeProposal(BaseModel):
    recipe: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ProposalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    handler_id: str
    reason: str


class Rejection(BaseModel):
    reason: str = Field(min_length=1)


class ImproverCall(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)


def round_row(connection: Connection, round_id: str) -> Row:
    row = (
        connection.execute(select(rounds).where(rounds.c.round_id == round_id))
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, f"There is no round {round_id}.")
    return row


def round_spent(connection: Connection, round_id: str) -> float:
    """What the round has spent: the improver's calls, its evaluations and the judge."""
    calls = connection.execute(
        select(func.coalesce(func.sum(improver_calls.c.cost), 0.0)).where(
            improver_calls.c.round_id == round_id
        )
    ).scalar()
    run_ids = select(runs.c.run_id).where(
        runs.c.round_id == round_id, runs.c.kind == "evaluation"
    )
    claims_cost = connection.execute(
        select(func.coalesce(func.sum(model_calls.c.cost), 0.0)).where(
            model_calls.c.run_id.in_(run_ids)
        )
    ).scalar()
    judged = connection.execute(
        select(func.coalesce(func.sum(grades.c.judge_cost), 0.0)).where(
            grades.c.run_id.in_(run_ids)
        )
    ).scalar()
    return float(calls or 0) + float(claims_cost or 0) + float(judged or 0)


def round_allows_claims(connection: Connection, round_id: str) -> None:
    """Refuse a new claim in a round's evaluation once it has stopped or overspent."""
    row = round_row(connection, round_id)
    if row["status"] == "stopped":
        raise HTTPException(409, f"Round {round_id} has been stopped.")
    limit = json.loads(row["settings"])["round_limit"]
    if round_spent(connection, round_id) >= limit:
        raise HTTPException(
            409, f"Round {round_id} has spent its limit of {money(limit)}."
        )


def cut_short(connection: Connection, run: Row, status: str) -> bool:
    """Say whether a round's evaluation was cut short by the limit or a stop."""
    if status != "finished" or not run["round_id"] or run["kind"] != "evaluation":
        return False
    row = round_row(connection, run["round_id"])
    undecided = connection.execute(
        select(claims.c.claim_id).where(
            claims.c.evaluation_id == run["run_id"], claims.c.decision.is_(None)
        )
    ).first()
    limit = json.loads(row["settings"])["round_limit"]
    spent = round_spent(connection, run["round_id"])
    return undecided is not None and (row["status"] == "stopped" or spent >= limit)


def whole_set_runs(connection: Connection, version: int) -> dict[str, list[str]]:
    """The finished whole-set evaluations that rounds ran on a version, by set."""
    found: dict[str, list[str]] = {"evaluation": [], "held_out": []}
    rows = connection.execute(
        select(runs.c.run_id, runs.c.evaluation_set)
        .where(
            runs.c.kind == "evaluation",
            runs.c.status == "finished",
            runs.c.tiny == 0,
            runs.c.round_id.is_not(None),
            runs.c.version == version,
        )
        .order_by(runs.c.id)
    ).all()
    for run_id, evaluation_set in rows:
        found[evaluation_set].append(run_id)
    return found


def run_scores(connection: Connection, run_id: str) -> dict[str, Any]:
    view = evaluation_view(connection, run_id, False)
    return {name: view[name] for name in improvement.MEASURES}


def version_scores(connection: Connection, version: int) -> dict[str, Any]:
    """A version's scores: the averages of the whole-set evaluations its rounds ran."""
    found = whole_set_runs(connection, version)
    return {
        name: improvement.average([run_scores(connection, run_id) for run_id in ids])
        if ids
        else None
        for name, ids in found.items()
    }


def evaluations_needed(connection: Connection, version: int) -> dict[str, int]:
    """How many first evaluations a version still needs on each set."""
    found = whole_set_runs(connection, version)
    return {name: max(FIRST_RUNS[name] - len(found[name]), 0) for name in FIRST_RUNS}


def proposal_view(
    connection: Connection, row: Row, start_version: int, recipe_version: int
) -> dict[str, Any]:
    view = {
        "id": row["id"],
        "kind": row["kind"],
        "reason": row["reason"],
        "status": row["status"],
        "created_at": row["created_at"],
        "decided_by": handler_name(connection, row["decided_by"]),
        "decided_at": row["decided_at"],
        "rejection_reason": row["rejection_reason"],
    }
    if row["kind"] == "change":
        before = versions.configuration(connection, start_version) or {}
        return view | {
            "model": {"before": before.get("model"), "after": row["model"]},
            "reasoning_effort": {
                "before": before.get("reasoning_effort"),
                "after": row["reasoning_effort"],
            },
            "diff": versions.diff_lines(
                before.get("instructions", ""), row["instructions"]
            ),
        }
    before = versions.recipe(connection, recipe_version) or {}
    return view | {"diff": versions.diff_lines(before.get("recipe", ""), row["recipe"])}


def round_view(connection: Connection, round_id: str) -> dict[str, Any]:
    row = round_row(connection, round_id)
    saved = json.loads(row["settings"])
    found = (
        connection.execute(
            select(proposals)
            .where(proposals.c.round_id == round_id)
            .order_by(proposals.c.id)
        )
        .mappings()
        .all()
    )
    listed = [
        proposal_view(connection, item, row["start_version"], row["recipe_version"])
        for item in found
    ]
    return {
        "round_id": round_id,
        "status": row["status"],
        "stage": row["stage"],
        "progress": row["progress"],
        "recipe_version": row["recipe_version"],
        "settings": {
            key: saved[key]
            for key in ["objectives", "noise", "tolerance", "round_limit"]
        }
        | {"improver_concurrency": saved["improver_concurrency"]},
        "start_version": row["start_version"],
        "version_tried": row["version_tried"],
        "reason": row["reason"],
        "before": json.loads(row["before_scores"]) if row["before_scores"] else None,
        "after": json.loads(row["after_scores"]) if row["after_scores"] else None,
        "verdict": row["verdict"],
        "why": row["why"],
        "started_by": handler_name(connection, row["started_by"]),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "spent": round_spent(connection, round_id),
        "limit": saved["round_limit"],
        "proposals": listed,
        "pending": next((item for item in listed if item["status"] == "pending"), None),
        "evaluations_needed": evaluations_needed(connection, row["start_version"]),
        "thread_id": row["thread_id"],
    }


@app.get("/api/rounds", operation_id="list_rounds")
def list_rounds() -> list[dict[str, Any]]:
    """List the improver's rounds, newest first."""
    with db.connect() as connection:
        ids = list(
            connection.execute(
                select(rounds.c.round_id).order_by(rounds.c.started_at.desc())
            ).scalars()
        )
        return [round_view(connection, round_id) for round_id in ids]


@app.get("/api/rounds/{round_id}", operation_id="get_round")
def get_round(round_id: str) -> dict[str, Any]:
    """Get a round with its stage, its proposals, its scores and what it has spent."""
    with db.connect() as connection:
        return round_view(connection, round_id)


@app.post("/api/rounds", operation_id="start_round")
def start_round(handler: auth.Senior) -> dict[str, Any]:
    """Ask the agent's service to start a round of the improver, as a senior handler."""
    return agent_service.forward("/api/rounds", {"started_by": handler["handler_id"]})


@app.post("/api/rounds/create", operation_id="create_round")
def create_round(new: NewRound) -> dict[str, Any]:
    """Create a round and its first stretch of work, refused while anything is open."""
    with db.connect() as connection:
        if connection.execute(
            select(rounds.c.round_id).where(rounds.c.status.in_(ROUND_OPEN))
        ).first():
            raise HTTPException(409, "A round is already open.")
        round_id = f"ROUND-{uuid4().hex[:8]}"
        stretch_id = new_run(connection, "improver", 1, round_id=round_id)
        current = versions.current(connection)
        connection.execute(
            insert(rounds).values(
                round_id=round_id,
                status="working",
                stage="improver_working",
                recipe_version=versions.current(connection, "current_recipe"),
                settings=json.dumps(settings.read_all(connection)),
                start_version=current,
                started_by=new.started_by,
                started_at=now_ms(),
                thread_id=f"{round_id}-{uuid4().hex[:8]}",
            )
        )
        return round_view(connection, round_id) | {"stretch_id": stretch_id}


@app.post("/api/rounds/{round_id}/stretches", operation_id="begin_stretch")
def begin_stretch(round_id: str) -> dict[str, str]:
    """Begin a new stretch of the improver's work on a round that was waiting."""
    with db.connect() as connection:
        row = round_row(connection, round_id)
        if row["status"] != "waiting":
            raise HTTPException(409, f"Round {round_id} is not waiting for a handler.")
        stretch_id = new_run(connection, "improver", 1, round_id=round_id)
        connection.execute(
            update(rounds).where(rounds.c.round_id == round_id).values(status="working")
        )
    return {"stretch_id": stretch_id}


def working_round(connection: Connection, round_id: str) -> Row:
    row = round_row(connection, round_id)
    if row["status"] != "working":
        raise HTTPException(409, f"Round {round_id} is {row['status']}.")
    return row


@app.put("/api/rounds/{round_id}/stage", operation_id="set_round_stage")
def set_round_stage(round_id: str, change: StageChange) -> dict[str, Any]:
    """Record where a round has got to."""
    with db.connect() as connection:
        working_round(connection, round_id)
        connection.execute(
            update(rounds)
            .where(rounds.c.round_id == round_id)
            .values(stage=change.stage, progress=change.progress)
        )
        return round_view(connection, round_id)


def pending_proposal(connection: Connection, round_id: str, **values: Any) -> int:
    result = connection.execute(
        insert(proposals).values(
            round_id=round_id, status="pending", created_at=now_ms(), **values
        )
    )
    return int(result.inserted_primary_key[0])  # pyright: ignore[reportOptionalSubscript]


@app.post(
    "/api/rounds/{round_id}/change-proposals", operation_id="record_change_proposal"
)
def record_change_proposal(round_id: str, proposal: ChangeProposal) -> dict[str, Any]:
    """Record a change the improver proposes, and wait for a handler."""
    allowed = {m["model"]: m["reasoning_efforts"] for m in versions.models_allowed()}
    if proposal.reasoning_effort not in allowed.get(proposal.model, []):
        raise HTTPException(
            422, f"{proposal.model} at {proposal.reasoning_effort} isn't allowed."
        )
    with db.connect() as connection:
        working_round(connection, round_id)
        pending_proposal(connection, round_id, kind="change", **proposal.model_dump())
        connection.execute(
            update(rounds)
            .where(rounds.c.round_id == round_id)
            .values(status="waiting", stage="change_waiting", progress=0)
        )
        return round_view(connection, round_id)


@app.post(
    "/api/rounds/{round_id}/recipe-proposals", operation_id="record_recipe_proposal"
)
def record_recipe_proposal(round_id: str, proposal: RecipeProposal) -> dict[str, Any]:
    """Record a recipe the improver proposes, and wait for a handler."""
    with db.connect() as connection:
        working_round(connection, round_id)
        pending_proposal(connection, round_id, kind="recipe", **proposal.model_dump())
        connection.execute(
            update(rounds)
            .where(rounds.c.round_id == round_id)
            .values(status="waiting", stage="recipe_waiting", progress=0)
        )
        return round_view(connection, round_id)


@app.post(
    "/api/proposals/{proposal_id}/decision", operation_id="record_proposal_decision"
)
def record_proposal_decision(
    proposal_id: int, decision: ProposalDecision
) -> dict[str, Any]:
    """Record a handler's decision on a proposal, making its version if approved."""
    with db.connect() as connection:
        found = (
            connection.execute(select(proposals).where(proposals.c.id == proposal_id))
            .mappings()
            .first()
        )
        if found is None:
            raise HTTPException(404, f"There is no proposal {proposal_id}.")
        row = round_row(connection, found["round_id"])
        if row["status"] not in ROUND_OPEN:
            raise HTTPException(409, f"Round {row['round_id']} is {row['status']}.")
        if found["status"] != "pending":
            raise HTTPException(409, f"Proposal {proposal_id} has been decided.")
        approved = decision.decision == "approve"
        name = record(connection, "handler", decision.handler_id)["name"]
        when = now_ms()
        connection.execute(
            update(proposals)
            .where(proposals.c.id == proposal_id)
            .values(
                status="approved" if approved else "rejected",
                decided_by=decision.handler_id,
                decided_at=when,
                rejection_reason=None if approved else decision.reason,
            )
        )
        stage = "improver_working"
        if approved:
            stage = make_version(connection, found, row, name, when)
        connection.execute(
            update(rounds)
            .where(rounds.c.round_id == row["round_id"])
            .values(stage=stage, progress=0)
        )
        return round_view(connection, row["round_id"])


def make_version(
    connection: Connection, proposal: Row, row: Row, approver: str, when: str
) -> str:
    """Make the version or recipe a proposal asked for, and return the next stage."""
    if proposal["kind"] == "recipe":
        version = (
            connection.execute(select(func.max(recipes.c.version))).scalar_one() + 1
        )
        connection.execute(
            insert(recipes).values(
                version=version,
                recipe=proposal["recipe"],
                reason=proposal["reason"],
                proposed_by="The improver",
                approved_by=approver,
                approved_at=when,
                round_id=row["round_id"],
            )
        )
        connection.execute(
            update(improvement_state)
            .where(improvement_state.c.key == "current_recipe")
            .values(value=version)
        )
        return "improver_working"
    version = (
        connection.execute(select(func.max(configurations.c.version))).scalar_one() + 1
    )
    connection.execute(
        insert(configurations).values(
            version=version,
            instructions=proposal["instructions"],
            model=proposal["model"],
            reasoning_effort=proposal["reasoning_effort"],
            reason=proposal["reason"],
            proposed_by="The improver",
            approved_by=approver,
            approved_at=when,
            round_id=row["round_id"],
        )
    )
    before = version_scores(connection, row["start_version"])["held_out"]
    connection.execute(
        update(rounds)
        .where(rounds.c.round_id == row["round_id"])
        .values(
            version_tried=version,
            reason=proposal["reason"],
            before_scores=json.dumps(before),
        )
    )
    return "evaluating"


def proposal_round(proposal_id: int) -> str:
    """The round a proposal belongs to."""
    with db.connect() as connection:
        found = connection.execute(
            select(proposals.c.round_id).where(proposals.c.id == proposal_id)
        ).scalar()
    if found is None:
        raise HTTPException(404, f"There is no proposal {proposal_id}.")
    return found


@app.post("/api/proposals/{proposal_id}/approve", operation_id="approve_proposal")
def approve_proposal(proposal_id: int, handler: auth.Senior) -> dict[str, Any]:
    """Ask the agent's service to approve a proposal, as a senior handler."""
    return agent_service.forward(
        f"/api/proposals/{proposal_id}/approve",
        {
            "handler_id": handler["handler_id"],
            "round_id": proposal_round(proposal_id),
            "reason": "",
        },
    )


@app.post("/api/proposals/{proposal_id}/reject", operation_id="reject_proposal")
def reject_proposal(
    proposal_id: int, rejection: Rejection, handler: auth.Senior
) -> dict[str, Any]:
    """Ask the agent's service to reject a proposal, with the handler's reason."""
    return agent_service.forward(
        f"/api/proposals/{proposal_id}/reject",
        {
            "handler_id": handler["handler_id"],
            "round_id": proposal_round(proposal_id),
            "reason": rejection.reason,
        },
    )


@app.post("/api/rounds/{round_id}/stop", operation_id="stop_round")
def stop_round(round_id: str, handler: auth.Senior) -> dict[str, Any]:
    """Stop an open round as a senior handler, and tell the agent's service."""
    with db.connect() as connection:
        row = round_row(connection, round_id)
        if row["status"] not in ROUND_OPEN:
            raise HTTPException(409, f"Round {round_id} is not open.")
        connection.execute(
            update(rounds)
            .where(rounds.c.round_id == round_id)
            .values(status="stopped", stage="stopped", finished_at=now_ms())
        )
        connection.execute(
            update(proposals)
            .where(proposals.c.round_id == round_id, proposals.c.status == "pending")
            .values(status="withdrawn")
        )
        view = round_view(connection, round_id)
    agent_service.forward(f"/api/rounds/{round_id}/stop")
    return view


@app.post("/api/rounds/{round_id}/model-calls", operation_id="record_improver_call")
def record_improver_call(round_id: str, call: ImproverCall) -> dict[str, str]:
    """Record the tokens and cost of one of the improver's model calls."""
    with db.connect() as connection:
        working_round(connection, round_id)
        connection.execute(
            insert(improver_calls).values(
                round_id=round_id, created_at=now_ms(), **call.model_dump()
            )
        )
    return {"status": "recorded"}


def max_cost_per_claim(connection: Connection, model: str) -> float | None:
    """The highest cost a claim has had in an evaluation run on a model."""
    found = connection.execute(
        select(runs.c.run_id).where(runs.c.kind == "evaluation", runs.c.model == model)
    ).scalars()
    highest = None
    for run_id in list(found):
        copies = connection.execute(
            select(func.count())
            .select_from(run_claims)
            .where(run_claims.c.run_id == run_id)
        ).scalar_one()
        total = connection.execute(
            select(func.coalesce(func.sum(model_calls.c.cost), 0.0)).where(
                model_calls.c.run_id == run_id
            )
        ).scalar_one()
        if copies:
            highest = max(highest or 0.0, total / copies)
    return highest


def cost_per_claim_estimate(connection: Connection, version: int) -> float:
    """The most a claim is likely to cost on a version's model."""
    config = version_or_404(connection, version)
    seen = max_cost_per_claim(connection, config["model"])
    if seen is not None:
        return seen
    now = versions.configuration(connection, versions.current(connection)) or config
    baseline = max_cost_per_claim(connection, now["model"]) or 0.0
    prices = {m["model"]: m for m in versions.models_allowed()}
    here, there = prices.get(now["model"]), prices.get(config["model"])
    if not here or not there or not here["input_price"] or not here["output_price"]:
        return baseline
    ratio = max(
        there["input_price"] / here["input_price"],
        there["output_price"] / here["output_price"],
    )
    return baseline * ratio


@app.get(
    "/api/rounds/{round_id}/estimates/{version}", operation_id="estimate_evaluation"
)
def estimate_evaluation(round_id: str, version: int) -> dict[str, Any]:
    """Estimate what a version's three evaluations will cost, beside what is left."""
    with db.connect() as connection:
        row = round_row(connection, round_id)
        left = json.loads(row["settings"])["round_limit"] - round_spent(
            connection, round_id
        )
        claims_in_set = connection.execute(
            select(func.count())
            .select_from(eval_claims)
            .where(eval_claims.c.eval_set == "held_out")
        ).scalar_one()
        each = claims_in_set * cost_per_claim_estimate(connection, version)
    return {"evaluations": 3, "each": each, "estimate": 3 * each, "left": left}


def held_out_runs(connection: Connection, round_id: str, version: int) -> list[Any]:
    return list(
        connection.execute(
            select(runs.c.run_id, runs.c.evaluation_set, runs.c.status)
            .where(
                runs.c.round_id == round_id,
                runs.c.kind == "evaluation",
                runs.c.version == version,
                runs.c.tiny == 0,
            )
            .order_by(runs.c.id)
        ).all()
    )


def roll_back(why: str) -> dict[str, Any]:
    return {"verdict": "rolled_back", "why": why}


def verdict_for(connection: Connection, row: Row) -> tuple[dict[str, Any], Any]:
    """Keep or roll back the version a round tried, by the rule and its settings."""
    version = row["version_tried"]
    found = held_out_runs(connection, row["round_id"], version)
    done = [r for r in found if r.status == "finished"]
    sets = [r.evaluation_set for r in done]
    if sets.count("evaluation") < FIRST_RUNS["evaluation"] or (
        sets.count("held_out") < FIRST_RUNS["held_out"]
    ):
        return roll_back("its evaluations were stopped or never finished"), None
    held = improvement.average(
        [
            run_scores(connection, r.run_id)
            for r in done
            if r.evaluation_set == "held_out"
        ]
    )
    before = json.loads(row["before_scores"]) if row["before_scores"] else None
    if before is None:
        return roll_back("there were no scores to compare it with"), held
    kept, why = improvement.keep_or_roll_back(held, before, json.loads(row["settings"]))
    return {"verdict": "kept" if kept else "rolled_back", "why": why}, held


def conclude(connection: Connection, row: Row, unfinished: str | None = None) -> None:
    """Record a round's verdict, and make a kept version the current one."""
    if unfinished:
        result, held = roll_back(unfinished), None
    else:
        result, held = verdict_for(connection, row)
    connection.execute(
        update(rounds)
        .where(rounds.c.round_id == row["round_id"])
        .values(
            verdict=result["verdict"],
            why=result["why"],
            after_scores=json.dumps(held) if held else None,
            stage="verdict",
            progress=0,
        )
    )
    if result["verdict"] == "kept":
        connection.execute(
            update(improvement_state)
            .where(improvement_state.c.key == "current_version")
            .values(value=row["version_tried"])
        )


@app.post("/api/rounds/{round_id}/verdict", operation_id="conclude_round")
def conclude_round(round_id: str) -> dict[str, Any]:
    """Keep or roll back the version a round tried, by the business's rule."""
    with db.connect() as connection:
        row = working_round(connection, round_id)
        if row["version_tried"] is None or row["verdict"] is not None:
            raise HTTPException(409, f"Round {round_id} has no version to judge.")
        conclude(connection, row)
        return round_view(connection, round_id)


@app.post("/api/rounds/{round_id}/finish", operation_id="finish_round")
def finish_round(round_id: str) -> dict[str, Any]:
    """Finish a round, rolling back a version it never got to evaluate."""
    with db.connect() as connection:
        row = working_round(connection, round_id)
        if row["version_tried"] is not None and row["verdict"] is None:
            conclude(
                connection,
                row,
                unfinished="the round could not afford to evaluate it",
            )
        connection.execute(
            update(rounds)
            .where(rounds.c.round_id == round_id)
            .values(
                status="finished", stage="finished", progress=0, finished_at=now_ms()
            )
        )
        return round_view(connection, round_id)


def handler_reasons(connection: Connection, evaluation_set: str) -> dict[str, Any]:
    return {
        claim_id: json.loads(data)
        for claim_id, data in connection.execute(
            select(eval_decisions.c.claim_id, eval_decisions.c.data).where(
                eval_decisions.c.eval_set == evaluation_set
            )
        ).all()
    }


def beside_handler(connection: Connection, run: Row, picked: list[str]) -> list[dict]:
    """Evaluation-set claims with the agent's answer beside the handler's reason."""
    key = handler_reasons(connection, "evaluation")
    summaries = {
        claim_id: json.loads(data)["summary"]
        for claim_id, data in connection.execute(
            select(eval_claims.c.claim_id, eval_claims.c.data).where(
                eval_claims.c.eval_set == "evaluation"
            )
        ).all()
    }
    shown = []
    for item in evaluation_rows(connection, run):
        if item["claim_id"] not in picked:
            continue
        expected = key[item["source_id"]]
        grade = item["grade"]
        shown.append(
            {
                "claim": item["source_id"],
                "summary": summaries[item["source_id"]],
                "agent": item["agent"] | {"rationale": item["reason"]},
                "handler": {
                    "decision": expected["decision"],
                    "payout": expected["payout"],
                    "reason": expected["reason"],
                    "reference": expected["reference"],
                },
                "judge": grade
                and {
                    "grade": round(grade["score"], 2),
                    "letter": grade["letter"],
                    "same_grounds": grade["same_grounds"] >= YES,
                    "same_section": grade["same_section"] >= YES,
                },
            }
        )
    return shown


def evaluation_set_view(connection: Connection, version: int) -> dict[str, Any] | None:
    """The wrong claims, and the five right ones explained least well."""
    ids = whole_set_runs(connection, version)["evaluation"]
    if not ids:
        return None
    run = run_row(connection, ids[-1])
    rows = evaluation_rows(connection, run)
    wrong = [r["claim_id"] for r in rows if not r["right"]]
    weakest = sorted(
        (r for r in rows if r["right"]),
        key=lambda r: r["grade"]["score"] if r["grade"] else 4.0,
    )[:5]
    return {
        "wrong": beside_handler(connection, run, wrong),
        "explained_least_well": beside_handler(
            connection, run, [r["claim_id"] for r in weakest]
        ),
    }


@app.get("/api/rounds/{round_id}/view", operation_id="get_improver_view")
def get_improver_view(round_id: str) -> dict[str, Any]:
    """Get what the improver may see: its version, scores, mistakes and limits."""
    with db.connect() as connection:
        row = round_row(connection, round_id)
        saved = json.loads(row["settings"])
        current = versions.current(connection)
        config = versions.configuration(connection, current) or {}
        earlier = (
            connection.execute(
                select(rounds)
                .where(rounds.c.round_id != round_id, rounds.c.verdict.is_not(None))
                .order_by(rounds.c.started_at)
            )
            .mappings()
            .all()
        )
        return {
            "current_version": {
                key: config[key]
                for key in ["version", "instructions", "model", "reasoning_effort"]
            },
            "scores": version_scores(connection, current),
            "evaluation_set": evaluation_set_view(connection, current),
            "earlier_rounds": [
                {
                    "round": index + 1,
                    "recipe_version": item["recipe_version"],
                    "version": item["version_tried"],
                    "reason": item["reason"],
                    "verdict": item["verdict"],
                    "why": item["why"],
                    "held_out_before": json.loads(item["before_scores"] or "null"),
                    "held_out_after": json.loads(item["after_scores"] or "null"),
                }
                for index, item in enumerate(earlier)
            ],
            "objectives_on_the_held_out_set": saved["objectives"],
            "noise": saved["noise"],
            "tolerance": saved["tolerance"],
            "rule": improvement.RULE,
            "spending_left": round(
                saved["round_limit"] - round_spent(connection, round_id), 4
            ),
            "models_allowed": versions.models_allowed(),
        }
