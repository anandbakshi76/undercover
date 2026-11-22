"""The claim agent: a LangGraph graph that works one claim and reports in a line."""

import json
import threading
from typing import Annotated, Any, Literal, TypedDict
from urllib.parse import quote

import httpx
import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, RetryPolicy, interrupt
from pydantic import BaseModel, Field

from agent import checkpoints, main
from backend.payout import Benefit, Line

MODEL_CALL_LIMIT = 30
MAX_ATTEMPTS = 2
RECURSION_LIMIT = 100
LOCKS: dict[str, threading.Lock] = {}

Document = Literal["policy_wording", "claims_manual"]


@tool
def get_claim(claim_id: str) -> Any:
    """Get a claim: the customer's story, the items claimed and the documents."""
    return main.call("GET", f"/api/claims/{claim_id}")


@tool
def get_policy(policy_id: str) -> Any:
    """Get a policy with its plan's limits, its add-ons, the trip and travelers."""
    return main.call("GET", f"/api/policies/{policy_id}")


@tool
def get_claim_history(customer_id: str) -> Any:
    """Get the customer's earlier claims."""
    return main.call("GET", f"/api/customers/{customer_id}/claims")


@tool
def search_documents(text: str) -> Any:
    """Find the sections of the wording and the manual that mention a phrase."""
    return main.call("GET", f"/api/search/{quote(text, safe='')}")


@tool
def list_sections(document: Document) -> Any:
    """List the numbered sections of the policy wording or the claims manual."""
    return main.call("GET", f"/api/documents/{document}/sections")


@tool(response_format="content_and_artifact")
def read_section(document: Document, section: str) -> tuple[Any, str | None]:
    """Read one numbered section of the policy wording or the claims manual."""
    found = main.call(
        "GET", f"/api/documents/{document}/sections/{quote(section, safe='')}"
    )
    if "text" not in found:
        return found, None
    return found, f"{found['name']} {found['section']}. {found['title']}"


@tool(parse_docstring=True)
def calculate_payout(
    policy_id: str, benefit: Benefit, lines: list[Line], paid_by_others: float
) -> Any:
    """Work out the payout on an approved claim with UnderCover's payout rules.

    Args:
        policy_id: The customer's policy id
        benefit: The benefit the claim is paid under
        lines: The covered items and expenses, one for each line of the claim,
            with any excluded ones left out. A pair or a set counts as one item
        paid_by_others: Anything a carrier, hotel or other party has paid or
            refunded for the same loss, or 0
    """
    body = {
        "benefit": benefit,
        "lines": [line.model_dump() for line in lines],
        "paid_by_others": paid_by_others,
    }
    return main.call("POST", f"/api/policies/{policy_id}/payout", body)


TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    search_documents,
    list_sections,
    read_section,
    calculate_payout,
]


class ClaimDecision(BaseModel):
    """A claims handler's decision on one claim."""

    rationale: str = Field(
        description="2-3 sentences a handler can check, with the key facts and "
        "numbers, such as $1,250.00"
    )
    decision: Literal["approve", "decline", "refer"]
    payout: float = Field(
        description="The amount to pay in US dollars, or 0 for a decline or a referral"
    )
    policy_section: str = Field(
        description="The section of the wording the decision rests on, such as "
        '"Section 3, Trip Delay", adding the manual\'s section when it settled '
        "the claim"
    )


class ClaimState(TypedDict):
    """What the graph keeps for one claim."""

    messages: Annotated[list, add_messages]
    claim_id: str
    model_calls: int
    decision: str
    payout: float
    rationale: str
    approved_by: str


def post_step(claim_id: str, kind: str, text: str) -> None:
    main.call("POST", f"/api/claims/{claim_id}/steps", {"kind": kind, "text": text})


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def after_agent(state: ClaimState) -> str:
    """Run the tools the model asked for, or move on to the decision."""
    return "tools" if state["messages"][-1].tool_calls else "decide"


def after_decide(state: ClaimState) -> str:
    """Pay an approval, after a handler's sign-off if the claims system asks."""
    if not state["decision"]:
        return "agent"
    if state["decision"] != "approve":
        return END
    claim = main.call("GET", f"/api/claims/{state['claim_id']}")
    return "wait_for_sign_off" if claim["status"] == "awaiting_sign_off" else "pay"


