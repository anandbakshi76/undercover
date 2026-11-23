"""Stub models that give scripted replies in place of a real model."""

import re

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from agent import main
from backend import db
from backend import main as backend

USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.01}


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies and remembers what it was sent."""

    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        reply = next(self.messages)
        if isinstance(reply, BaseException):
            raise reply
        self.messages = iter([reply, *self.messages])
        result = super()._generate(messages, stop, run_manager, **kwargs)
        for generation in result.generations:
            generation.message.response_metadata["token_usage"] = USAGE
        return result


def stub(*replies):
    """A stub model; a reply that is an exception is raised in its turn."""
    return StubModel(messages=iter(replies))


def call_tool(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


def decision(claim_id, kind="approve", payout=420.0):
    return {
        "claim_id": claim_id,
        "decision": kind,
        "payout": payout if kind == "approve" else 0,
        "reason": "The flight was canceled for a crew problem. The costs are covered.",
        "policy_section": "Section 3, Trip Delay",
    }


def decide(claim_id, kind="approve", payout=420.0):
    """The model's reply when the graph asks it to record its decision."""
    made = decision(claim_id, kind, payout)
    return call_tool(
        "ClaimDecision",
        rationale=made["reason"],
        decision=kind,
        payout=made["payout"],
        policy_section=made["policy_section"],
    )


def approve_and_pay(claim_id, payout=420.0):
    """The replies of a claim agent that looks the claim up, then approves it."""
    return [
        call_tool("get_claim", claim_id=claim_id),
        AIMessage(content=f"Approve {claim_id}."),
        decide(claim_id, payout=payout),
    ]


def claim(claim_id):
    return main.call("GET", f"/api/claims/{claim_id}")


def recorded_steps(claim_id):
    return main.call("GET", f"/api/claims/{claim_id}/steps")


def new_run(limit=1):
    """A run for one claim, as the Run agent button starts it."""
    with db.connect() as connection:
        return backend.new_run(connection, "claim", limit)


def new_dispatch(concurrency=3):
    """A dispatch, as the agent's service creates it."""
    body = {"concurrency": concurrency, "model": "stub", "reasoning_effort": "low"}
    return main.call("POST", "/api/runs/dispatch", body)["run_id"]


class ClaimStub(StubModel):
    """A model that works any claim: it looks the claim up, then decides.

    Several claims can share it at once. `failures` holds, for a claim id, the
    errors to raise one after another before it answers, and `payouts` the
    payout it approves, 420 by default.
    """

    failures: dict[str, list[BaseException]] = Field(default_factory=dict)
    payouts: dict[str, float] = Field(default_factory=dict)
    kinds: dict[str, str] = Field(default_factory=dict)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        claim_id = re.findall(r"Decide claim (\S+)\.", str(messages[1].content))[0]
        if self.failures.get(claim_id):
            raise self.failures[claim_id].pop(0)
        last = messages[-1]
        if isinstance(last, HumanMessage) and last.content == "Record your decision.":
            kind = self.kinds.get(claim_id, "approve")
            reply = decide(claim_id, kind, self.payouts.get(claim_id, 420.0))
        elif any(message.type == "tool" for message in messages):
            reply = AIMessage(content=f"Decide {claim_id}.")
        else:
            reply = call_tool("get_claim", claim_id=claim_id)
        reply.response_metadata["token_usage"] = USAGE
        return ChatResult(generations=[ChatGeneration(message=reply)])
