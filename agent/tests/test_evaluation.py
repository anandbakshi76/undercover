import re
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableConfig

from agent import checkpoints, dispatcher, main, service
from agent.tests.stubs import ClaimStub, claim, new_dispatch, recorded_steps


def create(evaluation_set="held_out", tiny=True, concurrency=5):
    body = {
        "concurrency": concurrency,
        "evaluation_set": evaluation_set,
        "tiny": tiny,
        "version": 1,
        "round_id": None,
        "model": "stub",
        "reasoning_effort": "low",
    }
    return main.call("POST", "/api/runs/evaluation", body)


def queue():
    return main.call("GET", "/api/claims")


def work(monkeypatch, **options):
    model = ClaimStub(messages=iter([]), **options)
    monkeypatch.setattr(main, "model", lambda _config: model)
    return model


def test_an_evaluation_works_its_copies_through_the_dispatcher(monkeypatch):
    work(monkeypatch)
    before = queue()
    run = create()
    dispatcher.dispatch(run["run_id"], 5)
    assert queue() == before
    shown = main.call("GET", f"/api/evaluations/{run['run_id']}")
    assert shown["claims"]
    assert len(shown["claims"]) == 5
    assert all(item["status"] == "decided" for item in shown["claims"])
    assert all(item["agent"]["decision"] == "approve" for item in shown["claims"])
    assert shown["cost_per_claim"] > 0
    assert shown["seconds_per_claim"] > 0


def test_a_copy_held_for_sign_off_counts_as_finished_and_its_progress_is_cleared(
    monkeypatch,
):
    run = create()
    held = run["claims_worked"][0]
    work(monkeypatch, payouts={held: 1250.0})
    dispatcher.dispatch(run["run_id"], 5)
    copy = claim(held)
    assert copy["status"] == "awaiting_sign_off"
    assert copy["decision"]["payout"] == 1250.0
    assert copy["payment"] is None
    config: RunnableConfig = {"configurable": {"thread_id": copy["thread_id"]}}
    assert checkpoints.memory().get_tuple(config) is None
    texts = [step["text"] for step in recorded_steps(held)]
    assert "Held for a sign-off. An evaluation copy counts as finished." in texts
    refused = main.call("POST", f"/api/claims/{held}/sign-off")
    assert "evaluation copy" in refused["detail"]
    main.call("POST", f"/api/runs/{run['run_id']}/finish", {"status": "finished"})
    shown = main.call("GET", f"/api/evaluations/{run['run_id']}")
    row = {item["claim_id"]: item for item in shown["claims"]}[held]
    assert row["agent"] == {"decision": "approve", "payout": 1250.0}


def test_a_dispatch_never_carries_a_copy_on(monkeypatch):
    work(monkeypatch)
    run = create()
    held = run["claims_worked"][0]
    main.call("POST", f"/api/claims/{held}/start", {"run_id": run["run_id"]})
    main.call("POST", "/api/runs/stop-running")
    dispatch = new_dispatch(5)
    dispatcher.dispatch(dispatch, 5)
    worked = main.call("GET", f"/api/runs/{dispatch}")["claims_worked"]
    assert held not in worked
    assert len(worked) == 25
    assert claim(held)["status"] == "in_progress"


def test_the_service_runs_an_evaluation(monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    work(monkeypatch)
    before = queue()
    client = TestClient(service.app)
    body = {"concurrency": 5, "evaluation_set": "held_out", "tiny": True}
    response = client.post("/api/evaluations", json=body)
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    shown = main.call("GET", f"/api/evaluations/{run_id}")
    assert shown["status"] == "finished"
    assert shown["concurrency"] == 5
    assert shown["model"] == "openai/gpt-6-luna"
    assert shown["reasoning_effort"] == "low"
    assert shown["claims"]
    assert queue() == before
    again = client.post("/api/evaluations", json=body)
    assert again.status_code == 200


def test_a_second_evaluation_is_refused_while_one_is_running():
    create()
    client = TestClient(service.app)
    body = {"concurrency": 5, "evaluation_set": "evaluation", "tiny": False}
    refused = client.post("/api/evaluations", json=body)
    assert refused.status_code == 409
    assert client.post("/api/evaluations", json=body | {"tiny": "x"}).status_code == 422


def test_the_agent_never_asks_for_the_answer_key():
    folder = Path(main.__file__).parent
    for path in folder.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "eval_decisions" not in source, path
        assert "scoring" not in source, path
        for forbidden in ["judge", "grades"]:
            assert forbidden not in source, (forbidden, path)
        calls = re.findall(r'call\(\s*"GET",\s*f?"/api/evaluations', source)
        assert calls == [], path
