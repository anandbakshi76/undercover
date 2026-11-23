import json

import httpx
import pytest
from sqlalchemy import delete, func, insert, select

from backend import db
from backend.db import claims, eval_claims, eval_decisions


def create(client, evaluation_set="evaluation", tiny=False, concurrency=3, model="m"):
    body = {
        "concurrency": concurrency,
        "evaluation_set": evaluation_set,
        "tiny": tiny,
        "model": model,
        "reasoning_effort": "low",
    }
    return client.post("/api/runs/evaluation", json=body)


def finish(client, run_id, status="finished"):
    return client.post(f"/api/runs/{run_id}/finish", json={"status": status}).json()


def dispatch(client):
    body = {"concurrency": 3, "model": "m", "reasoning_effort": "low"}
    return client.post("/api/runs/dispatch", json=body)


def start(client, run_id, claim_id):
    return client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})


def decide(client, claim_id, decision, payout: float = 0):
    body = {
        "decision": decision,
        "payout": payout,
        "reason": "The agent's reason.",
        "policy_section": "Section 3",
    }
    return client.post(f"/api/claims/{claim_id}/decision", json=body)


def usage(client, claim_id, run_id, cost, seconds):
    call = {"run_id": run_id, "prompt_tokens": 10, "completion_tokens": 5}
    client.post(f"/api/claims/{claim_id}/model-calls", json=call | {"cost": cost})
    work = {
        "run_id": run_id,
        "started_at": "2026-01-01T10:00:00.000",
        "seconds": seconds,
    }
    client.post(f"/api/claims/{claim_id}/work-time", json=work)


def counts(table, **where):
    with db.connect() as connection:
        query = select(func.count()).select_from(table)
        for name, value in where.items():
            query = query.where(table.c[name] == value)
        return connection.execute(query).scalar()


def test_seeding_stores_both_sets_and_the_decisions_out_of_the_queue(client):
    for name in ["evaluation", "held_out"]:
        assert counts(eval_claims, eval_set=name) == 25
        assert counts(eval_decisions, eval_set=name) == 25
        assert counts(eval_claims, eval_set=name, tiny=1) == 5
    assert len(client.get("/api/claims").json()) == 25
    with db.connect() as connection:
        held_out = set(
            connection.execute(
                select(eval_claims.c.claim_id).where(
                    eval_claims.c.eval_set == "held_out"
                )
            ).scalars()
        )
    assert not held_out & {c["claim_id"] for c in client.get("/api/claims").json()}


def test_an_evaluation_makes_copies_with_new_ids_written_into_their_records(client):
    run = create(client).json()
    run_id = run["run_id"]
    assert run["kind"] == "evaluation"
    assert run_id.startswith("EVAL-")
    assert run["evaluation_set"] == "evaluation"
    assert run["tiny"] is False
    assert len(run["claims_worked"]) == 25
    first = client.get(f"/api/claims/{run_id}-CLM-1001").json()
    assert first["claim_id"] == f"{run_id}-CLM-1001"
    assert first["evaluation_id"] == run_id
    assert first["status"] == "waiting"
    assert first["story"] == client.get("/api/claims/CLM-1001").json()["story"]


def test_a_tiny_evaluation_works_the_five_tiny_claims(client):
    run = create(client, "held_out", tiny=True).json()
    assert len(run["claims"]) == 5
    assert all("-CLM-11" in item["claim_id"] for item in run["claims"])
    assert run["evaluation_set"] == "held_out"
    assert run["tiny"] is True


def test_every_copy_has_a_policy_and_a_customer_to_read(client):
    for name in ["evaluation", "held_out"]:
        run = create(client, name).json()
        for item in run["claims"]:
            copy = client.get(f"/api/claims/{item['claim_id']}").json()
            assert client.get(f"/api/policies/{copy['policy_id']}").status_code == 200
            history = client.get(f"/api/customers/{copy['customer_id']}/claims")
            assert history.status_code == 200
        finish(client, run["run_id"])


def test_evaluations_leave_the_queue_as_it_was(client):
    before = client.get("/api/claims").json()
    probe = dispatch(client).json()["run_id"]
    to_work = client.get(f"/api/runs/{probe}/claims-to-work").json()
    finish(client, probe)
    for name, tiny in [("evaluation", False), ("held_out", True)]:
        run = create(client, name, tiny).json()
        start(client, run["run_id"], run["claims_worked"][0])
        decide(client, run["claims_worked"][0], "decline")
        finish(client, run["run_id"])
    assert client.get("/api/claims").json() == before
    probe = dispatch(client).json()["run_id"]
    assert client.get(f"/api/runs/{probe}/claims-to-work").json() == to_work


