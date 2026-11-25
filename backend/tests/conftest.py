import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import agent_service, auth, judge
from backend.main import app
from backend.tests import sign_in
from backend.tests.stub_judge import StubJev


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


@pytest.fixture(autouse=True)
def jev(monkeypatch):
    """A stand-in for the judge, so that no test calls it."""
    monkeypatch.delenv("MODEL_NAME", raising=False)
    stub = StubJev()
    monkeypatch.setattr(judge, "client", lambda: stub)
    return stub


@pytest.fixture(autouse=True)
def public_keys(monkeypatch):
    monkeypatch.setattr(auth, "keys", sign_in.StubKeys)


@pytest.fixture
def client():
    """A client signed in as Dana Whitfield, the senior handler."""
    with TestClient(app, headers=sign_in.bearer("dana")) as client:
        yield client


@pytest.fixture
def anonymous():
    """A client that has not signed in."""
    with TestClient(app) as client:
        yield client
