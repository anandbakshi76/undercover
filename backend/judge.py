"""The judge: Jev, through OpenRouter's Decisions endpoint, grades an explanation.

The endpoint is in alpha, so the call is written once, here. It runs in the
backend because the backend holds the handlers' decisions, and the held-out
set's reasons never leave it.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from typing import Any

from openrouter import OpenRouter
from openrouter.components import DecisionsNoulAnswer, DecisionsScoreAnswer
from openrouter.utils import BackoffStrategy, RetryConfig

JEV = "typesafe/jev-1.13"
TIMEOUT_MS = 10_000
NO_RETRIES = RetryConfig("none", BackoffStrategy(1, 1, 1.5, 1), False)

# Jev numbers the grades by their position in the list, so F is 0 and A is 3
RUBRIC = {
    "F": (
        "different grounds from the handler's reason, or it contradicts the "
        "handler's reason"
    ),
    "C": (
        "partly the same as the handler's reason: one of the handler's grounds "
        "or the section is missing, or it adds a ground the handler did not give"
    ),
    "B": (
        "the same grounds and the same section of the policy wording as the "
        "handler's reason"
    ),
    "A": (
        "the same grounds and section as the handler's reason, and clearly "
        "better expressed: clearer for the customer, with the section and the "
        "figures, shorter, and no jargon"
    ),
}
GRADES = list(RUBRIC)

QUESTIONS: dict[str, Any] = {
    "grade": {
        "type": "score",
        "instructions": (
            "An expert claims handler decided a travel insurance claim and gave "
            "`handler.reason`, resting on `handler.reference`. Grade how well "
            "`agent.rationale` explains the same decision."
        ),
        "criteria": [f"{letter}: {text}" for letter, text in RUBRIC.items()],
    },
    "same_grounds": {
        "type": "noul",
        "instructions": (
            "`agent.rationale` gives the same main grounds for the decision as "
            "`handler.reason`"
        ),
    },
    "same_section": {
        "type": "noul",
        "instructions": (
            "`agent.rationale` refers to the same section of the policy wording "
            "as `handler.reference`"
        ),
    },
}

UNDECIDED = {
    "grade": 0.0,
    "letter": "F",
    "same_grounds": 0.0,
    "same_section": 0.0,
    "judge_cost": 0.0,
}


def enabled() -> bool:
    """Say whether the judge is called: not when MODEL_NAME chooses a free model."""
    return not os.getenv("MODEL_NAME")


@cache
def server_url() -> str:
    """The origin of the Decisions endpoint, read once from the environment."""
    return os.getenv("JUDGE_SERVER_URL") or "https://openrouter.ai"


@cache
def client() -> OpenRouter:
    """The OpenRouter client, made once."""
    return OpenRouter(api_key=os.environ["OPENROUTER_API_KEY"])


def grade(
    decision: str, rationale: str, expected: dict[str, Any]
) -> dict[str, float | str]:
    """Ask Jev to grade a rationale against the handler's reason."""
    state = {
        "handler": {
            "decision": expected["decision"],
            "reason": expected["reason"],
            "reference": expected["reference"],
        },
        "agent": {"decision": decision, "rationale": rationale},
    }
    # The client ignores an address set on it, so the address goes on the call.
    response = client().alpha.decisions.create(
        model=JEV,
        state=state,
        questions=QUESTIONS,
        server_url=server_url(),
        timeout_ms=TIMEOUT_MS,
        retries=NO_RETRIES,
    )
    answer = response.answers["grade"]
    grounds = response.answers["same_grounds"]
    section = response.answers["same_section"]
    if not (
        isinstance(answer, DecisionsScoreAnswer)
        and isinstance(grounds, DecisionsNoulAnswer)
        and isinstance(section, DecisionsNoulAnswer)
        and answer.probabilities
    ):
        raise ValueError("Jev answered with an answer of another kind.")
    probabilities = answer.probabilities
    most_likely = max(probabilities, key=lambda level: probabilities[level])
    return {
        "grade": answer.score,
        "letter": GRADES[int(most_likely)],
        "same_grounds": grounds.noul,
        "same_section": section.noul,
        "judge_cost": response.usage.cost or 0.0,
    }


def grade_one(item: dict[str, Any]) -> dict[str, Any] | None:
    """Grade one claim, or return None when the judge couldn't."""
    if item["decision"] is None:
        return dict(UNDECIDED)
    try:
        return grade(item["decision"], item["rationale"], item["expected"])
    except Exception:  # noqa: BLE001
        return None


def grade_all(items: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """Grade the claims of an evaluation in parallel, by claim id."""
    if not items:
        return {}
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        results = list(pool.map(grade_one, items))
    return {
        item["claim_id"]: result for item, result in zip(items, results, strict=True)
    }
