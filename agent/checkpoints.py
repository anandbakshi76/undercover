"""Where the claim graph saves its progress: Supabase, or memory without it."""

import os
from functools import cache
from typing import Any

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA = "undercover"
MAX_CONNECTIONS = 4


SETTINGS: dict[str, Any] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
    "options": f"-c search_path={SCHEMA}",
}


def make_pool(url: str) -> ConnectionPool:
    """Make a closed pool of at most 4 connections with LangGraph's settings."""
    return ConnectionPool(url, max_size=MAX_CONNECTIONS, open=False, kwargs=SETTINGS)


@cache
def checkpointer() -> BaseCheckpointSaver:
    """Return this process's checkpointer, made on first use."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return InMemorySaver()
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    pool = make_pool(url)
    pool.open()
    saver = PostgresSaver(pool)  # pyright: ignore[reportArgumentType]
    saver.setup()
    return saver
