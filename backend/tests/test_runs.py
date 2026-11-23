import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import db, main


def decline(claim_id):
    main.record_decision(
        claim_id,
        main.Decision(
            decision="decline",
            payout=0,
            reason="Not covered.",
            policy_section="Section 9",
        ),
    )


def new_run():
    with db.connect() as connection:
        return main.new_run(connection, "claim", 3)


def dispatch(client, concurrency=3):
    body = {"concurrency": concurrency, "model": "m", "reasoning_effort": "low"}
    return client.post("/api/runs/dispatch", json=body)


def start(client, run_id, claim_id):
    return client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})


def release(client, claim_id, problem="The model timed out."):
    return client.post(f"/api/claims/{claim_id}/release", json={"problem": problem})


def status(client, claim_id):
    return client.get(f"/api/claims/{claim_id}").json()["status"]


def finish(client, run_id):
    end = {"status": "finished"}
    return client.post(f"/api/runs/{run_id}/finish", json=end)


def test_a_dispatch_records_n_the_model_and_the_effort(client):
    run = dispatch(client, 7).json()
    assert run["kind"] == "dispatch"
    assert run["concurrency"] == 7
    assert run["model"] == "m"
    assert run["reasoning_effort"] == "low"
    assert run["status"] == "running"
    assert run["claims_worked"] == []
    again = client.get(f"/api/runs/{run['run_id']}").json()
    assert again["run_id"] == run["run_id"]


def test_only_one_dispatch_runs_at_a_time(client):
    first = dispatch(client).json()
    refused = dispatch(client)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "A dispatch or evaluation is already running."
    finish(client, first["run_id"])
    assert dispatch(client).status_code == 200


def test_n_is_from_one_to_twenty_five(client):
    assert dispatch(client, 0).status_code == 422
    assert dispatch(client, 26).status_code == 422
    assert dispatch(client, 25).status_code == 200


def test_a_run_has_no_cap_on_its_claims(client):
    run_id = new_run()
    for claim_id in ["CLM-1001", "CLM-1002", "CLM-1003", "CLM-1004"]:
        assert start(client, run_id, claim_id).json()["status"] == "in_progress"
    assert len(client.get(f"/api/runs/{run_id}").json()["claims_worked"]) == 4


def test_a_claim_in_progress_joins_a_later_run_with_its_thread_kept(client):
    first, second = new_run(), new_run()
    started = start(client, first, "CLM-1001").json()
    joined = start(client, second, "CLM-1001").json()
    assert joined["thread_id"] == started["thread_id"]
    assert joined["run_id"] == first
    assert joined["attempts"] == 0
    assert client.get(f"/api/runs/{second}").json()["claims_worked"] == ["CLM-1001"]
    start(client, second, "CLM-1001")
    assert client.get(f"/api/runs/{second}").json()["claims_worked"] == ["CLM-1001"]


def test_the_claims_to_work_are_those_in_progress_then_those_waiting(client):
    run_id = new_run()
    start(client, run_id, "CLM-1005")
    start(client, run_id, "CLM-1003")
    decline("CLM-1003")
    start(client, run_id, "CLM-1002")
    release(client, "CLM-1002")
    for _ in range(2):
        start(client, run_id, "CLM-1004")
        release(client, "CLM-1004")
    todo = client.get(f"/api/runs/{run_id}/claims-to-work").json()
    assert todo[0] == "CLM-1005"
    assert "CLM-1003" not in todo
    assert "CLM-1004" not in todo
    assert "CLM-1002" in todo
    assert len(todo) == 23
    queue = {c["claim_id"]: c for c in client.get("/api/claims").json()}
    waiting = todo[1:]
    assert waiting == sorted(waiting, key=lambda cid: (queue[cid]["submitted_on"], cid))
    assert client.get("/api/runs/RUN-0000/claims-to-work").status_code == 404


def test_a_failed_claim_goes_back_and_can_be_retried(client):
    run_id = new_run()
    start(client, run_id, "CLM-1001")
    released = release(client, "CLM-1001").json()
    assert released["status"] == "waiting"
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert steps[-1]["kind"] == "error"
    assert steps[-1]["text"] == ("The model timed out. The claim is back in the queue.")
    assert start(client, run_id, "CLM-1001").status_code == 200
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["claims_worked"] == ["CLM-1001"]


def test_a_claim_that_fails_twice_is_left_for_a_handler(client):
    run_id = new_run()
    start(client, run_id, "CLM-1001")
    release(client, "CLM-1001")
    start(client, run_id, "CLM-1001")
    assert release(client, "CLM-1001").json()["status"] == "needs_handler"
    last = client.get("/api/claims/CLM-1001/steps").json()[-1]
    assert last["text"].endswith("It has failed twice and is left for a handler.")
    refused = start(client, new_run(), "CLM-1001")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Claim CLM-1001 is left for a handler."
    queue = {c["claim_id"]: c["status"] for c in client.get("/api/claims").json()}
    assert queue["CLM-1001"] == "needs_handler"


def test_releasing_a_decided_claim_changes_nothing(client):
    run_id = new_run()
    start(client, run_id, "CLM-1001")
    decline("CLM-1001")
    assert release(client, "CLM-1001").json()["status"] == "decided"
    assert client.get("/api/claims/CLM-1001/steps").json() == []


