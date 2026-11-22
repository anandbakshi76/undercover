from contextlib import nullcontext

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import claim as claim_agent
from agent import main, queue_agent, resume, service
from agent.__main__ import cli
from agent.tests.stubs import (
    approve_and_pay,
    call_tool,
    claim,
    decide,
    new_run,
    recorded_steps,
    stub,
)
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
    return claim_agent.work_claim(new_run(1), claim_id, model, saver)


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


def test_a_run_first_carries_on_what_an_earlier_run_left(monkeypatch, agent_service):
    hold()
    assert sign_off()["status"] == "in_progress"
    assert claim("CLM-1002")["payment"] is None
    stopped = stub(call_tool("get_claim", claim_id="CLM-1001"), KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        claim_agent.work_claim(new_run(1), "CLM-1001", stopped)
    for run in main.call("GET", "/api/queue/running-runs"):
        end = {"status": "finished", "summary": ""}
        main.call("POST", f"/api/runs/{run['run_id']}/finish", end)
    script = stub(
        AIMessage(content="Approve."),
        decide("CLM-1001"),
        *[AIMessage(content="Nothing more to do.") for _ in range(4)],
    )
    monkeypatch.setattr(main, "model", lambda: script)
    response = agent_service.post("/api/queue/run", json={"limit": 1})
    assert response.status_code == 200
    decided = claim("CLM-1001")
    assert decided["status"] == "decided"
    assert decided["attempts"] == 0
    assert claim("CLM-1002")["payment"]["amount"] == 1250.0
    finished = main.call("GET", f"/api/runs/{response.json()['run_id']}")
    assert finished["status"] == "finished", finished["summary"]
    assert finished["claims_worked"] == []
    steps = recorded_steps("CLM-1001")
    assert [s["kind"] for s in steps].count("tool_call") == 3


def test_a_queue_run_stopped_in_a_claim_is_finished_by_the_next_run():
    saver = InMemorySaver()
    run_id = new_run(1)
    working = [{"content": "Work CLM-1001", "status": "in_progress"}]
    first = stub(
        call_tool("write_todos", todos=working),
        call_tool("work_claim", claim_id="CLM-1001"),
        call_tool("get_claim", claim_id="CLM-1001"),
        KeyboardInterrupt(),
    )
    with pytest.raises(KeyboardInterrupt):
        queue_agent.run_queue(run_id, first, saver)
    assert main.call("GET", f"/api/runs/{run_id}")["status"] == "running"
    done = [{"content": "Work CLM-1001", "status": "done"}]
    second = stub(
        AIMessage(content="Approve."),
        decide("CLM-1001"),
        call_tool("write_todos", todos=done),
        AIMessage(content="Worked 1 claim: approved and paid."),
    )
    lines = resume.resume(llm=second, saver=saver)
    assert lines == [
        "CLM-1001: approved, $420.00 paid",
        "Worked 1 claim: approved and paid.",
    ]
    finished = main.call("GET", f"/api/runs/{run_id}")
    assert finished["status"] == "finished"
    assert finished["summary"] == "Worked 1 claim: approved and paid."
    assert finished["todos"] == done
    assert claim("CLM-1001")["decision"]["decision"] == "approve"
    kinds = [s["text"].split("(")[0] for s in recorded_steps("CLM-1001")]
    assert kinds.count("record_decision") == 1
    assert kinds.count("pay_claim") == 1


def test_resume_leaves_a_run_this_process_is_working():
    run_id = new_run(1)
    queue_agent.ACTIVE.add(run_id)
    try:
        assert resume.resume(llm=stub(), saver=InMemorySaver()) == []
    finally:
        queue_agent.ACTIVE.discard(run_id)
    assert resume.resume(skip_run_id=run_id, llm=stub(), saver=InMemorySaver()) == []
    assert main.call("GET", f"/api/runs/{run_id}")["status"] == "running"


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
    assert agent_service.post("/api/queue/run", json={"limit": 0}).status_code == 422


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


def test_the_command_line_first_carries_on_a_signed_off_claim(monkeypatch, capsys):
    hold()
    sign_off()
    for run in main.call("GET", "/api/queue/running-runs"):
        end = {"status": "finished", "summary": ""}
        main.call("POST", f"/api/runs/{run['run_id']}/finish", end)
    script = stub(*[AIMessage(content="Done.") for _ in range(4)])
    monkeypatch.setattr(main, "model", lambda: script)
    assert cli(["--limit", "1"]) == "Done."
    assert "CLM-1002: approved, $1,250.00 paid" in capsys.readouterr().out
    assert claim("CLM-1002")["payment"]["amount"] == 1250.0
