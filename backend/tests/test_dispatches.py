import httpx

from backend import db, main


def create(client, concurrency=3):
    body = {
        "concurrency": concurrency,
        "version": 1,
        "model": "m",
        "reasoning_effort": "low",
    }
    return client.post("/api/runs/dispatch", json=body).json()["run_id"]


def finish(client, run_id, status="finished"):
    return client.post(f"/api/runs/{run_id}/finish", json={"status": status}).json()


def start(client, run_id, claim_id):
    return client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})


def decide(client, claim_id, decision, payout=0):
    body = {
        "decision": decision,
        "payout": payout,
        "reason": "Because.",
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


def test_start_dispatch_sends_n_to_the_agents_service(client, agent):
    response = client.post("/api/dispatches", json={"concurrency": 7})
    assert response.json() == {"status": "started", "run_id": "RUN-1"}
    assert agent.calls == [("POST", "/api/dispatches", {"concurrency": 7})]


def test_n_is_from_one_to_twenty_five(client, agent):
    for n in [0, 26]:
        assert (
            client.post("/api/dispatches", json={"concurrency": n}).status_code == 422
        )
    assert agent.calls == []


def test_with_the_service_down_the_start_says_so_and_creates_nothing(client, agent):
    agent.down = True
    response = client.post("/api/dispatches", json={"concurrency": 3})
    assert response.status_code == 503
    assert response.json()["detail"] == "The agent isn't running."
    assert client.get("/api/dispatches/latest").json() is None


def test_the_services_refusal_comes_through(client, agent):
    agent.reply = httpx.Response(
        409, json={"detail": "A dispatch or evaluation is already running."}
    )
    response = client.post("/api/dispatches", json={"concurrency": 3})
    assert response.status_code == 409
    assert response.json()["detail"] == "A dispatch or evaluation is already running."


def test_the_latest_dispatch_is_the_newest(client):
    assert client.get("/api/dispatches/latest").json() is None
    first = create(client)
    finish(client, first)
    second = create(client)
    assert client.get("/api/dispatches/latest").json()["run_id"] == second


def test_stopping_marks_running_dispatches_stopped_and_leaves_their_claims(client):
    run_id = create(client)
    start(client, run_id, "CLM-1001")
    stopped = client.post("/api/runs/stop-running").json()
    assert stopped == [run_id]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "stopped"
    assert run["finished_at"] is not None
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "in_progress"
    assert client.post("/api/runs/stop-running").json() == []
    assert create(client)


def test_the_summary_comes_from_the_records(client):
    run_id = create(client)
    for claim_id in [f"CLM-100{n}" for n in range(1, 8)]:
        start(client, run_id, claim_id)
    decide(client, "CLM-1001", "approve", 420)
    client.post("/api/claims/CLM-1001/payment", json={"amount": 420})
    decide(client, "CLM-1002", "approve", 1250)
    decide(client, "CLM-1003", "decline")
    decide(client, "CLM-1004", "refer")
    decide(client, "CLM-1005", "refer")
    for _ in range(2):
        client.post("/api/claims/CLM-1006/release", json={"problem": "Broke."})
        start(client, run_id, "CLM-1006")
    client.post("/api/claims/CLM-1006/release", json={"problem": "Broke."})
    run = finish(client, run_id)
    assert run["summary"] == {
        "approved": 2,
        "declined": 1,
        "referred": 2,
        "awaiting_sign_off": [{"claim_id": "CLM-1002", "payout": 1250.0}],
        "needs_handler": ["CLM-1006"],
        "in_progress": ["CLM-1007"],
    }
    assert client.get(f"/api/runs/{run_id}").json()["summary"] == run["summary"]


def test_a_runs_claims_show_state_start_time_cost_and_time(client):
    run_id = create(client)
    start(client, run_id, "CLM-1001")
    decide(client, "CLM-1001", "decline")
    start(client, run_id, "CLM-1002")
    usage(client, "CLM-1001", run_id, 0.25, 4.0)
    usage(client, "CLM-1001", run_id, 0.25, 1.0)
    run = client.get(f"/api/runs/{run_id}").json()
    first, second = run["claims"]
    assert first["claim_id"] == "CLM-1001"
    assert first["status"] == "decided"
    assert first["decision"] == "decline"
    assert first["cost"] == 0.5
    assert first["seconds"] == 5.0
    assert first["started_at"] == "2026-01-01T10:00:00.000"
    assert second["status"] == "in_progress"
    assert second["cost"] == 0
    assert second["started_at"] is None
    assert run["cost"] == 0.5
    assert run["work_seconds"] == 5.0
    assert run["prompt_tokens"] == 20
    assert run["completion_tokens"] == 10
    assert run["elapsed_seconds"] >= 0


def test_each_runs_totals_are_its_own_failed_attempts_included(client):
    first = create(client)
    start(client, first, "CLM-1001")
    usage(client, "CLM-1001", first, 1.0, 10.0)
    client.post("/api/claims/CLM-1001/release", json={"problem": "Broke."})
    start(client, first, "CLM-1001")
    usage(client, "CLM-1001", first, 2.0, 20.0)
    finish(client, first)
    second = create(client)
    start(client, second, "CLM-1002")
    usage(client, "CLM-1002", second, 5.0, 50.0)
    usage(client, "CLM-1002", None, 9.0, 90.0)
    one = client.get(f"/api/runs/{first}").json()
    two = client.get(f"/api/runs/{second}").json()
    assert (one["cost"], one["work_seconds"]) == (3.0, 30.0)
    assert (two["cost"], two["work_seconds"]) == (5.0, 50.0)
    assert client.get("/api/claims/CLM-1002").json()["cost"] == 14.0


def test_a_run_that_is_still_going_shows_its_time_so_far(client):
    run_id = create(client)
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["finished_at"] is None
    assert run["elapsed_seconds"] >= 0
    with db.connect() as connection:
        assert main.build_summary(connection, run_id)["approved"] == 0
