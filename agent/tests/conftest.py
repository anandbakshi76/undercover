from contextlib import nullcontext

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import checkpoints, main
from backend import agent_service, auth, db, judge
from backend.main import app
from backend.tests import sign_in
from backend.tests.stub_judge import StubJev


def refuse(request):
    raise httpx.ConnectError("refused")


@pytest.fixture(autouse=True)
def fresh_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "test.db"))
    db.init()
    monkeypatch.setattr(auth, "keys", sign_in.StubKeys)
    client = TestClient(app, headers=sign_in.bearer("dana"))
    monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
    monkeypatch.setattr(main, "RETRY_DELAY", 0)
    down = httpx.Client(base_url="http://agent", transport=httpx.MockTransport(refuse))
    monkeypatch.setattr(agent_service, "client", lambda: down)
    stub = StubJev()
    monkeypatch.setattr(judge, "client", lambda: stub)
    checkpoints.memory.cache_clear()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ["DATABASE_URL", "LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"]:
        monkeypatch.delenv(name, raising=False)