def test_a_run_works_only_its_own_copies(client):
    run = create(client, "held_out", tiny=True).json()
    run_id = run["run_id"]
    todo = client.get(f"/api/runs/{run_id}/claims-to-work").json()
    assert sorted(todo) == sorted(run["claims_worked"])
    assert len(todo) == 5
    start(client, run_id, todo[0])
    assert client.get(f"/api/runs/{run_id}/claims-to-work").json()[0] == todo[0]


def test_only_one_dispatch_or_evaluation_runs_at_a_time(client):
    run_id = create(client, tiny=True).json()["run_id"]
    assert dispatch(client).status_code == 409
    assert create(client).status_code == 409
    finish(client, run_id)
    assert dispatch(client).status_code == 200


def test_get_claim_on_a_copy_holds_nothing_from_the_handlers(client):
    run = create(client).json()
    run_id = run["run_id"]
    with db.connect() as connection:
        rows = connection.execute(select(eval_decisions.c.data)).scalars().all()
    for item in run["claims"]:
        copy = client.get(f"/api/claims/{item['claim_id']}").json()
        steps = client.get(f"/api/claims/{item['claim_id']}/steps").json()
        text = json.dumps([copy, steps])
        assert copy["decision"] is None
        assert copy["payment"] is None
        for data in rows:
            key = json.loads(data)
            assert key["reason"] not in text
            assert key["handler_id"] not in copy.values()
    assert "handler" not in " ".join(
        client.get(f"/api/claims/{run_id}-CLM-1001").json()
    )


def test_a_copy_cannot_be_signed_off_or_run_from_the_claim_page(client):
    run = create(client, tiny=True).json()
    copy = run["claims_worked"][0]
    start(client, run["run_id"], copy)
    decide(client, copy, "approve", 1250)
    assert client.get(f"/api/claims/{copy}").json()["status"] == "awaiting_sign_off"
    refused = client.post(f"/api/claims/{copy}/sign-off", json={"handler_id": "HND-01"})
    assert refused.status_code == 409
    assert "evaluation copy" in refused.json()["detail"]
    assert client.get(f"/api/claims/{copy}").json()["sign_off"] is None
    other = run["claims_worked"][1]
    assert client.post(f"/api/claims/{other}/start-run").status_code == 409


def test_start_evaluation_sends_the_set_tiny_and_n_to_the_service(client, agent):
    body = {"concurrency": 5, "evaluation_set": "held_out", "tiny": True}
    assert client.post("/api/evaluations", json=body).status_code == 200
    assert agent.calls == [("POST", "/api/evaluations", body)]


def test_start_evaluation_checks_its_input_and_the_service(client, agent):
    body = {"concurrency": 5, "evaluation_set": "held_out", "tiny": True}
    assert (
        client.post("/api/evaluations", json=body | {"concurrency": 26}).status_code
        == 422
    )
    assert (
        client.post("/api/evaluations", json=body | {"evaluation_set": "x"}).status_code
        == 422
    )
    assert agent.calls == []
    agent.reply = httpx.Response(
        409, json={"detail": "A dispatch or evaluation is already running."}
    )
    assert client.post("/api/evaluations", json=body).status_code == 409
    agent.down = True
    down = client.post("/api/evaluations", json=body)
    assert down.status_code == 503
    assert down.json()["detail"] == "The agent isn't running."
    assert client.get("/api/evaluations").json() == []


def test_the_evaluation_records_its_set_n_model_and_effort(client):
    run_id = create(client, "held_out", True, 5, "openrouter/free").json()["run_id"]
    listed = client.get("/api/evaluations").json()
    assert [e["run_id"] for e in listed] == [run_id]
    shown = listed[0]
    assert shown["evaluation_set"] == "held_out"
    assert shown["tiny"] is True
    assert shown["concurrency"] == 5
    assert shown["model"] == "openrouter/free"
    assert shown["reasoning_effort"] == "low"
    assert shown["status"] == "running"
    assert "claims" not in shown
    assert shown["claim_count"] == 5


def test_evaluations_are_listed_newest_first_and_only_runs_of_that_kind_open(client):
    first = create(client, tiny=True).json()["run_id"]
    finish(client, first)
    second = create(client, tiny=True).json()["run_id"]
    assert [e["run_id"] for e in client.get("/api/evaluations").json()] == [
        second,
        first,
    ]
    other = dispatch(client)
    assert other.status_code == 409
    finish(client, second)
    dispatched = dispatch(client).json()["run_id"]
    assert client.get(f"/api/evaluations/{dispatched}").status_code == 404
    assert client.get("/api/evaluations/EVAL-0000").status_code == 404


def test_a_stopped_evaluation_shows_as_stopped_with_undecided_claims_as_referrals(
    client,
):
    run_id = create(client, tiny=True).json()["run_id"]
    assert client.post("/api/runs/stop-running").json() == [run_id]
    shown = client.get(f"/api/evaluations/{run_id}").json()
    assert shown["status"] == "stopped"
    assert shown["straight_through"] == 0
    assert all(item["agent"]["decision"] == "refer" for item in shown["claims"])
    assert all(item["agent"]["payout"] == 0 for item in shown["claims"])


