from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from agent import improver, main, service
from agent.tests.stubs import call_tool, stub

GOOD = {
    "instructions": "Be brief.",
    "model": "openai/gpt-6-luna",
    "reasoning_effort": "medium",
    "reason": "Shorter is cheaper.",
}


def new_round():
    return main.call("POST", "/api/rounds/create", {"started_by": "HND-01"})


def build(monkeypatch, *replies):
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    model = stub(*replies)
    monkeypatch.setattr(improver, "improver_model", lambda: model)
    return model


def run(graph, start, thread="t"):
    config = {"configurable": {"thread_id": thread}}
    return graph.invoke(start, config)


def test_a_proposal_outside_the_models_allowed_goes_back_before_a_handler_sees_it(
    monkeypatch,
):
    created = new_round()
    model = build(
        monkeypatch,
        call_tool("propose_change", **(GOOD | {"model": "made/up"})),
        call_tool("propose_change", **GOOD),
    )
    graph = improver.build(created["round_id"], "Recipe.")
    result = run(graph, {"messages": [HumanMessage("Begin.")]})
    assert len(model.seen) == 2
    told = [m.content for m in model.seen[1] if m.type == "tool"]
    assert told == ["made/up at medium isn't allowed. Choose from the models allowed."]
    asked = result["__interrupt__"][0].value["action_requests"]
    assert asked[0]["args"]["model"] == "openai/gpt-6-luna"
    shown = main.call("GET", f"/api/rounds/{created['round_id']}")
    assert shown["proposals"] == []


def test_a_proposal_pauses_and_an_approval_carries_the_improver_on(monkeypatch):
    created = new_round()
    round_id = created["round_id"]
    build(monkeypatch, call_tool("propose_change", **GOOD))
    graph = improver.build(round_id, "Recipe.")
    result = run(graph, {"messages": [HumanMessage("Begin.")]})
    improver.record_proposal(
        round_id, result["__interrupt__"][0].value["action_requests"][0]
    )
    shown = main.call("GET", f"/api/rounds/{round_id}")
    assert shown["pending"]["status"] == "pending"
    assert main.call("GET", "/api/configurations/current")["version"] == 1
    main.call("POST", "/api/runs/stop-running")
    assert main.call("GET", f"/api/rounds/{round_id}")["status"] == "waiting"


def test_a_rejection_reaches_the_improver_as_the_tools_answer(monkeypatch):
    created = new_round()
    round_id = created["round_id"]
    model = build(
        monkeypatch,
        call_tool("propose_change", **GOOD),
        AIMessage("I will stop."),
    )
    graph = improver.build(round_id, "Recipe.")
    run(graph, {"messages": [HumanMessage("Begin.")]})
    reject = {"type": "reject", "message": "Too short for a denial."}
    run(graph, Command(resume={"decisions": [reject]}))
    last = [m.content for m in model.seen[-1] if m.type == "tool"]
    assert "Too short for a denial." in last[0]


def test_the_improver_has_the_four_tools_and_nothing_to_change_a_limit():
    names = [tool.name for tool in improver.make_tools("ROUND-x")]
    assert names == [
        "read_results",
        "propose_change",
        "run_evaluation",
        "propose_recipe",
    ]


def test_a_stopped_round_ends_the_improvers_turn(monkeypatch):
    created = new_round()
    model = build(monkeypatch, call_tool("read_results"))
    main.call("POST", "/api/runs/stop-running")
    graph = improver.build(created["round_id"], "Recipe.")
    run(graph, {"messages": [HumanMessage("Begin.")]})
    assert model.seen == []


def test_only_the_improver_imports_its_client():
    folder = Path(main.__file__).parent
    for path in folder.glob("*.py"):
        if path.name in ("improver.py", "improver_api.py"):
            continue
        assert "improver_api" not in path.read_text(encoding="utf-8"), path


def test_a_free_model_runs_no_rounds(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    client = TestClient(service.app)
    refused = client.post("/api/rounds", json={"started_by": "HND-01"})
    assert refused.status_code == 409
