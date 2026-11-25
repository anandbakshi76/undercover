import json

import pytest
from sqlalchemy import update

from backend import db, settings
from backend.db import improvement_state
from backend.tests import sign_in

START = {
    "approval_limit": 1000.0,
    "objectives": {"accuracy": 0.6, "grade": 1.0},
    "noise": {
        "accuracy": 0.1,
        "grade": 0.15,
        "cost_per_claim": 0.15,
        "seconds_per_claim": 0.15,
    },
    "tolerance": {
        "accuracy": 0.1,
        "grade": 0.15,
        "cost_per_claim": 1.0,
        "seconds_per_claim": 0.25,
    },
    "round_limit": 0.5,
    "improver_concurrency": 25,
}


def values(client):
    return {s["key"]: s["value"] for s in client.get("/api/settings").json()}


def change(client, key, value, who="dana"):
    return client.put(
        f"/api/settings/{key}", json={"value": value}, headers=sign_in.bearer(who)
    )


def decide(client, claim_id, payout):
    client.post("/api/claims/" + claim_id + "/start-run")
    body = {
        "decision": "approve",
        "payout": payout,
        "reason": "Covered.",
        "policy_section": "Section 5",
    }
    return client.post(f"/api/claims/{claim_id}/decision", json=body).json()


def test_the_settings_start_with_the_labs_values_and_nobody_has_changed_them(client):
    assert values(client) == START
    assert all(s["changed_by"] is None for s in client.get("/api/settings").json())
    assert client.get("/api/settings/changes").json() == []


def test_a_senior_handler_changes_a_setting_and_it_records_who_and_when(client):
    changed = change(client, "round_limit", 0.75).json()
    assert changed["value"] == 0.75
    assert changed["changed_by"] == "Dana Whitfield"
    assert changed["changed_at"]
    assert values(client)["round_limit"] == 0.75
    log = client.get("/api/settings/changes").json()
    assert [(c["key"], c["old"], c["new"], c["changed_by"]) for c in log] == [
        ("round_limit", 0.5, 0.75, "Dana Whitfield")
    ]


def test_changes_are_listed_newest_first(client):
    change(client, "round_limit", 0.75)
    change(client, "improver_concurrency", 10)
    keys = [c["key"] for c in client.get("/api/settings/changes").json()]
    assert keys == ["improver_concurrency", "round_limit"]


def test_a_nested_setting_is_changed_whole(client):
    new = {"accuracy": 0.7, "grade": 1.5}
    assert change(client, "objectives", new).json()["value"] == new
    assert values(client)["objectives"] == new


BAD_NOISE = {
    "accuracy": -0.1,
    "grade": 0,
    "cost_per_claim": 0,
    "seconds_per_claim": 0,
}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("approval_limit", -5),
        ("approval_limit", 0),
        ("approval_limit", "lots"),
        ("round_limit", 0),
        ("improver_concurrency", 0),
        ("improver_concurrency", 26),
        ("objectives", {"accuracy": 1.5, "grade": 1.0}),
        ("objectives", {"accuracy": 0.5}),
        ("noise", BAD_NOISE),
    ],
)
def test_a_value_that_makes_no_sense_is_refused_and_nothing_changes(client, key, value):
    refused = change(client, key, value)
    assert refused.status_code == 422
    assert key in refused.json()["detail"]
    assert values(client) == START
    assert client.get("/api/settings/changes").json() == []


def test_an_unknown_setting_is_not_found(client):
    assert change(client, "colour", "teal").status_code == 404


def test_only_a_senior_handler_with_a_sign_in_may_change_a_setting(client, anonymous):
    marcus = change(client, "round_limit", 5, "marcus")
    assert marcus.status_code == 403
    assert "Marcus Oyelaran can't do this" in marcus.json()["detail"]
    nobody = anonymous.put("/api/settings/round_limit", json={"value": 5})
    assert nobody.status_code == 401
    assert values(client) == START


def test_a_new_approval_limit_applies_to_the_next_decision_only(client):
    first = decide(client, "CLM-1001", 600)
    assert first["status"] == "in_progress"
    change(client, "approval_limit", 500)
    second = decide(client, "CLM-1002", 600)
    assert second["status"] == "awaiting_sign_off"
    still = client.get("/api/claims/CLM-1001").json()
    assert still["status"] == "in_progress"
    paid = client.post("/api/claims/CLM-1001/payment", json={"amount": 600})
    assert paid.status_code == 200
    refused = client.post("/api/claims/CLM-1002/payment", json={"amount": 600})
    assert refused.status_code == 409
    assert "$500.00" in refused.json()["detail"]


def test_the_models_allowed_come_from_the_file(client):
    models = client.get("/api/models").json()
    assert [m["model"] for m in models] == [
        "openai/gpt-6-luna",
        "google/gemini-3.5-flash-lite",
        "deepseek/deepseek-v4.1-flash",
    ]
    luna = models[0]
    assert luna["reasoning_efforts"] == ["none", "low", "medium"]
    assert (luna["input_price"], luna["output_price"]) == (0.1, 0.5)
    stored = json.loads((db.DATA / "models.json").read_text(encoding="utf-8"))
    assert models == stored["models"]


def test_with_model_name_set_that_model_is_the_only_one_allowed_at_no_price(
    client, monkeypatch
):
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert client.get("/api/models").json() == [
        {
            "model": "openrouter/free",
            "reasoning_efforts": ["default"],
            "input_price": 0.0,
            "output_price": 0.0,
        }
    ]


def test_a_reseed_takes_the_settings_and_the_version_back_to_the_start(client):
    change(client, "round_limit", 3)
    change(client, "approval_limit", 250)
    with db.connect() as connection:
        connection.execute(update(improvement_state).values(value=7))
    db.reset()
    with db.connect() as connection:
        assert settings.read_all(connection) == START
    assert client.get("/api/settings/changes").json() == []
    assert client.get("/api/configurations/current").json()["version"] == 1
