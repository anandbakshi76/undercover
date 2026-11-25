"""The improver's way to the backend. The claim agent never imports this."""

from typing import Any

from agent import main


def round_state(round_id: str) -> dict[str, Any]:
    """Get a round with its stage, its proposals and what it has spent."""
    return main.call("GET", f"/api/rounds/{round_id}")


def view(round_id: str) -> dict[str, Any]:
    """Get what the improver may read: its version, scores, mistakes and limits."""
    return main.call("GET", f"/api/rounds/{round_id}/view")


def estimate(round_id: str, version: int) -> dict[str, Any]:
    """Get what a version's three evaluations are likely to cost, and what is left."""
    return main.call("GET", f"/api/rounds/{round_id}/estimates/{version}")


def set_stage(round_id: str, stage: str, progress: int) -> dict[str, Any]:
    """Say where the round has got to."""
    return main.call(
        "PUT", f"/api/rounds/{round_id}/stage", {"stage": stage, "progress": progress}
    )


def record_change(round_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    """Record a change the improver proposes, which waits for a handler."""
    return main.call("POST", f"/api/rounds/{round_id}/change-proposals", proposal)


def record_recipe(round_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    """Record a recipe the improver proposes, which waits for a handler."""
    return main.call("POST", f"/api/rounds/{round_id}/recipe-proposals", proposal)


def start_evaluation(round_id: str, options: dict[str, Any]) -> dict[str, Any]:
    """Start one of the round's evaluations of a version on a whole set."""
    body = options | {"tiny": False, "round_id": round_id}
    return main.call("POST", "/api/runs/evaluation", body)


def conclude(round_id: str) -> dict[str, Any]:
    """Ask the backend to keep or roll back the version the round tried."""
    return main.call("POST", f"/api/rounds/{round_id}/verdict")


def finish(round_id: str) -> dict[str, Any]:
    """Finish a round."""
    return main.call("POST", f"/api/rounds/{round_id}/finish")


def record_call(round_id: str, usage: dict[str, Any]) -> dict[str, Any]:
    """Record the tokens and cost of one of the improver's model calls."""
    return main.call("POST", f"/api/rounds/{round_id}/model-calls", usage)
