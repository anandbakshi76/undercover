"""The improver: an agent that proposes better versions of the claim agent."""

import contextvars
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
    hook_config,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import SecretStr

from agent import checkpoints, dispatcher, improver_api, main

IMPROVER_MODEL = "openai/gpt-6-luna"
IMPROVER_EFFORT = "medium"
MODEL_CALL_LIMIT = 40
MODEL_TIMEOUT = 300
RECURSION_LIMIT = 100
SETS = ["evaluation", "held_out", "held_out"]
PROPOSALS = ["propose_change", "propose_recipe"]

INSTRUCTIONS = (Path(__file__).parent / "improver.md").read_text(encoding="utf-8")


def improver_model() -> ChatOpenAI:
    """Return the improver's model, or MODEL_NAME if it is set."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or IMPROVER_MODEL,
        base_url=main.OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else IMPROVER_EFFORT,
        timeout=MODEL_TIMEOUT,
        model_kwargs={"parallel_tool_calls": False},
    )


def not_allowed(call: Any, allowed: list[dict[str, Any]]) -> str | None:
    """Say why a proposal's model and effort aren't allowed, or None if they are."""
    efforts = {item["model"]: item["reasoning_efforts"] for item in allowed}
    model, effort = call["args"].get("model"), call["args"].get("reasoning_effort")
    if effort in efforts.get(model, []):
        return None
    return f"{model} at {effort} isn't allowed. Choose from the models allowed."


class Validate(AgentMiddleware):
    """Send a proposal outside the models allowed back, before a handler sees it."""

    def __init__(self, round_id: str) -> None:
        super().__init__()
        self.round_id = round_id

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Answer a change that names a model or effort that isn't allowed."""
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            return None
        allowed = improver_api.view(self.round_id)["models_allowed"]
        replies = []
        for call in last.tool_calls:
            problem = call["name"] == "propose_change" and not_allowed(call, allowed)
            if problem:
                replies.append(ToolMessage(problem, tool_call_id=call["id"] or ""))
        if not replies:
            return None
        return {"messages": replies, "jump_to": "model"}


class Watch(AgentMiddleware):
    """Stop a round that was stopped or has spent its limit, and count its calls."""

    def __init__(self, round_id: str) -> None:
        super().__init__()
        self.round_id = round_id

    @hook_config(can_jump_to=["end"])
    def before_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, Any] | None:
        """End the improver's turn when the round is stopped or over its limit."""
        shown = improver_api.round_state(self.round_id)
        if shown.get("status") != "working" or shown["spent"] >= shown["limit"]:
            return {"jump_to": "end"}
        return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Record what the model call cost, before any approval pause can skip it."""
        response = handler(request)
        for reply in response.result:
            usage = reply.response_metadata.get("token_usage")
            if usage:
                improver_api.record_call(
                    self.round_id,
                    {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "cost": usage.get("cost") or 0.0,
                    },
                )
        return response


def evaluate(round_id: str, version: int, evaluation_set: str, concurrency: int) -> str:
    """Run one whole-set evaluation of a version for a round, and say how it ended."""
    config = main.call("GET", f"/api/configurations/{version}")
    model, effort = main.model_settings(config)
    run = improver_api.start_evaluation(
        round_id,
        {
            "concurrency": concurrency,
            "evaluation_set": evaluation_set,
            "version": version,
            "model": model,
            "reasoning_effort": effort,
        },
    )
    if "detail" in run:
        return run["detail"]
    status = "finished"
    try:
        dispatcher.dispatch(run["run_id"], concurrency)
    except Exception:
        status = "failed"
    main.call("POST", f"/api/runs/{run['run_id']}/finish", {"status": status})
    return status


def apart(work: Callable[[], str]) -> str:
    """Run work in a fresh context, so its graphs don't save into the improver's."""
    return contextvars.Context().run(work)


def make_tools(round_id: str) -> list[Any]:
    """Make the improver's four tools for one round."""

    @tool
    def read_results() -> str:
        """Read your version, its scores, mistakes, past rounds and limits."""
        return json.dumps(improver_api.view(round_id), indent=2, default=str)

    @tool(parse_docstring=True)
    def propose_change(
        instructions: str, model: str, reasoning_effort: str, reason: str
    ) -> str:
        """Propose a new version of the claim agent's configuration, for a handler.

        Args:
            instructions: The claim agent's complete instructions
            model: One of the models allowed
            reasoning_effort: A reasoning level allowed for that model
            reason: Why this change should improve the scores
        """
        tried = improver_api.round_state(round_id)["version_tried"]
        return f"Approved and saved as version {tried}. Run the evaluation next."

    @tool
    def run_evaluation() -> str:
        """Evaluate your proposed version, then see if it is kept or rolled back."""
        shown = improver_api.round_state(round_id)
        version = shown["version_tried"]
        if version is None or shown["verdict"]:
            return "There is no new version to evaluate. Propose a change first."
        cost = improver_api.estimate(round_id, version)
        if cost["estimate"] > cost["left"]:
            return (
                f"Refused: the evaluations could cost about ${cost['estimate']:.2f} "
                f"and ${cost['left']:.2f} of the round's spending is left."
            )
        size = shown["settings"]["improver_concurrency"]
        for number, evaluation_set in enumerate(SETS):
            improver_api.set_stage(round_id, "evaluating", number)
            apart(lambda s=evaluation_set: evaluate(round_id, version, s, size))
        done = improver_api.conclude(round_id)
        if "detail" in done:
            return done["detail"]
        scores = {"held_out_before": done["before"], "held_out_after": done["after"]}
        return (
            f"{done['verdict']} version {version}: {done['why']}.\n"
            f"{json.dumps(scores, indent=2)}"
        )

    @tool(parse_docstring=True)
    def propose_recipe(recipe: str, reason: str) -> str:
        """Propose a new version of your recipe, for a handler to review.

        Args:
            recipe: The complete new recipe
            reason: What this round showed, and why the recipe should improve
        """
        return "Approved and saved as the next recipe version."

    return [read_results, propose_change, run_evaluation, propose_recipe]


