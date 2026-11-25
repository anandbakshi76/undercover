"""A stand-in for Jev, so that no test calls the judge."""

import threading
import time
from collections.abc import Callable
from typing import Any

from openrouter.components import (
    DecisionsNoulAnswer,
    DecisionsResponse,
    DecisionsResponseUsage,
    DecisionsScoreAnswer,
)

LEVELS = {"0": 0.05, "1": 0.15, "2": 0.6, "3": 0.2}


def answer(
    probabilities: dict[str, float] | None = None,
    grounds: float = 0.9,
    section: float = 0.2,
    cost: float = 0.0004,
) -> DecisionsResponse:
    """Jev's answer to the three questions."""
    levels = probabilities or LEVELS
    score = sum(int(level) * chance for level, chance in levels.items())
    return DecisionsResponse(
        answers={
            "grade": DecisionsScoreAnswer(
                score=score, type="score", probabilities=levels
            ),
            "same_grounds": DecisionsNoulAnswer(noul=grounds, type="noul"),
            "same_section": DecisionsNoulAnswer(noul=section, type="noul"),
        },
        model="typesafe/jev-1.13",
        usage=DecisionsResponseUsage(input_tokens=10, output_tokens=2, cost=cost),
    )


class StubJev:
    """The OpenRouter client's Decisions endpoint, answering as a test says."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reply: Callable[[dict[str, Any]], DecisionsResponse] = lambda state: (
            answer()
        )
        self.delay = 0.0
        self.running = 0
        self.peak = 0
        self.lock = threading.Lock()

    @property
    def alpha(self) -> "StubJev":
        return self

    @property
    def decisions(self) -> "StubJev":
        return self

    def create(self, **call: Any) -> DecisionsResponse:
        with self.lock:
            self.calls.append(call)
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            time.sleep(self.delay)
            return self.reply(call["state"])
        finally:
            with self.lock:
                self.running -= 1
