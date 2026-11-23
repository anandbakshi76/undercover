"""The dispatcher: a graph with no model that works a run's claims N at once."""

import operator
from typing import Annotated, TypedDict

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

from agent import claim, main

MAX_WAITS = 4


class Dispatch(TypedDict):
    """What the dispatcher keeps for one run."""

    run_id: str
    claims: list[str]
    results: Annotated[list[str], operator.add]


class Worker(TypedDict):
    """What the worker is given: one claim in one run."""

    run_id: str
    claim_id: str


def list_claims(state: Dispatch) -> dict[str, list[str]]:
    """The run's claims to work, leaving out any being resumed after a sign-off."""
    ids = main.call("GET", f"/api/runs/{state['run_id']}/claims-to-work")
    return {"claims": [claim_id for claim_id in ids if not claim.is_working(claim_id)]}


def fan_out(state: Dispatch) -> list[Send]:
    """Send each claim to a claim agent."""
    return [
        Send("work_claim", {"run_id": state["run_id"], "claim_id": claim_id})
        for claim_id in state["claims"]
    ]


def work_claim(worker: Worker) -> dict[str, list[str]]:
    """Work one claim, and once more if it failed and went back in the queue."""
    claim_id, run_id = worker["claim_id"], worker["run_id"]
    line = claim.work_claim(claim_id, run_id)
    if main.call("GET", f"/api/claims/{claim_id}")["status"] == "waiting":
        line = claim.work_claim(claim_id, run_id)
    return {"results": [line]}


def leave_in_progress(worker: Worker, error: NodeError) -> dict[str, list[str]]:
    """When the retries run out, leave the claim in progress for the next dispatch."""
    line = (
        f"{worker['claim_id']}: in progress, left for the next dispatch. {error.error}"
    )
    return {"results": [line]}


def build() -> StateGraph:
    """Build the dispatcher: list the claims, fan out, work each claim."""
    waits = RetryPolicy(
        max_attempts=MAX_WAITS,
        initial_interval=5 * main.RETRY_DELAY,
        retry_on=(ModelRateLimitError, ModelTimeoutError),
    )
    builder = StateGraph(Dispatch)
    builder.add_node("list_claims", list_claims)
    builder.add_node(  # pyright: ignore[reportCallIssue]
        "work_claim",
        work_claim,  # pyright: ignore[reportArgumentType]
        input_schema=Worker,
        retry_policy=waits,
        error_handler=leave_in_progress,  # pyright: ignore[reportArgumentType]
    )
    builder.add_edge(START, "list_claims")
    builder.add_conditional_edges("list_claims", fan_out, ["work_claim"])
    builder.add_edge("work_claim", END)
    return builder


def dispatch(run_id: str, concurrency: int) -> list[str]:
    """Work a run's claims with up to N claim agents at once, and report each."""
    graph = build().compile()
    result = graph.invoke(
        {"run_id": run_id, "claims": [], "results": []},
        {"max_concurrency": concurrency},
    )
    return result["results"]