def middleware(round_id: str) -> list[Any]:
    """The improver's middleware, in the order it is listed.

    A model's reply meets the hooks after it in reverse order, so Validate, the
    last, sends a bad proposal back before the approval pauses for a handler.
    """
    return [
        Watch(round_id),
        ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT, exit_behavior="end"),
        HumanInTheLoopMiddleware(
            interrupt_on={
                name: {"allowed_decisions": ["approve", "reject"]} for name in PROPOSALS
            }
        ),
        Validate(round_id),
    ]


def build(round_id: str, recipe: str) -> CompiledStateGraph:
    """Build the improver for a round, with a recipe as its instructions."""
    return create_agent(
        model=improver_model(),
        tools=make_tools(round_id),
        system_prompt=INSTRUCTIONS.format(recipe=recipe),
        middleware=middleware(round_id),
        checkpointer=checkpoints.saver(),
    )


def record_proposal(round_id: str, request: dict[str, Any]) -> None:
    """Record what the improver proposed, so a handler can decide on it."""
    args = request["args"]
    if request["name"] == "propose_change":
        improver_api.record_change(round_id, args)
    else:
        improver_api.record_recipe(round_id, args)


def first_evaluations(round_id: str) -> None:
    """Run the current version's missing whole-set evaluations, before the improver."""
    shown = improver_api.round_state(round_id)
    needed = shown["evaluations_needed"]
    sets = ["evaluation"] * needed["evaluation"] + ["held_out"] * needed["held_out"]
    for number, evaluation_set in enumerate(sets):
        improver_api.set_stage(round_id, "first_evaluations", number)
        evaluate(
            round_id,
            shown["start_version"],
            evaluation_set,
            shown["settings"]["improver_concurrency"],
        )


def drive_round(round_id: str, command: Command | None) -> None:
    """Carry a round on until the improver ends or waits for a handler."""
    shown = improver_api.round_state(round_id)
    if command is None:
        first_evaluations(round_id)
        improver_api.set_stage(round_id, "improver_working", 0)
    recipe = main.call("GET", f"/api/recipes/{shown['recipe_version']}")["recipe"]
    graph = build(round_id, recipe)
    config = (
        main.tracing(round_id)
        | {"configurable": {"thread_id": shown["thread_id"]}}
        | {"recursion_limit": RECURSION_LIMIT}
    )
    start_with = command or {"messages": [HumanMessage("Begin the round.")]}
    with main.session(round_id):
        result = graph.invoke(start_with, config, durability="sync")
    waiting = result.get("__interrupt__")
    if waiting:
        record_proposal(round_id, waiting[0].value["action_requests"][0])
    else:
        improver_api.finish(round_id)
