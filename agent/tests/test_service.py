from contextlib import nullcontext

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from agent import claim as claim_agent
from agent import dispatcher, main, service
from agent.tests.stubs import (
    ClaimStub,
    approve_and_pay,
    claim,
    new_dispatch,
    new_run,
    recorded_steps,
    stub,
)
from backend import agent_service


@pytest.fixture
def service_client():
    return TestClient(service.app)


@pytest.fixture
def wired(monkeypatch, service_client):
    """The backend tells the real service about a sign-off."""
    monkeypatch.setattr(agent_service, "client", lambda: nullcontext(service_client))


def hold(claim_id="CLM-1002", saver=None):
    """Work a claim until it waits for a sign-off."""
    model = stub(*approve_and_pay(claim_id, payout=1250.0))
    return claim_agent.work_claim(claim_id, new_run(1), model, saver)


def sign_off(claim_id="CLM-1002"):
    return main.call("POST", f"/api/claims/{claim_id}/sign-off")


def payments(claim_id):
    texts = [step["text"] for step in recorded_steps(claim_id)]
    return [text for text in texts if text.startswith("pay_claim(")]


def test_a_sign_off_reaches_the_service_which_resumes_and_pays_once(
    wired, monkeypatch, service_client
):
    monkeypatch.setattr(main, "model", lambda _config: stub())
    assert hold().endswith("waiting for sign-off")
    signed = sign_off()
    assert signed["status"] == "in_progress"
    paid = claim("CLM-1002")
    assert paid["status"] == "decided"
    assert paid["payment"]["amount"] == 1250.0
    assert paid["payment"]["signed_off_by"] == "HND-01"
    again = service_client.post("/api/claims/CLM-1002/resume")
    assert again.status_code == 409
    assert claim("CLM-1002")["payment"] == paid["payment"]
    assert len(payments("CLM-1002")) == 1


def test_resume_needs_a_claim_that_is_in_progress(service_client):
    assert service_client.post("/api/claims/CLM-1001/resume").status_code == 409
    assert service_client.post("/api/claims/CLM-0000/resume").status_code == 404


def test_the_service_runs_one_claim(monkeypatch, service_client):
    monkeypatch.setattr(
        main, "model", lambda _config: stub(*approve_and_pay("CLM-1001"))
    )
    response = service_client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 200
    assert claim("CLM-1001")["status"] == "decided"
    run = main.call("GET", f"/api/runs/{response.json()['run_id']}")
    assert run["kind"] == "claim"
    assert run["status"] == "finished"


def test_the_service_passes_on_the_backends_refusals(monkeypatch, service_client):
    monkeypatch.setattr(
        main, "model", lambda _config: stub(*approve_and_pay("CLM-1001"))
    )
    service_client.post("/api/claims/CLM-1001/run")
    refused = service_client.post("/api/claims/CLM-1001/run")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Claim CLM-1001 is not waiting."
    assert service_client.post("/api/claims/CLM-0000/run").status_code == 404


def test_the_service_is_healthy(service_client):
    assert service_client.get("/api/health").json() == {"status": "ok"}


def test_every_service_operation_follows_the_api_rules():
    for route in service.app.routes:
        if isinstance(route, APIRoute):
            assert route.operation_id == route.name, route.path
            doc = (route.endpoint.__doc__ or "").strip()
            assert doc, route.path
            assert "\n" not in doc, route.path
    spec = service.app.openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    for methods in spec["paths"].values():
        for operation in methods.values():
            for parameter in operation.get("parameters", []):
                assert parameter["required"], operation["operationId"]
            if "requestBody" in operation:
                body = operation["requestBody"]["content"]["application/json"]
                schema = schemas[body["schema"]["$ref"].split("/")[-1]]
                assert set(schema["required"]) == set(schema["properties"])


def test_a_claim_held_for_sign_off_is_not_worked_by_the_service_twice():
    saver = InMemorySaver()
    hold(saver=saver)
    assert not claim_agent.is_working("CLM-1002")


def run_of(response):
    return main.call("GET", f"/api/runs/{response.json()['run_id']}")


def test_the_service_starts_a_dispatch_and_works_it(monkeypatch, service_client):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    model = ClaimStub(messages=iter([]))
    monkeypatch.setattr(main, "model", lambda _config: model)
    response = service_client.post("/api/dispatches", json={"concurrency": 5})
    assert response.status_code == 200
    run = run_of(response)
    assert run["status"] == "finished"
    assert run["kind"] == "dispatch"
    assert run["concurrency"] == 5
    assert run["model"] == "openai/gpt-6-luna"
    assert run["reasoning_effort"] == "low"
    assert len(run["claims"]) == 25
    assert run["summary"]["approved"] == 25
    assert run["cost"] > 0
    assert run["work_seconds"] > 0


def test_another_model_is_recorded_with_no_effort(monkeypatch, service_client):
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    monkeypatch.setattr(dispatcher, "dispatch", lambda run_id, concurrency: [])
    run = run_of(service_client.post("/api/dispatches", json={"concurrency": 2}))
    assert run["model"] == "openrouter/free"
    assert run["reasoning_effort"] == "default"


def test_a_second_start_is_refused_while_one_is_running(service_client):
    new_dispatch()
    refused = service_client.post("/api/dispatches", json={"concurrency": 3})
    assert refused.status_code == 409
    assert refused.json()["detail"] == "A dispatch or evaluation is already running."


def test_n_must_be_from_one_to_twenty_five(service_client):
    for n in [0, 26]:
        assert (
            service_client.post("/api/dispatches", json={"concurrency": n}).status_code
            == 422
        )


def test_a_dispatch_that_breaks_is_recorded_as_failed(monkeypatch, service_client):
    def broken(run_id, concurrency):
        raise RuntimeError("The backend is down.")

    monkeypatch.setattr(dispatcher, "dispatch", broken)
    response = service_client.post("/api/dispatches", json={"concurrency": 3})
    assert run_of(response)["status"] == "failed"


def test_on_startup_the_service_stops_runs_an_earlier_one_left():
    run_id = new_dispatch()
    main.call("POST", "/api/claims/CLM-1001/start", {"run_id": run_id})
    with TestClient(service.app):
        pass
    run = main.call("GET", f"/api/runs/{run_id}")
    assert run["status"] == "stopped"
    assert claim("CLM-1001")["status"] == "in_progress"


def test_the_service_waits_for_the_backend_to_come_up(monkeypatch):
    calls = []

    def call(method, path, body=None):
        calls.append(path)
        if len(calls) < 3:
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(main, "call", call)
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)
    service.stop_running_runs()
    assert calls == ["/api/runs/stop-running"] * 3
