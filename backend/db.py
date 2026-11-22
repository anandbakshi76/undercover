"""The UnderCover database, seeded from the business data in data/.

SQLite by default, or Supabase when DATABASE_URL is set. Every table lives in
the undercover schema on Postgres.
"""

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    Connection,
    Engine,
    Float,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    insert,
    select,
    text,
)
from sqlalchemy.pool import NullPool

DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB = Path(__file__).resolve().parent / "undercover.db"
SCHEMA = "undercover"
MAX_CONNECTIONS = 4

metadata = MetaData()

records = Table(
    "records",
    metadata,
    Column("kind", Text, primary_key=True),
    Column("id", Text, primary_key=True),
    Column("data", Text, nullable=False),
)
claims = Table(
    "claims",
    metadata,
    Column("claim_id", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("data", Text, nullable=False),
    Column("decision", Text),
    Column("payout", Float),
    Column("reason", Text),
    Column("policy_section", Text),
    Column("decided_at", Text),
    Column("signed_off_by", Text),
    Column("signed_off_at", Text),
    Column("paid_amount", Float),
    Column("paid_at", Text),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("run_id", Text),
)
runs = Table(
    "runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Text, nullable=False, unique=True),
    Column("kind", Text, nullable=False),
    Column("claim_limit", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("todos", Text, nullable=False, server_default="[]"),
    Column("summary", Text),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
)
run_claims = Table(
    "run_claims",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("claim_id", Text, nullable=False),
    UniqueConstraint("run_id", "claim_id"),
)
steps = Table(
    "steps",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("claim_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)

RECORDS = {
    "policy": ("policies.json", "policy_id"),
    "customer": ("customers.json", "customer_id"),
    "handler": ("handlers.json", "handler_id"),
}

engines: dict[str, Engine] = {}


def db_path() -> Path:
    """Return the SQLite file, from UNDERCOVER_DB if set."""
    return Path(os.getenv("UNDERCOVER_DB") or DEFAULT_DB)


def on_postgres() -> bool:
    """Say whether the backend is using Supabase."""
    return bool(os.getenv("DATABASE_URL"))


def database_url() -> str:
    """Return the SQLAlchemy URL for Supabase if DATABASE_URL is set, else SQLite."""
    url = os.getenv("DATABASE_URL")
    if url:
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    return f"sqlite:///{db_path()}"


def engine() -> Engine:
    """Return the engine for the current database, made once."""
    url = database_url()
    if url not in engines:
        if on_postgres():
            engines[url] = create_engine(
                url,
                pool_size=MAX_CONNECTIONS,
                max_overflow=0,
                pool_pre_ping=True,
                connect_args={"options": f"-c search_path={SCHEMA}"},
            )
        else:
            engines[url] = create_engine(url, poolclass=NullPool)
    return engines[url]


@contextmanager
def connect() -> Iterator[Connection]:
    """Open the database for one unit of work, committed at the end."""
    with engine().begin() as connection:
        yield connection


def load(name: str) -> Any:
    """Read one JSON file from data/."""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def seed(connection: Connection) -> None:
    """Fill an empty database from data/, leaving out data/evals/."""
    rows = [
        {"kind": kind, "id": record[key], "data": json.dumps(record)}
        for kind, (name, key) in RECORDS.items()
        for record in load(name)
    ]
    settings = {"plans": load("plans.json"), "company": load("company.json")}
    for document in ["policy_wording", "claims_manual"]:
        settings[document] = (DATA / f"{document}.md").read_text(encoding="utf-8")
    rows += [
        {"kind": "settings", "id": key, "data": json.dumps(value)}
        for key, value in settings.items()
    ]
    connection.execute(insert(records), rows)
    connection.execute(
        insert(claims),
        [
            {
                "claim_id": claim["claim_id"],
                "status": "waiting",
                "data": json.dumps(claim),
            }
            for claim in load("claims.json")
        ],
    )


def init() -> None:
    """Create the tables and seed them on first start."""
    if on_postgres():
        with engine().begin() as connection:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    metadata.create_all(engine())
    with connect() as connection:
        if connection.execute(select(claims.c.claim_id).limit(1)).first() is None:
            seed(connection)


def reset() -> None:
    """Delete the data, checkpoints included, and seed a fresh database."""
    if on_postgres():
        with engine().begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    else:
        engine().dispose()
        db_path().unlink(missing_ok=True)
    init()


def describe() -> str:
    """Name the database in use, without its password."""
    return f"the {SCHEMA} schema on Supabase" if on_postgres() else str(db_path())


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset()
        print(f"Reset {describe()}")
    else:
        init()
        print(f"Ready {describe()}")
