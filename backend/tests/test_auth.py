import json

import pytest
from fastapi import HTTPException

from backend import auth, db
from backend.db import runs
from backend.tests import sign_in

START = {"concurrency": 3}
EVALUATION = {"concurrency": 3, "evaluation_set": "held_out", "tiny": True}

BAD_SIGN_INS = {
    "no token": {},
    "an expired token": sign_in.bearer("dana", expires_in=-60),
    "the wrong audience": sign_in.bearer("dana", audience="anon"),
    "another key": sign_in.bearer("dana", key=sign_in.OTHER_KEY),
    "a stranger's email": sign_in.bearer("stranger@example.com"),
    "garbage": {"Authorization": "Bearer not-a-token"},
}


def actions(client, headers):
    """Each action that needs a signed-in handler, as a response."""
    return {
        "start_dispatch": client.post("/api/dispatches", json=START, headers=headers),
        "start_evaluation": client.post(
            "/api/evaluations", json=EVALUATION, headers=headers
        ),
        "sign_off": client.post("/api/claims/CLM-1002/sign-off", headers=headers),
        "get_me": client.get("/api/me", headers=headers),
    }


@pytest.mark.parametrize("name", BAD_SIGN_INS)
def test_every_handler_action_is_refused_without_a_valid_sign_in(
    name, anonymous, agent
):
    for action, response in actions(anonymous, BAD_SIGN_INS[name]).items():
        assert response.status_code == 401, (name, action)
        assert "Sign in" in response.json()["detail"]
    assert agent.calls == []
    with db.connect() as connection:
        assert connection.execute(runs.select()).first() is None
    claim = anonymous.get("/api/claims/CLM-1002").json()
    assert claim["sign_off"] is None


def test_a_valid_sign_in_gets_through(anonymous, agent):
    headers = sign_in.bearer("dana")
    assert (
        anonymous.post("/api/dispatches", json=START, headers=headers).status_code
        == 200
    )
    evaluation = anonymous.post("/api/evaluations", json=EVALUATION, headers=headers)
    assert evaluation.status_code == 200
    assert [call[1] for call in agent.calls] == ["/api/dispatches", "/api/evaluations"]


def test_any_signed_in_handler_may_start_runs(anonymous, agent):
    headers = sign_in.bearer("priya")
    assert (
        anonymous.post("/api/dispatches", json=START, headers=headers).status_code
        == 200
    )


def test_get_me_says_who_is_signed_in_and_what_they_may_do(anonymous):
    dana = anonymous.get("/api/me", headers=sign_in.bearer("dana")).json()
    assert dana["name"] == "Dana Whitfield"
    assert dana["can_approve_over_limit"] is True
    marcus = anonymous.get("/api/me", headers=sign_in.bearer("marcus")).json()
    assert marcus["handler_id"] == "HND-02"
    assert marcus["can_approve_over_limit"] is False


def test_the_email_is_matched_whatever_its_case(anonymous):
    response = anonymous.get(
        "/api/me", headers=sign_in.bearer("Dana.Whitfield@UnderCover.example")
    )
    assert response.json()["handler_id"] == "HND-01"


def test_only_dana_can_sign_off_over_the_limit_in_the_data():
    handlers = json.loads((db.DATA / "handlers.json").read_text(encoding="utf-8"))
    can = {h["name"]: h["can_approve_over_limit"] for h in handlers}
    assert can == {
        "Dana Whitfield": True,
        "Marcus Oyelaran": False,
        "Priya Raman": False,
    }


def test_the_seed_stores_each_handlers_email(client):
    handlers = client.get("/api/handlers").json()
    assert [h["email"] for h in handlers] == [
        "dana.whitfield@undercover.example",
        "marcus.oyelaran@undercover.example",
        "priya.raman@undercover.example",
    ]


def test_the_senior_check_names_the_handler_and_the_reason():
    marcus = {"name": "Marcus Oyelaran", "can_approve_over_limit": False}
    with pytest.raises(HTTPException) as refused:
        auth.senior_handler(marcus)
    assert refused.value.status_code == 403
    assert refused.value.detail == (
        "Marcus Oyelaran can't do this. Only a handler who can sign off payouts "
        "over the limit may."
    )
    dana = {"name": "Dana Whitfield", "can_approve_over_limit": True}
    assert auth.senior_handler(dana) is dana


def test_the_keys_come_from_the_projects_address(monkeypatch):
    monkeypatch.setenv("VITE_SUPABASE_URL", "https://abc.supabase.co/")
    client = auth.make_keys()
    assert client.uri == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"


def test_without_an_address_sign_in_says_it_isnt_set_up(monkeypatch):
    monkeypatch.delenv("VITE_SUPABASE_URL", raising=False)
    with pytest.raises(HTTPException) as refused:
        auth.make_keys()
    assert refused.value.status_code == 503
