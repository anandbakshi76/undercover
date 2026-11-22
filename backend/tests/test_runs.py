import httpx
from fastapi.testclient import TestClient

from backend import main


def decline(claim_id, thread_id):
    main.record_decision(
        claim_id,
        main.Decision(
            decision="decline",
            payout=0,
            reason="Not covered.",
            policy_section="Section 9",
        ),
    )


def new_run(client, limit):
    return client.post("/api/runs", json={"limit": limit}).json()["run_id"]


def start(client, run_id, claim_id):
    return client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})


def release(client, claim_id, problem="The model timed out."):
    return client.post(f"/api/claims/{claim_id}/release", json={"problem": problem})


def status(client, claim_id):
    return client.get(f"/api/claims/{claim_id}").json()["status"]


def test_the_cap_holds(client):
    run_id = new_run(client, 2)
    assert start(client, run_id, "CLM-1001").json()["status"] == "in_progress"
    assert start(client, run_id, "CLM-1002").status_code == 200
    refused = start(client, run_id, "CLM-1003")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "This run has reached its cap of 2 claims."
    assert status(client, "CLM-1003") == "waiting"


def test_a_failed_claim_goes_back_and_can_be_retried(client):
    run_id = new_run(client, 1)
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
    run_id = new_run(client, 1)
    start(client, run_id, "CLM-1001")
    release(client, "CLM-1001")
    start(client, run_id, "CLM-1001")
    assert release(client, "CLM-1001").json()["status"] == "needs_handler"
    last = client.get("/api/claims/CLM-1001/steps").json()[-1]
    assert last["text"].endswith("It has failed twice and is left for a handler.")
    refused = start(client, new_run(client, 1), "CLM-1001")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Claim CLM-1001 is left for a handler."
    queue = {c["claim_id"]: c["status"] for c in client.get("/api/claims").json()}
    assert queue["CLM-1001"] == "needs_handler"


def test_releasing_a_decided_claim_changes_nothing(client):
    run_id = new_run(client, 1)
    start(client, run_id, "CLM-1001")
    decline("CLM-1001", "t")
    assert release(client, "CLM-1001").json()["status"] == "decided"
    assert client.get("/api/claims/CLM-1001/steps").json() == []


def test_steps_from_every_attempt_are_kept(client):
    run_id = new_run(client, 1)
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


def test_starting_a_claim_needs_a_waiting_claim_and_a_running_run(client):
    run_id = new_run(client, 3)
    start(client, run_id, "CLM-1001")
    other_run = new_run(client, 3)
    assert start(client, other_run, "CLM-1001").status_code == 409
    client.post(
        f"/api/runs/{run_id}/finish", json={"status": "finished", "summary": "Done."}
    )
    refused = start(client, run_id, "CLM-1002")
    assert refused.status_code == 409
    assert refused.json()["detail"] == f"Run {run_id} has finished."
    assert start(client, "RUN-0000", "CLM-1002").status_code == 404


def test_the_to_do_list_is_stored(client):
    run_id = new_run(client, 2)
    todos = [
        {"content": "Work CLM-1001", "status": "done"},
        {"content": "Work CLM-1002", "status": "in_progress"},
        {"content": "Work CLM-1003", "status": "left_for_handler"},
        {"content": "Check for missed claims", "status": "to_do"},
    ]
    stored = client.put(f"/api/runs/{run_id}/todos", json={"todos": todos}).json()
    assert stored["todos"] == todos
    assert client.get(f"/api/runs/{run_id}").json()["todos"] == todos
    assert client.get("/api/queue/latest-run").json()["todos"] == todos


def test_an_unknown_to_do_status_is_refused(client):
    run_id = new_run(client, 1)
    bad = [{"content": "Work CLM-1001", "status": "maybe"}]
    response = client.put(f"/api/runs/{run_id}/todos", json={"todos": bad})
    assert response.status_code == 422


def test_finishing_a_run(client):
    run_id = new_run(client, 1)
    end = {"status": "finished", "summary": "Worked one claim."}
    run = client.post(f"/api/runs/{run_id}/finish", json=end).json()
    assert run["status"] == "finished"
    assert run["summary"] == "Worked one claim."
    assert run["finished_at"] is not None


