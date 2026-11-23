import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import agent_service
from backend.main import app


class StandIn:
    """A stand-in for the agent's service that records what it is asked."""

    def __init__(self):
        self.calls = []
        self.down = False
        self.reply = httpx.Response(200, json={"status": "started", "run_id": "RUN-1"})

    def handle(self, request):
        if self.down:
            raise httpx.ConnectError("refused")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        return self.reply

    def client(self):
        return httpx.Client(
            base_url="http://agent", transport=httpx.MockTransport(self.handle)
        )


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture(autouse=True)
def agent(monkeypatch):
    stand_in = StandIn()
    monkeypatch.setattr(agent_service, "client", stand_in.client)
    return stand_in


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client