@pytest.fixture
def small_key(client):
    """Six claims of the evaluation set with handler decisions chosen by hand."""
    key = {
        "CLM-1001": ("approve", 400.0),
        "CLM-1002": ("approve", 500.0),
        "CLM-1003": ("approve", 300.0),
        "CLM-1004": ("decline", 0.0),
        "CLM-1005": ("approve", 100.0),
        "CLM-1006": ("approve", 250.0),
    }
    with db.connect() as connection:
        data = dict(connection.execute(select(claims.c.claim_id, claims.c.data)).all())
        connection.execute(delete(eval_claims))
        connection.execute(delete(eval_decisions))
        for claim_id, (decision, payout) in key.items():
            connection.execute(
                insert(eval_claims).values(
                    eval_set="evaluation",
                    claim_id=claim_id,
                    tiny=0,
                    data=data[claim_id],
                )
            )
            connection.execute(
                insert(eval_decisions).values(
                    eval_set="evaluation",
                    claim_id=claim_id,
                    decision=decision,
                    payout=payout,
                    data="{}",
                )
            )
    return key


def test_the_measures_match_ones_worked_out_by_hand(client, small_key):
    run_id = create(client).json()["run_id"]
    answers = {
        "CLM-1001": ("approve", 400.5),
        "CLM-1002": ("approve", 650.0),
        "CLM-1003": ("decline", 0.0),
        "CLM-1004": ("approve", 200.0),
        "CLM-1005": ("refer", 0.0),
    }
    for source_id in small_key:
        copy = f"{run_id}-{source_id}"
        start(client, run_id, copy)
        usage(client, copy, run_id, 0.1, 2.0)
        if source_id in answers:
            decision, payout = answers[source_id]
            assert decide(client, copy, decision, payout).status_code == 200
    finished = finish(client, run_id)
    assert finished["status"] == "finished"
    shown = client.get(f"/api/evaluations/{run_id}").json()
    assert shown["claims"]
    assert len(shown["claims"]) == 6
    assert shown["decision_accuracy"] == pytest.approx(2 / 6)
    assert shown["accuracy"] == pytest.approx(1 / 6)
    assert shown["cost_of_mistakes"] == pytest.approx(0.5 + 150 + 1500 + 200 + 50 + 50)
    assert shown["straight_through"] == pytest.approx(4 / 6)
    assert shown["cost_per_claim"] == pytest.approx(0.1)
    assert shown["seconds_per_claim"] == pytest.approx(2.0)
    assert shown["run_seconds"] >= 0
    by_source = {item["source_id"]: item for item in shown["claims"]}
    assert by_source["CLM-1001"]["right"] is True
    assert by_source["CLM-1002"]["right"] is False
    assert by_source["CLM-1002"]["mistake_cost"] == 150
    assert by_source["CLM-1003"]["mistake_cost"] == 1500
    assert by_source["CLM-1004"]["mistake_cost"] == 200
    assert by_source["CLM-1005"]["mistake_cost"] == 50
    assert by_source["CLM-1006"]["agent"] == {"decision": "refer", "payout": 0.0}
    assert by_source["CLM-1006"]["mistake_cost"] == 50
    assert by_source["CLM-1002"]["handler"] == {"decision": "approve", "payout": 500.0}
    assert by_source["CLM-1002"]["claim_id"] == f"{run_id}-CLM-1002"
    listed = client.get("/api/evaluations").json()[0]
    assert listed["accuracy"] == shown["accuracy"]


def test_a_wrongful_approval_costs_its_payout_whether_or_not_it_was_paid(
    client, small_key
):
    run_id = create(client).json()["run_id"]
    copy = f"{run_id}-CLM-1004"
    start(client, run_id, copy)
    decide(client, copy, "approve", 1250)
    held = client.get(f"/api/evaluations/{run_id}").json()
    assert {i["source_id"]: i for i in held["claims"]}["CLM-1004"][
        "mistake_cost"
    ] == 1250
    start(client, run_id, f"{run_id}-CLM-1001")
    decide(client, f"{run_id}-CLM-1001", "approve", 400)
    client.post(f"/api/claims/{run_id}-CLM-1001/payment", json={"amount": 400})
    paid = client.get(f"/api/evaluations/{run_id}").json()
    assert {i["source_id"]: i for i in paid["claims"]}["CLM-1001"]["right"] is True


def test_an_evaluation_with_no_claims_has_empty_measures(client, small_key):
    with db.connect() as connection:
        connection.execute(delete(eval_claims))
    run_id = create(client).json()["run_id"]
    shown = client.get(f"/api/evaluations/{run_id}").json()
    assert shown["claims"] == []
    assert shown["accuracy"] == 0
