"""The platform's settings, kept in the database, and what each may be set to."""

import json
from typing import Annotated, Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import Connection, select

from backend.db import platform_settings


class Objectives(BaseModel):
    accuracy: float = Field(ge=0, le=1)
    grade: float = Field(ge=0, le=3)


class Measures(BaseModel):
    accuracy: float = Field(ge=0)
    grade: float = Field(ge=0)
    cost_per_claim: float = Field(ge=0)
    seconds_per_claim: float = Field(ge=0)


VALIDATORS: dict[str, TypeAdapter[Any]] = {
    "approval_limit": TypeAdapter(Annotated[float, Field(gt=0)]),
    "objectives": TypeAdapter(Objectives),
    "noise": TypeAdapter(Measures),
    "tolerance": TypeAdapter(Measures),
    "round_limit": TypeAdapter(Annotated[float, Field(gt=0)]),
    "improver_concurrency": TypeAdapter(Annotated[int, Field(ge=1, le=25)]),
}


def check(key: str, value: Any) -> Any:
    """Check a new value for a setting, and return it as it will be kept."""
    if key not in VALIDATORS:
        raise HTTPException(404, f"There is no setting {key}.")
    try:
        checked = VALIDATORS[key].validate_python(value)
    except ValidationError as error:
        problem = error.errors()[0]
        where = ".".join(str(part) for part in problem["loc"])
        raise HTTPException(
            422, f"{key}{'.' + where if where else ''}: {problem['msg']}"
        ) from None
    return checked.model_dump() if isinstance(checked, BaseModel) else checked


def read(connection: Connection, key: str) -> Any:
    """Read one setting."""
    value = connection.execute(
        select(platform_settings.c.value).where(platform_settings.c.key == key)
    ).scalar_one()
    return json.loads(value)


def read_all(connection: Connection) -> dict[str, Any]:
    """Read every setting."""
    rows = connection.execute(
        select(platform_settings.c.key, platform_settings.c.value)
    ).all()
    return {key: json.loads(value) for key, value in rows}
