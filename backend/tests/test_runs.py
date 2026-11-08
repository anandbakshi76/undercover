import pytest
from fastapi.testclient import TestClient

from backend import db, main


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


@pytest.fixture
def runs(monkeypatch):
    seen = []

    def fake_run(claim_id, thread_id):
        seen.append((claim_id, thread_id))
        decline(claim_id, thread_id)

    monkeypatch.setattr(main.agent, "run", fake_run)
    return seen


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
    assert start(client, run_id, "CLM-1001").status_code == 409
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


def test_startup_releases_stuck_claims_and_fails_running_runs(client):
    run_id = new_run(client, 1)
    start(client, run_id, "CLM-1001")
    main.recover()
    assert status(client, "CLM-1001") == "waiting"
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["summary"] == "The backend restarted during the run."
    step = client.get("/api/claims/CLM-1001/steps").json()[-1]
    assert step["text"].startswith("The backend restarted during the run.")


def test_the_app_recovers_when_it_starts():
    db.init()
    with db.connect() as connection:
        run_id = main.new_run(connection, "queue", 1)
        main.start(connection, run_id, "CLM-1001")
    with TestClient(main.app) as client:
        assert status(client, "CLM-1001") == "waiting"


def test_run_agent_decides_a_waiting_claim(client, runs):
    response = client.post("/api/claims/CLM-1001/run").json()
    assert response["status"] == "in_progress"
    assert runs[0] == ("CLM-1001", f"{response['run_id']}-CLM-1001")
    assert status(client, "CLM-1001") == "decided"
    run = client.get(f"/api/runs/{response['run_id']}").json()
    assert run["kind"] == "claim"
    assert run["status"] == "finished"


def test_run_agent_needs_a_waiting_claim(client, runs):
    client.post("/api/claims/CLM-1001/run")
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 409
    assert len(runs) == 1


def test_run_agent_on_an_unknown_claim_is_404(client, runs):
    assert client.post("/api/claims/CLM-0000/run").status_code == 404


def test_run_queue_is_capped(client, runs):
    run = client.post("/api/queue/run", json={"limit": 3}).json()
    assert run["claim_limit"] == 3
    assert [claim_id for claim_id, _ in runs] == ["CLM-1001", "CLM-1002", "CLM-1003"]
    statuses = [c["status"] for c in client.get("/api/claims").json()]
    assert statuses.count("decided") == 3
    assert statuses.count("waiting") == 22
    latest = client.get("/api/queue/latest-run").json()
    assert latest["run_id"] == run["run_id"]
    assert latest["status"] == "finished"


def test_run_queue_needs_a_cap_of_at_least_one(client, runs):
    assert client.post("/api/queue/run", json={"limit": 0}).status_code == 422
    assert runs == []


def test_a_run_without_a_decision_puts_the_claim_back(client, monkeypatch):
    monkeypatch.setattr(main.agent, "run", lambda claim_id, thread_id: "Hello.")
    client.post("/api/claims/CLM-1001/run")
    assert status(client, "CLM-1001") == "waiting"
    step = client.get("/api/claims/CLM-1001/steps").json()[-1]
    assert step["kind"] == "error"
    assert step["text"].startswith("The agent finished without recording a decision.")


def test_an_error_before_the_decision_puts_the_claim_back(client, monkeypatch):
    def fail(claim_id, thread_id):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(main.agent, "run", fail)
    client.post("/api/claims/CLM-1001/run")
    assert status(client, "CLM-1001") == "waiting"
    step = client.get("/api/claims/CLM-1001/steps").json()[-1]
    assert step["text"] == (
        "The agent stopped with an error: connection lost. "
        "The claim is back in the queue."
    )
