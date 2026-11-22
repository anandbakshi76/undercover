"""Carry on the work an earlier run left: claims and queue runs."""

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from agent import claim, main, queue_agent


def resume(
    skip_run_id: str | None = None,
    llm: BaseChatModel | None = None,
    saver: BaseCheckpointSaver | None = None,
) -> list[str]:
    """Carry on every claim in progress, then every queue run left running."""
    lines = [
        claim.carry_on(item["claim_id"], llm, saver)
        for item in main.call("GET", "/api/queue/unfinished")
    ]
    for run in main.call("GET", "/api/queue/running-runs"):
        if run["run_id"] not in (skip_run_id, *queue_agent.ACTIVE):
            lines.append(queue_agent.run_queue(run["run_id"], llm, saver))
    return lines