class Nodes:
    """The graph's nodes, which share the model and the instructions."""

    def __init__(self, llm: BaseChatModel, instructions: str) -> None:
        self.system = SystemMessage(instructions)
        self.model_with_tools = llm.bind_tools(TOOLS)
        self.decider = llm.with_structured_output(
            ClaimDecision, method="function_calling"
        )

    def agent(self, state: ClaimState) -> dict[str, Any]:
        """Call the model with the conversation so far."""
        if state["model_calls"] >= MODEL_CALL_LIMIT:
            raise RuntimeError(f"The agent made {MODEL_CALL_LIMIT} model calls.")
        reply = self.model_with_tools.invoke([self.system, *state["messages"]])
        return {"messages": [reply], "model_calls": state["model_calls"] + 1}

    def decide(self, state: ClaimState) -> dict[str, Any]:
        """Turn the agent's work into a decision and record it."""
        claim_id = state["claim_id"]
        made = main.call("GET", f"/api/claims/{claim_id}")["decision"]
        if made:
            return {
                "decision": made["decision"],
                "payout": made["payout"],
                "rationale": made["reason"],
            }
        asked = [self.system, *state["messages"], HumanMessage("Record your decision.")]
        result = ClaimDecision.model_validate(self.decider.invoke(asked))
        body = {
            "decision": result.decision,
            "payout": result.payout,
            "reason": result.rationale,
            "policy_section": result.policy_section,
        }
        recorded = main.call("POST", f"/api/claims/{claim_id}/decision", body)
        post_step(claim_id, "tool_call", f"record_decision({json.dumps(body)})")
        calls = state["model_calls"] + 1
        if "detail" in recorded:
            post_step(claim_id, "tool_result", f"record_decision: {recorded['detail']}")
            refusal = f"The claims system refused your decision: {recorded['detail']}"
            return {
                "messages": [HumanMessage(refusal + " Check the claim and try again.")],
                "model_calls": calls,
                "decision": "",
            }
        post_step(claim_id, "tool_result", f"record_decision: {result.decision}")
        return {
            "model_calls": calls,
            "decision": result.decision,
            "payout": result.payout,
            "rationale": result.rationale,
        }

    def wait_for_sign_off(self, state: ClaimState) -> dict[str, Any]:
        """Wait for a handler to sign off a payout over the limit."""
        answer = interrupt(
            {
                "claim_id": state["claim_id"],
                "payout": state["payout"],
                "rationale": state["rationale"],
            }
        )
        text = f"Signed off by {answer['handler_name']}."
        post_step(state["claim_id"], "reply", text)
        return {"approved_by": answer["handler_id"]}

    def pay(self, state: ClaimState) -> None:
        """Pay the approved payout. The claims system treats a repeat as done."""
        claim_id, amount = state["claim_id"], state["payout"]
        post_step(claim_id, "tool_call", f"pay_claim({claim_id}, {amount})")
        paid = main.call("POST", f"/api/claims/{claim_id}/payment", {"amount": amount})
        if "detail" in paid:
            post_step(claim_id, "tool_result", f"pay_claim: {paid['detail']}")
            raise RuntimeError(paid["detail"])
        post_step(claim_id, "tool_result", f"pay_claim: paid {money(amount)}")


def claim_graph(
    llm: BaseChatModel, saver: BaseCheckpointSaver, instructions: str
) -> Any:
    """Build the claim graph with its checkpointer."""
    nodes = Nodes(llm, instructions)
    delay = main.RETRY_DELAY
    provider = RetryPolicy(max_attempts=3, initial_interval=delay, retry_on=Exception)
    backend = RetryPolicy(
        max_attempts=3,
        initial_interval=delay,
        retry_on=(httpx.TransportError, httpx.HTTPStatusError, openai.APIError),
    )
    builder = StateGraph(ClaimState)
    builder.add_node("agent", nodes.agent, retry_policy=provider)
    builder.add_node("tools", ToolNode(TOOLS), retry_policy=backend)
    builder.add_node("decide", nodes.decide, retry_policy=provider)
    builder.add_node("wait_for_sign_off", nodes.wait_for_sign_off)
    builder.add_node("pay", nodes.pay, retry_policy=backend)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", after_agent, ["tools", "decide"])
    builder.add_edge("tools", "agent")
    builder.add_conditional_edges(
        "decide", after_decide, ["agent", "wait_for_sign_off", "pay", END]
    )
    builder.add_edge("wait_for_sign_off", "pay")
    builder.add_edge("pay", END)
    return builder.compile(checkpointer=saver)


def stream_steps(graph: Any, graph_input: Any, config: Any, claim_id: str) -> None:
    """Run the graph and record each step of the agent's work."""
    for update in graph.stream(
        graph_input, config, stream_mode="updates", durability="sync"
    ):
        for name, node in update.items():
            if name == "__interrupt__":
                continue
            for message in (node or {}).get("messages", []):
                for kind, text in main.steps(message):
                    post_step(claim_id, kind, text)


