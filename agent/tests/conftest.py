from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from agent import main
from backend import db
from backend.main import app


@pytest.fixture(autouse=True)
def fresh_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "test.db"))
    db.init()
    client = TestClient(app)
    monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
    monkeypatch.setattr(main, "RETRY_DELAY", 0)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ["DATABASE_URL", "LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"]:
        monkeypatch.delenv(name, raising=False)