def test_the_waiting_claims_in_reach(client):
    run_id = new_run(client, 2)
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["claims_left"] == 2
    assert len(run["waiting_in_reach"]) == 25
    start(client, run_id, "CLM-1001")
    start(client, run_id, "CLM-1002")
    decline("CLM-1002", "t")
    release(client, "CLM-1001")
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["claims_left"] == 0
    assert run["waiting_in_reach"] == ["CLM-1001"]


def test_there_is_no_latest_run_at_first(client):
    assert client.get("/api/queue/latest-run").json() is None


def test_the_waiting_claims_show_their_attempts(client):
    run_id = new_run(client, 1)
    start(client, run_id, "CLM-1001")
    release(client, "CLM-1001")
    waiting = client.get("/api/queue/waiting").json()
    assert len(waiting) == 25
    assert waiting[0]["claim_id"] == "CLM-1001"
    assert waiting[0]["attempts"] == 1


def test_a_restart_leaves_claims_in_progress_and_runs_running(client):
    run_id = new_run(client, 1)
    start(client, run_id, "CLM-1001")
    with TestClient(main.app) as restarted:
        assert status(restarted, "CLM-1001") == "in_progress"
        assert restarted.get(f"/api/runs/{run_id}").json()["status"] == "running"


def test_the_agent_can_ask_which_claims_and_runs_are_unfinished(client):
    run_id = new_run(client, 2)
    start(client, run_id, "CLM-1001")
    unfinished = client.get("/api/queue/unfinished").json()
    assert unfinished == [
        {
            "claim_id": "CLM-1001",
            "run_id": run_id,
            "thread_id": f"CLM-1001-{run_id}-1",
        }
    ]
    running = client.get("/api/queue/running-runs").json()
    assert [run["run_id"] for run in running] == [run_id]
    client.post(
        f"/api/runs/{run_id}/finish", json={"status": "finished", "summary": "Done."}
    )
    assert client.get("/api/queue/running-runs").json() == []


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


def test_run_queue_asks_the_agents_service_with_the_cap(client, agent):
    client.post("/api/queue/run", json={"limit": 3})
    assert agent.calls == [("POST", "/api/queue/run", {"limit": 3})]


def test_the_agents_refusals_come_through(client, agent):
    agent.reply = httpx.Response(409, json={"detail": "Claim CLM-1001 is not waiting."})
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 409
    assert response.json()["detail"] == "Claim CLM-1001 is not waiting."


def test_running_needs_the_agent_to_be_up(client, agent):
    agent.down = True
    for path, body in [
        ("/api/claims/CLM-1001/run", None),
        ("/api/queue/run", {"limit": 1}),
    ]:
        response = client.post(path, json=body)
        assert response.status_code == 503
        assert response.json()["detail"] == "The agent isn't running."
    assert status(client, "CLM-1001") == "waiting"
    assert client.get("/api/queue/latest-run").json() is None


def test_run_queue_needs_a_cap_of_at_least_one(client, agent):
    assert client.post("/api/queue/run", json={"limit": 0}).status_code == 422
    assert agent.calls == []


def test_a_cap_of_one_says_claim(client):
    run_id = new_run(client, 1)
    start(client, run_id, "CLM-1001")
    refused = start(client, run_id, "CLM-1002")
    assert refused.json()["detail"] == "This run has reached its cap of 1 claim."


def test_the_instructions_cover_the_queue_and_a_claim(client):
    instructions = client.get("/api/instructions").json()
    assert instructions["queue"].startswith("# Running the queue")
    assert instructions["claim"].startswith("# Working a claim")


def test_starting_a_claim_gives_it_a_thread_and_a_repeat_start_returns_it(client):
    run_id = new_run(client, 1)
    first = start(client, run_id, "CLM-1001").json()
    assert first["thread_id"] == f"CLM-1001-{run_id}-1"
    again = start(client, run_id, "CLM-1001")
    assert again.status_code == 200
    assert again.json()["thread_id"] == first["thread_id"]
    assert client.get(f"/api/runs/{run_id}").json()["claims_worked"] == ["CLM-1001"]
    release(client, "CLM-1001")
    assert start(client, run_id, "CLM-1001").json()["thread_id"].endswith("-2")


def test_releasing_an_approved_claim_changes_nothing(client):
    run_id = new_run(client, 1)
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