def test_steps_from_every_attempt_are_kept(client):
    run_id = new_run()
    start(client, run_id, "CLM-1001")
    client.post("/api/claims/CLM-1001/steps", json={"kind": "reply", "text": "One."})
    release(client, "CLM-1001")
    start(client, run_id, "CLM-1001")
    client.post("/api/claims/CLM-1001/steps", json={"kind": "reply", "text": "Two."})
    texts = [s["text"] for s in client.get("/api/claims/CLM-1001/steps").json()]
    assert texts == [
        "One.",
        "The model timed out. The claim is back in the queue.",
        "Two.",
    ]


def test_starting_a_claim_needs_a_running_run(client):
    run_id = new_run()
    finish(client, run_id)
    refused = start(client, run_id, "CLM-1002")
    assert refused.status_code == 409
    assert refused.json()["detail"] == f"Run {run_id} has finished."
    assert start(client, "RUN-0000", "CLM-1002").status_code == 404
    assert start(client, new_run(), "CLM-0000").status_code == 404


def test_finishing_a_run(client):
    run_id = new_run()
    run = client.post(f"/api/runs/{run_id}/finish", json={"status": "finished"}).json()
    assert run["status"] == "finished"
    assert run["summary"]["approved"] == 0
    assert run["finished_at"] is not None


def test_starting_a_claim_gives_it_a_thread_and_a_repeat_start_returns_it(client):
    run_id = new_run()
    first = start(client, run_id, "CLM-1001").json()
    assert first["thread_id"] == f"CLM-1001-{run_id}-1"
    again = start(client, run_id, "CLM-1001")
    assert again.status_code == 200
    assert again.json()["thread_id"] == first["thread_id"]
    assert client.get(f"/api/runs/{run_id}").json()["claims_worked"] == ["CLM-1001"]
    release(client, "CLM-1001")
    assert start(client, run_id, "CLM-1001").json()["thread_id"].endswith("-2")


def test_releasing_an_approved_claim_changes_nothing(client):
    run_id = new_run()
    start(client, run_id, "CLM-1001")
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "approve",
            "payout": 420.0,
            "reason": "Covered.",
            "policy_section": "Section 3",
        },
    )
    claim = release(client, "CLM-1001").json()
    assert claim["status"] == "in_progress"
    assert claim["attempts"] == 0


def test_starting_a_claim_run_starts_no_agent(client, agent):
    response = client.post("/api/claims/CLM-1001/start-run").json()
    assert response["status"] == "in_progress"
    assert status(client, "CLM-1001") == "in_progress"
    run = client.get(f"/api/runs/{response['run_id']}").json()
    assert run["kind"] == "claim"
    assert client.post("/api/claims/CLM-1001/start-run").status_code == 409
    assert client.post("/api/claims/CLM-0000/start-run").status_code == 404
    assert agent.calls == []


def test_run_agent_asks_the_agents_service(client, agent):
    response = client.post("/api/claims/CLM-1001/run")
    assert response.json() == {"status": "started", "run_id": "RUN-1"}
    assert agent.calls == [("POST", "/api/claims/CLM-1001/run", None)]


def test_the_agents_refusals_come_through(client, agent):
    agent.reply = httpx.Response(409, json={"detail": "Claim CLM-1001 is not waiting."})
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 409
    assert response.json()["detail"] == "Claim CLM-1001 is not waiting."


def test_running_needs_the_agent_to_be_up(client, agent):
    agent.down = True
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 503
    assert response.json()["detail"] == "The agent isn't running."
    assert status(client, "CLM-1001") == "waiting"


def test_the_instructions_are_the_claim_agents(client):
    instructions = client.get("/api/instructions").json()
    assert list(instructions) == ["claim"]
    assert instructions["claim"].startswith("# Working a claim")


def test_a_restart_leaves_claims_in_progress_and_runs_running(client):
    run_id = new_run()
    start(client, run_id, "CLM-1001")
    with TestClient(main.app) as restarted:
        assert status(restarted, "CLM-1001") == "in_progress"
        assert restarted.get(f"/api/runs/{run_id}").json()["status"] == "running"


def test_usage_is_kept_with_a_dispatch_and_not_with_a_claim_run(client):
    dispatch_id = dispatch(client).json()["run_id"]
    claim_run = new_run()
    for run_id in [dispatch_id, claim_run, None, "RUN-0000"]:
        body = {"run_id": run_id, "prompt_tokens": 1, "completion_tokens": 1}
        client.post("/api/claims/CLM-1001/model-calls", json=body | {"cost": 1.0})
        work = {"run_id": run_id, "started_at": "2026-01-01T00:00:00", "seconds": 2}
        client.post("/api/claims/CLM-1001/work-time", json=work)
    with db.connect() as connection:
        used = connection.execute(select(db.model_calls.c.run_id)).scalars().all()
        timed = connection.execute(select(db.work_time.c.run_id)).scalars().all()
    assert used == [dispatch_id, None, None, None]
    assert timed == used
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["cost"] == 4.0
    assert claim["seconds"] == 8.0
