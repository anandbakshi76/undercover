"""The queue agent: plans a run as a to-do list and hands claims to claim agents."""

import threading
from typing import Any, Literal, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRetryMiddleware,
    hook_config,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.base import BaseCheckpointSaver

from agent import checkpoints, claim, main

MAX_CHECKS = 3
ACTIVE: set[str] = set()


class Todo(TypedDict):
    content: str
    status: Literal["to_do", "in_progress", "done", "left_for_handler"]


def get_run(run_id: str) -> dict[str, Any]:
    return main.call("GET", f"/api/runs/{run_id}")


def queue_tools(run_id: str, llm: BaseChatModel) -> list[BaseTool]:
    """The queue agent's tools, bound to one run."""
    one_at_a_time = threading.Lock()

    @tool
    def write_todos(todos: list[Todo]) -> str:
        """Replace the to-do list for this run, with every item and its status."""
        saved = main.call("PUT", f"/api/runs/{run_id}/todos", {"todos": todos})
        if "detail" in saved:
            return f"The to-do list was not saved: {saved['detail']}"
        return f"The to-do list is saved, with {len(todos)} items."

    @tool
    def list_waiting_claims() -> dict[str, Any]:
        """List the waiting claims, the run's cap and how many claims it has left."""
        run = get_run(run_id)
        return {
            "claims": main.call("GET", "/api/queue/waiting"),
            "claim_limit": run["claim_limit"],
            "claims_worked": run["claims_worked"],
            "claims_left": run["claims_left"],
        }

    @tool
    def work_claim(claim_id: str) -> str:
        """Hand one claim to a new claim agent, and get back a line on how it ended."""
        with one_at_a_time:
            return claim.work_claim(run_id, claim_id, llm)

    return [write_todos, list_waiting_claims, work_claim]


class MissedClaimCheck(AgentMiddleware):
    """When the queue agent tries to finish, send it back to any claims it missed."""

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.checks = 0

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        if self.checks >= MAX_CHECKS:
            return None
        missed = get_run(self.run_id)["waiting_in_reach"]
        if not missed:
            return None
        self.checks += 1
        reminder = (
            "Before you finish: these waiting claims are still within this run's cap: "
            f"{', '.join(missed)}. Call list_waiting_claims to see how many claims "
            "you have left, work them, then finish with your summary."
        )
        return {"messages": [HumanMessage(content=reminder)], "jump_to": "model"}


def work_queue(run_id: str, llm: BaseChatModel, saver: BaseCheckpointSaver) -> str:
    """Run the queue agent on a run, and return its summary for the handlers.

    A run that was stopped carries on from its last checkpoint.
    """
    instructions = main.call("GET", "/api/instructions")["queue"]
    limit = get_run(run_id)["claim_limit"]
    agent = create_agent(
        llm,
        tools=queue_tools(run_id, llm),
        system_prompt=instructions,
        middleware=[
            ModelRetryMiddleware(
                max_retries=2, on_failure="error", initial_delay=main.RETRY_DELAY
            ),
            MissedClaimCheck(run_id),
        ],
        checkpointer=saver,
    )
    config = (
        main.tracing(run_id)
        | {"configurable": {"thread_id": run_id}}
        | {"recursion_limit": 100}
    )
    state = agent.get_state(config)
    claims = "claim" if limit == 1 else "claims"
    graph_input: Any = {
        "messages": [
            {
                "role": "user",
                "content": f"Work the queue. This run is capped at {limit} {claims}.",
            }
        ]
    }
    if state.next:
        graph_input = None
    elif state.values:
        return state.values["messages"][-1].text
    with main.session(run_id):
        result = agent.invoke(graph_input, config, durability="sync")
    return result["messages"][-1].text


def run_queue(
    run_id: str,
    llm: BaseChatModel | None = None,
    saver: BaseCheckpointSaver | None = None,
) -> str:
    """Run the queue agent, then record the run as finished or failed."""
    ACTIVE.add(run_id)
    try:
        end = {
            "status": "finished",
            "summary": work_queue(
                run_id, llm or main.model(), saver or checkpoints.checkpointer()
            ),
        }
    except Exception as error:
        end = {
            "status": "failed",
            "summary": f"The queue agent stopped with an error: {error}.",
        }
    finally:
        ACTIVE.discard(run_id)
    main.call("POST", f"/api/runs/{run_id}/finish", end)
    return end["summary"]
