import os
import subprocess
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from psycopg.rows import dict_row

from agent import checkpoints, main
from agent import claim as claim_agent
from agent.__main__ import cli
from agent.tests.stubs import (
    approve_and_pay,
    call_tool,
    claim,
    decide,
    decision,
    new_run,
    recorded_steps,
    stub,
)
from backend import main as backend

ERROR = RuntimeError("model unavailable")
LINES = [
    {
        "description": "Suitcase",
        "amount": 400,
        "quantity": 1,
        "kind": "item",
        "nights": 0,
        "hours": 0,
    }
]


def graph_state(claim_id, saver):
    thread_id = claim(claim_id)["thread_id"]
    graph = claim_agent.claim_graph(stub(), saver, "Instructions.")
    return graph.get_state({"configurable": {"thread_id": thread_id}})


def sign_off(claim_id, handler_id="HND-01"):
    return main.call(
        "POST", f"/api/claims/{claim_id}/sign-off", {"handler_id": handler_id}
    )


def test_tools_call_the_backend():
    tools = claim_agent
    assert tools.get_claim.invoke({"claim_id": "CLM-1001"})["policy_id"] == "POL-2001"
    assert tools.get_policy.invoke({"policy_id": "POL-2001"})["plan"] == "premium"
    history = tools.get_claim_history.invoke({"customer_id": "CUS-3001"})
    assert history["previous_claims"][0]["claim_id"] == "CLM-0412"
    found = tools.search_documents.invoke({"text": "wild animal"})
    assert found[0]["section"] == "7"
    contents = tools.list_sections.invoke({"document": "claims_manual"})
    assert contents["sections"][9]["title"] == "Meals without receipts"
    section = tools.read_section.invoke({"document": "claims_manual", "section": "9"})
    assert section["title"] == "Repeat claims"
    missing = tools.read_section.invoke({"document": "claims_manual", "section": "99"})
    assert missing["detail"].startswith("There is no section")
    result = tools.calculate_payout.invoke(
        {
            "policy_id": "POL-2001",
            "benefit": "baggage",
            "lines": LINES,
            "paid_by_others": 0,
        }
    )
    assert result["payout"] == 250.0
    assert not hasattr(tools, "record_decision")
    assert not hasattr(tools, "pay_claim")
    assert len(tools.TOOLS) == 7


def test_a_claim_is_looked_up_decided_and_paid():
    model = stub(
        call_tool("get_claim", claim_id="CLM-1001"),
        call_tool("search_documents", text="repeat claims"),
        call_tool("read_section", document="claims_manual", section="9"),
        AIMessage(content="Approve $420.00."),
        decide("CLM-1001"),
    )
    line = claim_agent.work_claim(new_run(1), "CLM-1001", model)
    assert line == "CLM-1001: approved, $420.00 paid"
    decided = claim("CLM-1001")
    assert decided["status"] == "decided"
    assert decided["payment"]["amount"] == 420.0
    steps = recorded_steps("CLM-1001")
    sections = [step["text"] for step in steps if step["kind"] == "section"]
    assert sections == ["Claims manual 9. Repeat claims"]
    texts = [step["text"] for step in steps]
    assert texts.index('get_claim({"claim_id": "CLM-1001"})') == 0
    calls = [t for t in texts if t.startswith(("record_decision(", "pay_claim("))]
    assert [t.split("(")[0] for t in calls] == ["record_decision", "pay_claim"]
    assert texts[-1] == "pay_claim: paid $420.00"


def test_the_payout_tool_is_used_and_shown_as_a_step():
    model = stub(
        call_tool("get_claim", claim_id="CLM-1001"),
        call_tool(
            "calculate_payout",
            policy_id="POL-2001",
            benefit="baggage",
            lines=LINES,
            paid_by_others=0,
        ),
        AIMessage(content="Approve $250.00."),
        decide("CLM-1001", payout=250.0),
    )
    assert claim_agent.work_claim(new_run(1), "CLM-1001", model).endswith("paid")
    texts = [step["text"] for step in recorded_steps("CLM-1001")]
    assert any(t.startswith("calculate_payout: ") and "250.0" in t for t in texts)


