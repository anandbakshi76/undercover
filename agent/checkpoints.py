"""Where the claim graph saves its progress: Supabase, or memory without it."""

import os
import threading
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
WAIT_FOR_CONNECTION = 300


SETTINGS: dict[str, Any] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
    "options": f"-c search_path={SCHEMA}",
}


def make_pool(url: str) -> ConnectionPool:
    """Make a closed pool of at most 4 connections with LangGraph's settings."""
    return ConnectionPool(
        url,
        min_size=1,
        max_size=MAX_CONNECTIONS,
        open=False,
        timeout=WAIT_FOR_CONNECTION,
        kwargs=SETTINGS,
    )


@cache
def memory() -> InMemorySaver:
    """Return the one in-memory saver, used without DATABASE_URL."""
    return InMemorySaver()


POOL_LOCK = threading.Lock()


def shared_pool() -> ConnectionPool:
    """Return this process's pool, made once however many claims ask at first."""
    with POOL_LOCK:
        return make_shared_pool()


@cache
def make_shared_pool() -> ConnectionPool:
    """Make the pool, with the tables set up."""
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    pool = make_pool(url)
    pool.open()
    PostgresSaver(pool).setup()  # pyright: ignore[reportArgumentType]
    return pool


def saver() -> BaseCheckpointSaver:
    """Return a saver for one claim run, on the process's shared pool.

    A PostgresSaver holds a lock around every read and write, so claim runs
    that shared one would take turns.
    """
    if not os.getenv("DATABASE_URL"):
        return memory()
    return PostgresSaver(shared_pool())  # pyright: ignore[reportArgumentType]
