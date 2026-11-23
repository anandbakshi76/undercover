"""The measures of an evaluation, as in the course's labs."""

from typing import Any

WRONGFUL_DECLINE = 1500
NEEDLESS_REFERRAL = 50


def is_right(agent: dict[str, Any], handler: dict[str, Any]) -> bool:
    """The decision matches the handler's and the payout is within a dollar."""
    return (
        agent["decision"] == handler["decision"]
        and abs(agent["payout"] - handler["payout"]) <= 1
    )


def mistake_cost(agent: dict[str, Any], handler: dict[str, Any]) -> float:
    """What the agent's answer costs UnderCover, against the handler's decision."""
    if agent["decision"] == handler["decision"]:
        if agent["decision"] == "approve":
            return abs(agent["payout"] - handler["payout"])
        return 0
    if agent["decision"] == "decline":
        return WRONGFUL_DECLINE
    if agent["decision"] == "approve":
        return agent["payout"]
    return NEEDLESS_REFERRAL


def answer(decision: str | None, payout: float | None) -> dict[str, Any]:
    """The agent's answer; a claim it didn't decide counts as a referral."""
    if decision is None:
        return {"decision": "refer", "payout": 0.0}
    return {"decision": decision, "payout": payout or 0.0}


def measures(rows: list[dict[str, Any]], run_seconds: float) -> dict[str, Any]:
    """The measures of a run, from one row for each claim."""
    count = len(rows)
    if not count:
        return {
            "claim_count": 0,
            "decision_accuracy": 0.0,
            "accuracy": 0.0,
            "cost_of_mistakes": 0.0,
            "straight_through": 0.0,
            "cost_per_claim": 0.0,
            "seconds_per_claim": 0.0,
            "run_seconds": run_seconds,
        }
    return {
        "claim_count": count,
        "decision_accuracy": sum(
            r["agent"]["decision"] == r["handler"]["decision"] for r in rows
        )
        / count,
        "accuracy": sum(r["right"] for r in rows) / count,
        "cost_of_mistakes": sum(r["mistake_cost"] for r in rows),
        "straight_through": sum(r["agent"]["decision"] != "refer" for r in rows)
        / count,
        "cost_per_claim": sum(r["cost"] for r in rows) / count,
        "seconds_per_claim": sum(r["seconds"] for r in rows) / count,
        "run_seconds": run_seconds,
    }
