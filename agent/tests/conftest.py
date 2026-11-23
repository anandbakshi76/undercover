from contextlib import nullcontext

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import checkpoints, main
from backend import agent_service, db
from backend.main import app


def refuse(request):
    raise httpx.ConnectError("refused")


@pytest.fixture(autouse=True)
def fresh_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "test.db"))
    db.init()
    client = TestClient(app)
    monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
    monkeypatch.setattr(main, "RETRY_DELAY", 0)
    down = httpx.Client(base_url="http://agent", transport=httpx.MockTransport(refuse))
    monkeypatch.setattr(agent_service, "client", lambda: down)
    checkpoints.memory.cache_clear()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ["DATABASE_URL", "LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"]:
        monkeypatch.delenv(name, raising=False)
