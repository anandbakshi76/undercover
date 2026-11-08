from pathlib import Path

from langchain_core.messages import AIMessage

from agent import claim as claim_agent
from agent import main
from agent.__main__ import cli
from agent.tests.stubs import (
    approve_and_pay,
    call_tool,
    claim,
    decision,
    new_run,
    recorded_steps,
    stub,
)
from backend import main as backend

ERROR = RuntimeError("model unavailable")


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
    assert tools.record_decision.invoke(decision("CLM-1001"))["status"] == "decided"
    paid = tools.pay_claim.invoke({"claim_id": "CLM-1001", "amount": 420.0})
    assert paid["payment"]["amount"] == 420.0


def test_a_claim_is_looked_up_decided_and_paid():
    model = stub(
        call_tool("get_claim", claim_id="CLM-1001"),
        call_tool("search_documents", text="repeat claims"),
        call_tool("read_section", document="claims_manual", section="9"),
        call_tool("record_decision", **decision("CLM-1001")),
        call_tool("pay_claim", claim_id="CLM-1001", amount=420.0),
        AIMessage(content="Approved and paid $420.00."),
    )
    line = claim_agent.work_claim(new_run(1), "CLM-1001", model)
    assert line == "CLM-1001: approved, $420.00 paid"
    decided = claim("CLM-1001")
    assert decided["status"] == "decided"
    assert decided["payment"]["amount"] == 420.0
    steps = recorded_steps("CLM-1001")
    sections = [step["text"] for step in steps if step["kind"] == "section"]
    assert sections == ["Claims manual 9. Repeat claims"]
    assert steps[-1] == steps[-1] | {"kind": "reply"}


def test_a_section_that_does_not_exist_is_not_recorded_as_read():
    model = stub(
        call_tool("read_section", document="claims_manual", section="99"),
        AIMessage(content="Nothing there."),
    )
    claim_agent.decide("CLM-1001", "t1", model)
    assert "section" not in [step["kind"] for step in recorded_steps("CLM-1001")]


def test_a_payout_over_the_limit_waits_for_sign_off():
    model = stub(*approve_and_pay("CLM-1002", payout=1250.0))
    line = claim_agent.work_claim(new_run(1), "CLM-1002", model)
    assert line == "CLM-1002: approved $1,250.00, waiting for sign-off"
    assert claim("CLM-1002")["payment"] is None
    refusal = [s for s in recorded_steps("CLM-1002") if "pay_claim:" in s["text"]]
    assert "needs a handler's sign-off" in refusal[0]["text"]


def test_declines_and_referrals_are_reported():
    run_id = new_run(2)
    declined = stub(
        call_tool("record_decision", **decision("CLM-1001", "decline")),
        AIMessage(content="Declined."),
    )
    referred = stub(
        call_tool("record_decision", **decision("CLM-1002", "refer")),
        AIMessage(content="Referred."),
    )
    assert claim_agent.work_claim(run_id, "CLM-1001", declined) == "CLM-1001: declined"
    assert claim_agent.work_claim(run_id, "CLM-1002", referred) == "CLM-1002: referred"


def test_an_approval_left_unpaid_is_reported():
    model = stub(
        call_tool("record_decision", **decision("CLM-1001")),
        AIMessage(content="Approved."),
    )
    line = claim_agent.work_claim(new_run(1), "CLM-1001", model)
    assert line == "CLM-1001: approved $420.00, not paid"


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


def test_a_run_without_a_decision_puts_the_claim_back():
    line = claim_agent.work_claim(new_run(1), "CLM-1001", stub(AIMessage("Hello.")))
    assert line.startswith("CLM-1001: failed, back in the queue")
    assert line.endswith("The agent finished without recording a decision.")
    assert claim("CLM-1001")["status"] == "waiting"


def test_a_second_failure_leaves_the_claim_for_a_handler():
    run_id = new_run(1)
    claim_agent.work_claim(run_id, "CLM-1001", stub(AIMessage("Hello.")))
    line = claim_agent.work_claim(run_id, "CLM-1001", stub(AIMessage("Hello.")))
    assert line.startswith("CLM-1001: failed twice, left for a handler.")
    assert claim("CLM-1001")["status"] == "needs_handler"


def test_a_looping_agent_is_stopped(monkeypatch):
    monkeypatch.setattr(claim_agent, "MODEL_CALL_LIMIT", 3)
    loop = [call_tool("get_claim", claim_id="CLM-1001") for _ in range(10)]
    line = claim_agent.work_claim(new_run(1), "CLM-1001", stub(*loop))
    assert line.startswith("CLM-1001: failed, back in the queue")
    assert "limit" in line.lower()


def test_each_attempt_starts_from_a_clean_conversation():
    model = stub(AIMessage("Hello."), *approve_and_pay("CLM-1001"))
    run_id = new_run(1)
    claim_agent.work_claim(run_id, "CLM-1001", model)
    claim_agent.work_claim(run_id, "CLM-1001", model)
    second_attempt = model.seen[1]
    assert [message.type for message in second_attempt] == ["system", "human"]
    assert claim("CLM-1001")["status"] == "decided"


def test_a_decided_claim_is_never_decided_or_paid_twice():
    model = stub(
        call_tool("record_decision", **decision("CLM-1001")),
        call_tool("record_decision", **decision("CLM-1001", "decline")),
        AIMessage(content="Done."),
    )
    claim_agent.decide("CLM-1001", "t1", model)
    refusal = [s for s in recorded_steps("CLM-1001") if s["kind"] == "tool_result"][1]
    assert "has already been decided" in refusal["text"]
    assert claim("CLM-1001")["decision"]["decision"] == "approve"
    line = claim_agent.work_claim(new_run(1), "CLM-1001", stub())
    assert line == "CLM-1001: not started. Claim CLM-1001 is not waiting."


def test_instructions_come_from_the_backend(tmp_path, monkeypatch):
    (tmp_path / "claim.md").write_text("Decide claims carefully.", encoding="utf-8")
    monkeypatch.setattr(backend, "INSTRUCTIONS", tmp_path)
    model = stub(AIMessage(content="Done."))
    claim_agent.decide("CLM-1001", "t1", model)
    assert model.seen[0][0].content == "Decide claims carefully."


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
