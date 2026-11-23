from contextlib import nullcontext

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from agent import claim as claim_agent
from agent import main, service
from agent.tests.stubs import approve_and_pay, claim, new_run, recorded_steps, stub
from backend import main as backend

SIGN_OFF = {"handler_id": "HND-01"}


@pytest.fixture
def agent_service():
    return TestClient(service.app)


@pytest.fixture
def wired(monkeypatch, agent_service):
    """The backend tells the real service about a sign-off."""
    monkeypatch.setattr(backend, "agent_client", lambda: nullcontext(agent_service))


def hold(claim_id="CLM-1002", saver=None):
    """Work a claim until it waits for a sign-off."""
    model = stub(*approve_and_pay(claim_id, payout=1250.0))
    return claim_agent.work_claim(claim_id, new_run(1), model, saver)


def sign_off(claim_id="CLM-1002"):
    return main.call("POST", f"/api/claims/{claim_id}/sign-off", SIGN_OFF)


def payments(claim_id):
    texts = [step["text"] for step in recorded_steps(claim_id)]
    return [text for text in texts if text.startswith("pay_claim(")]


def test_a_sign_off_reaches_the_service_which_resumes_and_pays_once(
    wired, monkeypatch, agent_service
):
    monkeypatch.setattr(main, "model", stub)
    assert hold().endswith("waiting for sign-off")
    signed = sign_off()
    assert signed["status"] == "in_progress"
    paid = claim("CLM-1002")
    assert paid["status"] == "decided"
    assert paid["payment"]["amount"] == 1250.0
    assert paid["payment"]["signed_off_by"] == "HND-01"
    again = agent_service.post("/api/claims/CLM-1002/resume")
    assert again.status_code == 409
    assert claim("CLM-1002")["payment"] == paid["payment"]
    assert len(payments("CLM-1002")) == 1


def test_resume_needs_a_claim_that_is_in_progress(agent_service):
    assert agent_service.post("/api/claims/CLM-1001/resume").status_code == 409
    assert agent_service.post("/api/claims/CLM-0000/resume").status_code == 404


def test_the_service_runs_one_claim(monkeypatch, agent_service):
    monkeypatch.setattr(main, "model", lambda: stub(*approve_and_pay("CLM-1001")))
    response = agent_service.post("/api/claims/CLM-1001/run")
    assert response.status_code == 200
    assert claim("CLM-1001")["status"] == "decided"
    run = main.call("GET", f"/api/runs/{response.json()['run_id']}")
    assert run["kind"] == "claim"
    assert run["status"] == "finished"


def test_the_service_passes_on_the_backends_refusals(monkeypatch, agent_service):
    monkeypatch.setattr(main, "model", lambda: stub(*approve_and_pay("CLM-1001")))
    agent_service.post("/api/claims/CLM-1001/run")
    refused = agent_service.post("/api/claims/CLM-1001/run")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Claim CLM-1001 is not waiting."
    assert agent_service.post("/api/claims/CLM-0000/run").status_code == 404


def test_the_service_is_healthy(agent_service):
    assert agent_service.get("/api/health").json() == {"status": "ok"}


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
