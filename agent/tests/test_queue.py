from langchain_core.messages import AIMessage, HumanMessage

from agent import main, queue_agent
from agent.__main__ import cli
from agent.tests.stubs import approve_and_pay, call_tool, claim, new_run, stub
from backend import main as backend

ERROR = RuntimeError("model unavailable")


def todos(*items):
    return call_tool(
        "write_todos",
        todos=[{"content": content, "status": status} for content, status in items],
    )


def work(claim_id):
    return call_tool("work_claim", claim_id=claim_id)


def run(run_id):
    return main.call("GET", f"/api/runs/{run_id}")


def statuses():
    return [c["status"] for c in main.call("GET", "/api/claims")]


def tool_results(model):
    return [m.content for m in model.seen[-1] if m.type == "tool"]


def test_a_capped_run_works_its_claims_and_finishes():
    run_id = new_run(2)
    model = stub(
        call_tool("list_waiting_claims"),
        todos(("Work CLM-1001", "to_do"), ("Work CLM-1002", "to_do")),
        work("CLM-1001"),
        *approve_and_pay("CLM-1001"),
        todos(("Work CLM-1001", "done"), ("Work CLM-1002", "in_progress")),
        work("CLM-1002"),
        *approve_and_pay("CLM-1002"),
        todos(("Work CLM-1001", "done"), ("Work CLM-1002", "done")),
        AIMessage(content="Worked 2 claims, both approved and paid."),
    )
    summary = queue_agent.run_queue(run_id, model)
    assert summary == "Worked 2 claims, both approved and paid."
    finished = run(run_id)
    assert finished["status"] == "finished"
    assert finished["summary"] == summary
    assert finished["todos"] == [
        {"content": "Work CLM-1001", "status": "done"},
        {"content": "Work CLM-1002", "status": "done"},
    ]
    assert finished["claims_worked"] == ["CLM-1001", "CLM-1002"]
    assert statuses().count("decided") == 2
    assert statuses().count("waiting") == 23
    waiting = model.seen[1][-1].content
    assert '"claims_left": 2' in waiting
    assert "The to-do list is saved, with 2 items." in tool_results(model)


def test_the_cap_holds_when_the_agent_tries_one_more():
    run_id = new_run(1)
    model = stub(
        work("CLM-1001"),
        *approve_and_pay("CLM-1001"),
        work("CLM-1002"),
        AIMessage(content="Done."),
    )
    queue_agent.run_queue(run_id, model)
    assert claim("CLM-1002")["status"] == "waiting"
    assert (
        "CLM-1002: not started. This run has reached its cap of 1 claim."
        in tool_results(model)
    )


def test_a_failed_claim_does_not_stop_the_run(monkeypatch):
    monkeypatch.setattr(queue_agent, "MAX_CHECKS", 0)
    run_id = new_run(2)
    model = stub(
        work("CLM-1001"),
        ERROR,
        ERROR,
        ERROR,
        work("CLM-1002"),
        *approve_and_pay("CLM-1002"),
        AIMessage(content="One claim failed."),
    )
    queue_agent.run_queue(run_id, model)
    assert claim("CLM-1001")["status"] == "waiting"
    assert claim("CLM-1002")["status"] == "decided"
    assert run(run_id)["status"] == "finished"


def test_a_failed_claim_is_tried_again_and_decided():
    run_id = new_run(1)
    model = stub(
        work("CLM-1001"),
        ERROR,
        ERROR,
        ERROR,
        work("CLM-1001"),
        *approve_and_pay("CLM-1001"),
        AIMessage(content="Approved on the second try."),
    )
    queue_agent.run_queue(run_id, model)
    decided = claim("CLM-1001")
    assert decided["status"] == "decided"
    assert decided["attempts"] == 1


def test_a_claim_that_fails_twice_is_left_for_a_handler():
    run_id = new_run(1)
    model = stub(
        work("CLM-1001"),
        ERROR,
        ERROR,
        ERROR,
        work("CLM-1001"),
        ERROR,
        ERROR,
        ERROR,
        todos(("Work CLM-1001", "left_for_handler")),
        AIMessage(content="CLM-1001 is left for a handler."),
    )
    queue_agent.run_queue(run_id, model)
    assert claim("CLM-1001")["status"] == "needs_handler"
    finished = run(run_id)
    assert finished["status"] == "finished"
    assert finished["todos"][0]["status"] == "left_for_handler"


def test_the_agent_is_sent_back_to_claims_it_missed():
    run_id = new_run(2)
    model = stub(
        work("CLM-1001"),
        *approve_and_pay("CLM-1001"),
        AIMessage(content="Done."),
        work("CLM-1002"),
        *approve_and_pay("CLM-1002"),
        AIMessage(content="Done, both claims."),
    )
    assert queue_agent.run_queue(run_id, model) == "Done, both claims."
    reminders = [
        m.text
        for seen in model.seen
        for m in seen
        if isinstance(m, HumanMessage) and m.text.startswith("Before you finish")
    ]
    assert reminders
    assert "CLM-1002" in reminders[0]
    assert statuses().count("decided") == 2


def test_the_check_gives_up_after_three_reminders():
    run_id = new_run(1)
    model = stub(*[AIMessage(content="Done.") for _ in range(4)])
    queue_agent.run_queue(run_id, model)
    assert len(model.seen) == 4
    assert run(run_id)["status"] == "finished"


def test_a_failing_queue_agent_fails_its_run_and_leaves_nothing_in_progress():
    run_id = new_run(2)
    model = stub(work("CLM-1001"), ERROR, ERROR, ERROR, ERROR, ERROR, ERROR)
    summary = queue_agent.run_queue(run_id, model)
    assert summary == "The queue agent stopped with an error: model unavailable."
    assert run(run_id)["status"] == "failed"
    assert "in_progress" not in statuses()


def test_a_to_do_list_the_backend_refuses_is_reported():
    tools = queue_agent.queue_tools("RUN-0000", stub())
    write_todos = tools[0]
    result = write_todos.invoke({"todos": [{"content": "x", "status": "to_do"}]})
    assert result == "The to-do list was not saved: There is no run RUN-0000."


def test_the_queue_instructions_come_from_the_backend(tmp_path, monkeypatch):
    (tmp_path / "queue.md").write_text("Run the queue carefully.", encoding="utf-8")
    (tmp_path / "claim.md").write_text("Decide claims.", encoding="utf-8")
    monkeypatch.setattr(backend, "INSTRUCTIONS", tmp_path)
    model = stub(AIMessage(content="Nothing to do."))
    queue_agent.run_queue(new_run(1), model)
    assert model.seen[0][0].content == "Run the queue carefully."
    assert model.seen[0][1].content == "Work the queue. This run is capped at 1 claim."


def test_the_command_line_runs_a_capped_queue(monkeypatch):
    script = stub(work("CLM-1001"), *approve_and_pay("CLM-1001"), AIMessage("Done."))
    monkeypatch.setattr(main, "model", lambda: script)
    assert cli(["--limit", "1"]) == "Done."
    assert claim("CLM-1001")["status"] == "decided"
