"""The claim agent's configuration and the improver's recipe, in versions."""

import difflib
import json
import os
from typing import Any

from sqlalchemy import Connection, select

from backend.db import DATA, configurations, improvement_state, recipes


def diff_lines(old: str, new: str) -> list[dict[str, str]]:
    """The lines that differ between two texts, with a little around each change."""
    lines: list[dict[str, str]] = []
    for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), lineterm="", n=2
    ):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            lines.append({"kind": "gap", "text": "..."})
        elif line.startswith("+"):
            lines.append({"kind": "added", "text": line[1:]})
        elif line.startswith("-"):
            lines.append({"kind": "removed", "text": line[1:]})
        else:
            lines.append({"kind": "same", "text": line[1:]})
    return lines


def models_allowed() -> list[dict[str, Any]]:
    """The models the claim agent may use, from data/models.json or MODEL_NAME."""
    name = os.getenv("MODEL_NAME")
    if name:
        return [
            {
                "model": name,
                "reasoning_efforts": ["default"],
                "input_price": 0.0,
                "output_price": 0.0,
            }
        ]
    path = DATA / "models.json"
    return json.loads(path.read_text(encoding="utf-8"))["models"]


def current(connection: Connection, key: str = "current_version") -> int:
    """The version of the configuration (or the recipe) now in use."""
    return connection.execute(
        select(improvement_state.c.value).where(improvement_state.c.key == key)
    ).scalar_one()


def configuration(connection: Connection, version: int) -> dict[str, Any] | None:
    """One version of the configuration, or None."""
    row = (
        connection.execute(
            select(configurations).where(configurations.c.version == version)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def recipe(connection: Connection, version: int) -> dict[str, Any] | None:
    """One version of the recipe, or None."""
    row = (
        connection.execute(select(recipes).where(recipes.c.version == version))
        .mappings()
        .first()
    )
    return dict(row) if row else None
