"""Keeping or rolling back a new version, by the rule from the business."""

from typing import Any

HIGHER_IS_BETTER = ["accuracy", "grade"]
LOWER_IS_BETTER = ["cost_per_claim", "seconds_per_claim"]
MEASURES = ["decision_accuracy", *HIGHER_IS_BETTER, *LOWER_IS_BETTER]

RULE = (
    "A new version is kept when it meets the objectives, at least one measure "
    "improves by more than its noise and none gets worse by more than its "
    "tolerance"
)


def average(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """The average of a version's runs on a set. A run with no grade has none."""
    scores: dict[str, Any] = {}
    for name in MEASURES:
        values = [run[name] for run in runs if run[name] is not None]
        scores[name] = sum(values) / len(values) if values else None
    return scores | {"runs": len(runs)}


def compare(
    new: dict[str, Any],
    old: dict[str, Any],
    noise: dict[str, float],
    tolerance: dict[str, float],
) -> tuple[list[str], list[str]]:
    """Which measures got better by more than the noise, and which worse.

    A measure is worse when it falls by more than its tolerance. Accuracy and
    the grade are compared as a difference, cost and time as a fraction of the
    old value.
    """
    better: list[str] = []
    worse: list[str] = []
    for name in HIGHER_IS_BETTER:
        if new[name] is None or old[name] is None:
            continue
        if new[name] > old[name] + noise[name]:
            better.append(name)
        if new[name] < old[name] - tolerance[name]:
            worse.append(name)
    for name in LOWER_IS_BETTER:
        if new[name] < old[name] * (1 - noise[name]):
            better.append(name)
        if new[name] > old[name] * (1 + tolerance[name]):
            worse.append(name)
    return better, worse


def keep_or_roll_back(
    new: dict[str, Any],
    old: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[bool, str]:
    """Decide whether to keep a version from its held-out scores."""
    if new["grade"] is None:
        return False, "it has no grade"
    objectives = settings["objectives"]
    missed = [name for name, floor in objectives.items() if new[name] < floor]
    if missed:
        return False, f"it missed the objectives on {', '.join(missed)}"
    better, worse = compare(new, old, settings["noise"], settings["tolerance"])
    if worse:
        return False, f"{', '.join(worse)} got worse"
    if not better:
        return False, "no measure improved by more than its noise"
    return True, f"{', '.join(better)} improved"
