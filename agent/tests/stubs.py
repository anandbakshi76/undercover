"""Stub models that give scripted replies in place of a real model."""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import Field

from agent import main

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


def new_run(limit):
    return main.call("POST", "/api/runs", {"limit": limit})["run_id"]