def test_a_section_that_does_not_exist_is_not_recorded_as_read():
    model = stub(
        call_tool("read_section", document="claims_manual", section="99"),
        AIMessage(content="Nothing there."),
        decide("CLM-1001", "refer"),
    )
    claim_agent.work_claim(new_run(1), "CLM-1001", model)
    assert "section" not in [step["kind"] for step in recorded_steps("CLM-1001")]


def test_a_payout_over_the_limit_pauses_for_sign_off():
    saver = InMemorySaver()
    model = stub(*approve_and_pay("CLM-1002", payout=1250.0))
    line = claim_agent.work_claim(new_run(1), "CLM-1002", model, saver)
    assert line == "CLM-1002: approved $1,250.00, waiting for sign-off"
    held = claim("CLM-1002")
    assert held["status"] == "awaiting_sign_off"
    assert held["payment"] is None
    state = graph_state("CLM-1002", saver)
    assert state.next == ("wait_for_sign_off",)
    paused = state.interrupts[0].value
    assert paused["claim_id"] == "CLM-1002"
    assert paused["payout"] == 1250.0
    last = recorded_steps("CLM-1002")[-1]
    assert last["text"] == "The payout of $1,250.00 waits for a handler's sign-off."


def test_a_sign_off_resumes_the_claim_and_pays_it_once():
    saver = InMemorySaver()
    claim_agent.work_claim(
        new_run(1), "CLM-1002", stub(*approve_and_pay("CLM-1002", 1250.0)), saver
    )
    assert claim_agent.carry_on("CLM-1002", stub(), saver).endswith("sign-off")
    assert claim("CLM-1002")["status"] == "awaiting_sign_off"
    signed = sign_off("CLM-1002")
    assert signed["status"] == "in_progress"
    assert signed["payment"] is None
    model = stub()
    assert claim_agent.carry_on("CLM-1002", model, saver) == (
        "CLM-1002: approved, $1,250.00 paid"
    )
    assert not model.seen
    paid = claim("CLM-1002")
    assert paid["status"] == "decided"
    assert paid["payment"]["amount"] == 1250.0
    assert paid["payment"]["signed_off_by"] == "HND-01"
    assert paid["payment"]["signed_off_at"] == paid["sign_off"]["signed_off_at"]
    texts = [step["text"] for step in recorded_steps("CLM-1002")]
    assert "Signed off by Dana Whitfield." in texts
    again = claim_agent.carry_on("CLM-1002", stub(), saver)
    assert again == "CLM-1002: approved, $1,250.00 paid"
    assert claim("CLM-1002")["payment"] == paid["payment"]
    assert len([t for t in texts if t.startswith("pay_claim(")]) == 1


def test_declines_and_referrals_are_reported():
    run_id = new_run(2)
    declined = stub(AIMessage(content="No."), decide("CLM-1001", "decline"))
    referred = stub(AIMessage(content="Hmm."), decide("CLM-1002", "refer"))
    assert claim_agent.work_claim(run_id, "CLM-1001", declined) == "CLM-1001: declined"
    assert claim_agent.work_claim(run_id, "CLM-1002", referred) == "CLM-1002: referred"
    assert claim("CLM-1001")["payment"] is None


def test_a_refused_decision_goes_back_to_the_model():
    bad = call_tool(
        "ClaimDecision",
        rationale="No.",
        decision="decline",
        payout=50,
        policy_section="Section 3, Trip Delay",
    )
    model = stub(
        AIMessage(content="Decline."),
        bad,
        AIMessage(content="Fixing the payout."),
        decide("CLM-1001", "decline"),
    )
    line = claim_agent.work_claim(new_run(1), "CLM-1001", model)
    assert line == "CLM-1001: declined"
    texts = [step["text"] for step in recorded_steps("CLM-1001")]
    assert "record_decision: A decline or a refer has a payout of 0." in texts
    told = [m for m in model.seen[2] if isinstance(m, HumanMessage)]
    assert "refused your decision" in told[-1].content


