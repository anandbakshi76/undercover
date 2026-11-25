import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import insert, update

from agent import claim as claim_agent
from agent import main, service
from agent.tests.stubs import approve_and_pay, call_tool, claim, new_dispatch, stub
from backend import db
from backend.db import configurations, improvement_state


def add_version(version, instructions, model="openai/gpt-6-luna", effort="low"):
    with db.connect() as connection:
        connection.execute(
            insert(configurations).values(
                version=version,
                instructions=instructions,
                model=model,
                reasoning_effort=effort,
                reason="A change.",
                proposed_by="The improver",
                approved_by="Dana Whitfield",
                approved_at="2026-11-25T10:00:00+00:00",
            )
        )


def make_current(version):
    with db.connect() as connection:
        connection.execute(
            update(improvement_state)
            .where(improvement_state.c.key == "current_version")
            .values(value=version)
        )


def dispatch_of(version):
    body = {
        "concurrency": 3,
        "version": version,
        "model": "stub",
        "reasoning_effort": "low",
    }
    return main.call("POST", "/api/runs/dispatch", body)["run_id"]


def test_a_claim_uses_the_version_of_the_run_that_starts_it():
    add_version(2, "Version two.")
    run_id = dispatch_of(2)
    model = stub(*approve_and_pay("CLM-1001"))
    claim_agent.work_claim("CLM-1001", run_id, model)
    assert model.seen[0][0].content == "Version two."
    assert claim("CLM-1001")["version"] == 2


def test_a_claim_carried_on_keeps_the_version_it_started_with():
    saver = InMemorySaver()
    first = dispatch_of(1)
    started = stub(call_tool("get_claim", claim_id="CLM-1001"), KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        claim_agent.work_claim("CLM-1001", first, started, saver)
    first_instructions = main.call("GET", "/api/configurations/1")["instructions"]
    add_version(2, "Version two.")
    make_current(2)
    main.call("POST", f"/api/runs/{first}/finish", {"status": "finished"})
    second = dispatch_of(2)
    carried = stub(*approve_and_pay("CLM-1001")[1:])
    claim_agent.work_claim("CLM-1001", second, carried, saver)
    assert carried.seen[0][0].content == first_instructions
    assert claim("CLM-1001")["version"] == 1
    assert claim("CLM-1001")["status"] == "decided"


def test_the_model_and_effort_come_from_the_version(monkeypatch):
    add_version(2, "Version two.", "google/gemini-3.5-flash-lite", "minimal")
    run_id = dispatch_of(2)
    seen = []

    def build(config):
        seen.append(config)
        return stub(*approve_and_pay("CLM-1001"))

    monkeypatch.setattr(main, "model", build)
    claim_agent.work_claim("CLM-1001", run_id)
    assert [(c["model"], c["reasoning_effort"]) for c in seen] == [
        ("google/gemini-3.5-flash-lite", "minimal")
    ]


def test_the_service_starts_a_dispatch_on_the_current_version(monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    add_version(2, "Version two.", "google/gemini-3.5-flash-lite", "minimal")
    make_current(2)
    monkeypatch.setattr(service.dispatcher, "dispatch", lambda run_id, n: [])
    response = TestClient(service.app).post("/api/dispatches", json={"concurrency": 4})
    run = main.call("GET", f"/api/runs/{response.json()['run_id']}")
    assert run["version"] == 2
    assert run["model"] == "google/gemini-3.5-flash-lite"
    assert run["reasoning_effort"] == "minimal"


def test_the_service_starts_an_evaluation_on_the_current_version(monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    add_version(2, "Version two.")
    make_current(2)
    monkeypatch.setattr(service.dispatcher, "dispatch", lambda run_id, n: [])
    body = {"concurrency": 3, "evaluation_set": "held_out", "tiny": True}
    response = TestClient(service.app).post("/api/evaluations", json=body)
    assert (
        main.call("GET", f"/api/evaluations/{response.json()['run_id']}")["version"]
        == 2
    )


def test_an_evaluation_run_made_with_a_dispatch_helper_uses_version_1():
    run = main.call("GET", f"/api/runs/{new_dispatch()}")
    assert run["version"] == 1