def next_input(graph: Any, config: Any, claim: dict[str, Any]) -> Any:
    """Say how to carry on a claim's thread: its first input, nothing, or a sign-off."""
    state = graph.get_state(config)
    if state.interrupts:
        sign_off = claim["sign_off"]
        if sign_off is None:
            return "wait"
        handlers = main.call("GET", "/api/handlers")
        name = next(
            h["name"] for h in handlers if h["handler_id"] == sign_off["handler_id"]
        )
        return Command(
            resume={"handler_id": sign_off["handler_id"], "handler_name": name}
        )
    if state.next:
        return None
    if state.values:
        return "done"
    return {
        "messages": [HumanMessage(f"Decide claim {claim['claim_id']}.")],
        "claim_id": claim["claim_id"],
        "model_calls": 0,
        "decision": "",
        "payout": 0.0,
        "rationale": "",
        "approved_by": "",
    }


def outcome(claim: dict[str, Any]) -> str:
    """Describe a decided claim's decision and payment."""
    decision = claim["decision"]
    if decision["decision"] != "approve":
        return {"decline": "declined", "refer": "referred"}[decision["decision"]]
    payout = money(decision["payout"])
    if claim["status"] == "awaiting_sign_off":
        return f"approved {payout}, waiting for sign-off"
    if claim["payment"]:
        return f"approved, {payout} paid"
    return f"approved {payout}, not paid"


def report(claim: dict[str, Any], problem: str) -> str:
    """Describe where a claim ended up in one line."""
    claim_id, status = claim["claim_id"], claim["status"]
    if status == "waiting":
        return (
            f"{claim_id}: failed, back in the queue "
            f"(attempt {claim['attempts']} of {MAX_ATTEMPTS}). {problem}"
        )
    if status == "needs_handler":
        return f"{claim_id}: failed twice, left for a handler. {problem}"
    if claim["decision"] is None:
        return f"{claim_id}: in progress. {problem}"
    return f"{claim_id}: {outcome(claim)}"


def drive(graph: Any, config: Any, claim: dict[str, Any]) -> None:
    """Carry on a claim's thread, and note it when it stops for a sign-off."""
    graph_input = next_input(graph, config, claim)
    if graph_input in ("wait", "done"):
        return
    stream_steps(graph, graph_input, config, claim["claim_id"])
    if graph.get_state(config).interrupts:
        held = main.call("GET", f"/api/claims/{claim['claim_id']}")
        payout = money(held["decision"]["payout"])
        text = f"The payout of {payout} waits for a handler's sign-off."
        post_step(claim["claim_id"], "reply", text)


def carry_on(
    claim_id: str,
    llm: BaseChatModel | None = None,
    saver: BaseCheckpointSaver | None = None,
) -> str:
    """Work a claim from its last checkpoint, one thread at a time per claim."""
    with LOCKS.setdefault(claim_id, threading.Lock()):
        return work_thread(claim_id, llm, saver)


def work_thread(
    claim_id: str, llm: BaseChatModel | None, saver: BaseCheckpointSaver | None
) -> str:
    """Work a claim from its last checkpoint, release it if it fails, and report."""
    claim = main.call("GET", f"/api/claims/{claim_id}")
    thread_id = claim["thread_id"]
    instructions = main.call("GET", "/api/instructions")["claim"]
    graph = claim_graph(
        llm or main.model(), saver or checkpoints.checkpointer(), instructions
    )
    config = (
        main.tracing(thread_id)
        | {"configurable": {"thread_id": thread_id}}
        | {"recursion_limit": RECURSION_LIMIT}
    )
    problem = "The agent finished without recording a decision."
    try:
        with main.session(thread_id):
            drive(graph, config, claim)
    except Exception as error:
        problem = f"The agent stopped with an error: {error}."
    claim = main.call("POST", f"/api/claims/{claim_id}/release", {"problem": problem})
    return report(claim, problem)


def work_claim(
    run_id: str,
    claim_id: str,
    llm: BaseChatModel | None = None,
    saver: BaseCheckpointSaver | None = None,
) -> str:
    """Start a waiting claim in a run, work it with a claim agent, and report."""
    worked = main.call("GET", f"/api/claims/{claim_id}")
    if worked.get("run_id") == run_id and worked.get("decision") is not None:
        return report(worked, "")
    started = main.call("POST", f"/api/claims/{claim_id}/start", {"run_id": run_id})
    if "detail" in started:
        return f"{claim_id}: not started. {started['detail']}"
    return carry_on(claim_id, llm, saver)
