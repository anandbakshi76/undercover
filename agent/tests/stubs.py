"""Stub models that give scripted replies in place of a real model."""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import Field

from agent import main


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies and remembers what it was sent."""

    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        reply = next(self.messages)
        if isinstance(reply, Exception):
            raise reply
        self.messages = iter([reply, *self.messages])
        return super()._generate(messages, stop, run_manager, **kwargs)


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


def approve_and_pay(claim_id, payout=420.0):
    """The replies of a claim agent that approves a claim and pays it."""
    return [
        call_tool("get_claim", claim_id=claim_id),
        call_tool("record_decision", **decision(claim_id, payout=payout)),
        call_tool("pay_claim", claim_id=claim_id, amount=payout),
        AIMessage(content=f"Approved {claim_id}."),
    ]


def claim(claim_id):
    return main.call("GET", f"/api/claims/{claim_id}")


def recorded_steps(claim_id):
    return main.call("GET", f"/api/claims/{claim_id}/steps")


def new_run(limit):
    return main.call("POST", "/api/runs", {"limit": limit})["run_id"]