def test_a_decision_already_recorded_is_taken_without_asking_the_model():
    run_id = new_run(1)
    main.call("POST", "/api/claims/CLM-1001/start", {"run_id": run_id})
    main.call("POST", "/api/claims/CLM-1001/decision", decision("CLM-1001"))
    model = stub(AIMessage(content="Looked it up."))
    line = claim_agent.carry_on("CLM-1001", model, InMemorySaver())
    assert line == "CLM-1001: approved, $420.00 paid"
    assert len(model.seen) == 1
    assert len(main.call("GET", "/api/claims/CLM-1001/steps")) == 3


def test_a_stopped_claim_carries_on_from_its_last_step():
    saver = InMemorySaver()
    run_id = new_run(1)
    first = stub(call_tool("get_claim", claim_id="CLM-1001"), KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        claim_agent.work_claim(run_id, "CLM-1001", first, saver)
    stopped = claim("CLM-1001")
    assert stopped["status"] == "in_progress"
    assert stopped["attempts"] == 0
    second = stub(AIMessage(content="Approve $420.00."), decide("CLM-1001"))
    line = claim_agent.carry_on("CLM-1001", second, saver)
    assert line == "CLM-1001: approved, $420.00 paid"
    assert len(second.seen) == 2
    assert any(m.type == "tool" for m in second.seen[0])
    done = claim("CLM-1001")
    assert done["attempts"] == 0
    assert done["thread_id"] == stopped["thread_id"]
    kinds = [s["text"].split("(")[0] for s in recorded_steps("CLM-1001")]
    assert kinds.count("record_decision") == 1
    assert kinds.count("get_claim") == 1


def test_a_model_error_is_retried():
    model = stub(ERROR, *approve_and_pay("CLM-1001"))
    assert claim_agent.work_claim(new_run(1), "CLM-1001", model).endswith("paid")


def test_a_failing_model_puts_the_claim_back():
    line = claim_agent.work_claim(new_run(1), "CLM-1001", stub(ERROR, ERROR, ERROR))
    assert line == (
        "CLM-1001: failed, back in the queue (attempt 1 of 2). "
        "The agent stopped with an error: model unavailable."
    )
    failed = claim("CLM-1001")
    assert failed["status"] == "waiting"
    assert failed["attempts"] == 1
    error = recorded_steps("CLM-1001")[-1]
    assert error["kind"] == "error"
    assert "model unavailable" in error["text"]


def test_a_second_failure_leaves_the_claim_for_a_handler():
    run_id = new_run(1)
    claim_agent.work_claim(run_id, "CLM-1001", stub(ERROR, ERROR, ERROR))
    line = claim_agent.work_claim(run_id, "CLM-1001", stub(ERROR, ERROR, ERROR))
    assert line.startswith("CLM-1001: failed twice, left for a handler.")
    assert claim("CLM-1001")["status"] == "needs_handler"


def test_each_attempt_gets_a_new_thread_and_a_clean_conversation():
    saver = InMemorySaver()
    run_id = new_run(1)
    claim_agent.work_claim(run_id, "CLM-1001", stub(ERROR, ERROR, ERROR), saver)
    first_thread = claim("CLM-1001")["thread_id"]
    model = stub(*approve_and_pay("CLM-1001"))
    claim_agent.work_claim(run_id, "CLM-1001", model, saver)
    second_thread = claim("CLM-1001")["thread_id"]
    assert first_thread == f"CLM-1001-{run_id}-1"
    assert second_thread == f"CLM-1001-{run_id}-2"
    assert [message.type for message in model.seen[0]] == ["system", "human"]
    assert claim("CLM-1001")["status"] == "decided"


def test_a_looping_agent_is_stopped(monkeypatch):
    assert claim_agent.MODEL_CALL_LIMIT == 30
    monkeypatch.setattr(claim_agent, "MODEL_CALL_LIMIT", 3)
    loop = [call_tool("get_claim", claim_id="CLM-1001") for _ in range(10)]
    line = claim_agent.work_claim(new_run(1), "CLM-1001", stub(*loop))
    assert line.startswith("CLM-1001: failed, back in the queue")
    assert "3 model calls" in line


def test_a_claim_is_never_decided_or_paid_twice():
    model = stub(*approve_and_pay("CLM-1001"))
    claim_agent.work_claim(new_run(1), "CLM-1001", model)
    line = claim_agent.work_claim(new_run(1), "CLM-1001", stub())
    assert line == "CLM-1001: not started. Claim CLM-1001 is not waiting."
    assert claim("CLM-1001")["payment"]["amount"] == 420.0


def test_instructions_come_from_the_backend(tmp_path, monkeypatch):
    (tmp_path / "claim.md").write_text("Decide claims carefully.", encoding="utf-8")
    (tmp_path / "queue.md").write_text("Run the queue.", encoding="utf-8")
    monkeypatch.setattr(backend, "INSTRUCTIONS", tmp_path)
    model = stub(*approve_and_pay("CLM-1001"))
    claim_agent.work_claim(new_run(1), "CLM-1001", model)
    assert model.seen[0][0].content == "Decide claims carefully."


def test_the_checkpointer_is_in_memory_without_a_database_url():
    checkpoints.checkpointer.cache_clear()
    assert isinstance(checkpoints.checkpointer(), InMemorySaver)


def test_the_pool_keeps_to_four_connections_with_langgraphs_settings():
    pool = checkpoints.make_pool("postgresql://u:p@db.example.com:5432/postgres")
    assert pool.max_size == 4
    assert pool.kwargs == checkpoints.SETTINGS
    assert checkpoints.SETTINGS["autocommit"] is True
    assert checkpoints.SETTINGS["prepare_threshold"] == 0
    assert checkpoints.SETTINGS["options"] == "-c search_path=undercover"
    assert checkpoints.SETTINGS["row_factory"] is dict_row


def test_the_command_line_works_one_claim(monkeypatch):
    monkeypatch.setattr(main, "model", lambda: stub(*approve_and_pay("CLM-1001")))
    assert cli(["--claim", "CLM-1001"]) == "CLM-1001: approved, $420.00 paid"


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    default = main.model()
    assert default.model_name == "deepseek/deepseek-v4.1-flash"
    assert default.reasoning_effort == "low"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    chosen = main.model()
    assert chosen.model_name == "openrouter/free"
    assert chosen.reasoning_effort is None


def test_the_agent_never_reads_the_data_folder():
    for path in Path(main.__file__).parent.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "data/" not in source
        assert "evals" not in source


def test_tracing_needs_langfuse_keys(monkeypatch):
    assert main.tracing("T-1") == {}
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    config = main.tracing("T-1")
    assert config.get("metadata") == {"langfuse_session_id": "T-1"}
    callbacks = config.get("callbacks")
    assert isinstance(callbacks, list)
    assert len(callbacks) == 1


def test_a_session_is_a_no_op_without_langfuse_keys_and_works_with_them(monkeypatch):
    with main.session("T-1"):
        pass
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    with main.session("T-1"):
        pass


def test_importing_the_agent_does_not_load_env():
    code = "import os, agent.main; print(os.getenv('LANGFUSE_SECRET_KEY'))"
    env = {k: v for k, v in os.environ.items() if not k.startswith("LANGFUSE")}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.stdout.strip() == "None", result.stderr
