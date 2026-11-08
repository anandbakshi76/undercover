"""The claim agent: works one claim from a clean start and reports in a line."""

from typing import Any, Literal
from urllib.parse import quote

import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool

from agent import main

MODEL_CALL_LIMIT = 40
MAX_ATTEMPTS = 2

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


@tool
def record_decision(
    claim_id: str,
    decision: Literal["approve", "decline", "refer"],
    payout: float,
    reason: str,
    policy_section: str,
) -> Any:
    """Record the decision on a claim with the payout, the reason and the section."""
    body = {
        "decision": decision,
        "payout": payout,
        "reason": reason,
        "policy_section": policy_section,
    }
    return main.call("POST", f"/api/claims/{claim_id}/decision", body)


@tool
def pay_claim(claim_id: str, amount: float) -> Any:
    """Pay an approved claim its payout."""
    return main.call("POST", f"/api/claims/{claim_id}/payment", {"amount": amount})


TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    search_documents,
    list_sections,
    read_section,
    record_decision,
    pay_claim,
]


def middleware() -> list[Any]:
    """Retry the model and the backend on errors, and stop a claim agent that loops."""
    return [
        ModelRetryMiddleware(
            max_retries=2, on_failure="error", initial_delay=main.RETRY_DELAY
        ),
        ToolRetryMiddleware(
            max_retries=2,
            retry_on=(httpx.TransportError, httpx.HTTPStatusError),
            initial_delay=main.RETRY_DELAY,
        ),
        ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT, exit_behavior="error"),
    ]


def decide(claim_id: str, thread_id: str, llm: BaseChatModel | None = None) -> str:
    """Run a new claim agent on one claim, record each step, and return its reply."""
    instructions = main.call("GET", "/api/instructions")["claim"]
    agent = create_agent(
        llm or main.model(),
        tools=TOOLS,
        system_prompt=instructions,
        middleware=middleware(),
    )
    reply = ""
    for update in agent.stream(
        {"messages": [{"role": "user", "content": f"Decide claim {claim_id}."}]},
        config={"configurable": {"thread_id": thread_id}},
        stream_mode="updates",
    ):
        for node in update.values():
            for message in (node or {}).get("messages", []):
                for kind, text in main.steps(message):
                    main.call(
                        "POST",
                        f"/api/claims/{claim_id}/steps",
                        {"kind": kind, "text": text},
                    )
                    if kind == "reply":
                        reply = text
    return reply


def money(amount: float) -> str:
    return f"${amount:,.2f}"


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
    return f"{claim_id}: {outcome(claim)}"


def work_started(run_id: str, claim_id: str, llm: BaseChatModel | None = None) -> str:
    """Work a claim already started in a run, release it if it fails, and report."""
    attempt = main.call("GET", f"/api/claims/{claim_id}")["attempts"] + 1
    try:
        decide(claim_id, f"{run_id}-{claim_id}-{attempt}", llm)
        problem = "The agent finished without recording a decision."
    except Exception as error:
        problem = f"The agent stopped with an error: {error}."
    claim = main.call("POST", f"/api/claims/{claim_id}/release", {"problem": problem})
    return report(claim, problem)


def work_claim(run_id: str, claim_id: str, llm: BaseChatModel | None = None) -> str:
    """Start a waiting claim in a run, work it with a new claim agent, and report."""
    started = main.call("POST", f"/api/claims/{claim_id}/start", {"run_id": run_id})
    if "detail" in started:
        return f"{claim_id}: not started. {started['detail']}"
    return work_started(run_id, claim_id, llm)
