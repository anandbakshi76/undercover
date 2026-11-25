import time

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from agent import claim as claim_agent
from agent import main
from agent.tests.stubs import (
    USAGE,
    approve_and_pay,
    call_tool,
    decide,
    new_dispatch,
    new_run,
    stub,
)
from backend import db
from backend.db import model_calls, work_time

ERROR = RuntimeError("model unavailable")


def calls(claim_id):
    with db.connect() as connection:
        return (
            connection.execute(
                select(model_calls).where(model_calls.c.claim_id == claim_id)
            )
            .mappings()
            .all()
        )


def stretches(claim_id):
    with db.connect() as connection:
        return (
            connection.execute(
                select(work_time).where(work_time.c.claim_id == claim_id)
            )
            .mappings()
            .all()
        )


def test_every_model_reply_is_recorded_with_its_tokens_and_cost():
    run_id = new_dispatch()
    model = stub(*approve_and_pay("CLM-1001"))
    claim_agent.work_claim("CLM-1001", run_id, model)
    recorded = calls("CLM-1001")
    assert len(recorded) == 3
    for call in recorded:
        assert call["run_id"] == run_id
        assert call["prompt_tokens"] == USAGE["prompt_tokens"]
        assert call["completion_tokens"] == USAGE["completion_tokens"]
        assert call["cost"] == USAGE["cost"]
    total = main.call("GET", "/api/claims/CLM-1001")
    assert round(total["cost"], 2) == 0.03
    assert total["seconds"] > 0


def test_a_failed_attempt_keeps_its_cost_and_its_time():
    model = stub(call_tool("get_claim", claim_id="CLM-1001"), ERROR, ERROR, ERROR)
    claim_agent.work_claim("CLM-1001", new_run(1), model)
    assert len(calls("CLM-1001")) == 1
    (stretch,) = stretches("CLM-1001")
    assert stretch["seconds"] >= 0


def test_the_wait_for_a_sign_off_is_not_counted():
    saver = InMemorySaver()
    model = stub(*approve_and_pay("CLM-1002", payout=1250.0))
    begun = time.monotonic()
    claim_agent.work_claim("CLM-1002", new_run(1), model, saver)
    time.sleep(0.5)
    main.call("POST", "/api/claims/CLM-1002/sign-off")
    claim_agent.carry_on("CLM-1002", stub(), saver)
    elapsed = time.monotonic() - begun
    first, second = stretches("CLM-1002")
    assert first["started_at"] < second["started_at"]
    assert first["seconds"] + second["seconds"] <= elapsed - 0.5


def test_costs_add_up_under_their_own_run_or_under_none():
    def record(claim_id, run_id, cost):
        body = {"run_id": run_id, "prompt_tokens": 1, "completion_tokens": 1}
        main.call("POST", f"/api/claims/{claim_id}/model-calls", body | {"cost": cost})

    run_a = new_dispatch()
    main.call(
        "POST", f"/api/runs/{run_a}/finish", {"status": "finished", "summary": ""}
    )
    run_b = new_dispatch()
    record("CLM-1001", run_a, 0.5)
    record("CLM-1002", run_a, 0.25)
    record("CLM-1002", run_b, 1.0)
    record("CLM-1002", None, 2.0)
    with db.connect() as connection:
        totals = dict(
            connection.execute(
                select(model_calls.c.run_id, func.sum(model_calls.c.cost)).group_by(
                    model_calls.c.run_id
                )
            ).all()
        )
    assert totals == {run_a: 0.75, run_b: 1.0, None: 2.0}


def test_a_reply_without_usage_records_nothing():
    claim_agent.record_usage("CLM-1001", None, AIMessage(content="Hi"))
    assert calls("CLM-1001") == []


def test_decide_keeps_the_cost_of_a_refused_decision():
    refused = call_tool(
        "ClaimDecision",
        rationale="No.",
        decision="decline",
        payout=50,
        policy_section="Section 3, Trip Delay",
    )
    model = stub(
        AIMessage(content="Decline."),
        refused,
        AIMessage(content="Fix."),
        decide("CLM-1001", "decline"),
    )
    claim_agent.work_claim("CLM-1001", new_run(1), model)
    assert len(calls("CLM-1001")) == 4
