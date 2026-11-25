import json

import pytest
from sqlalchemy import insert, select, update

from backend import db, improvement, main
from backend.db import improver_calls, model_calls, rounds
from backend.tests import sign_in

SETTINGS = {
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
}


def scores(accuracy=0.7, grade: float | None = 1.5, cost=0.01, seconds=10.0):
    return {
        "decision_accuracy": 0.8,
        "accuracy": accuracy,
        "grade": grade,
        "cost_per_claim": cost,
        "seconds_per_claim": seconds,
    }


def create(client):
    response = client.post("/api/rounds/create", json={"started_by": "HND-01"})
    assert response.status_code == 200, response.text
    return response.json()


def propose(client, round_id, **changes):
    body = {
        "instructions": "Be brief.",
        "model": "openai/gpt-6-luna",
        "reasoning_effort": "medium",
        "reason": "Shorter answers are cheaper.",
    } | changes
    return client.post(f"/api/rounds/{round_id}/change-proposals", json=body)


def decide(client, proposal_id, decision="approve", reason=""):
    body = {"decision": decision, "handler_id": "HND-01", "reason": reason}
    return client.post(f"/api/proposals/{proposal_id}/decision", json=body)


def test_a_new_better_version_is_kept_when_it_beats_the_noise():
    old, new = scores(), scores(accuracy=0.85)
    kept, why = improvement.keep_or_roll_back(new, old, SETTINGS)
    assert kept
    assert "accuracy" in why


@pytest.mark.parametrize(
    ("new", "word"),
    [
        (scores(accuracy=0.72), "noise"),
        (scores(accuracy=0.9, grade=1.2), "worse"),
        (scores(accuracy=0.9, seconds=14.0), "worse"),
        (scores(accuracy=0.5), "objectives"),
        (scores(accuracy=0.9, grade=None), "no grade"),
    ],
)
def test_a_new_version_is_rolled_back_otherwise(new, word):
    kept, why = improvement.keep_or_roll_back(new, scores(), SETTINGS)
    assert not kept
    assert word in why


def test_only_a_senior_handler_starts_a_round(client):
    marcus = client.post("/api/rounds", headers=sign_in.bearer("marcus"), json=None)
    assert marcus.status_code == 403
    started = client.post("/api/rounds")
    assert started.status_code == 200


def test_a_round_starts_and_holds_the_one_run_rule(client):
    started = create(client)
    assert started["status"] == "working"
    assert started["stage"] == "improver_working"
    refused = client.post(
        "/api/runs/dispatch",
        json={"concurrency": 3, "version": 1, "model": "m", "reasoning_effort": "low"},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        f"The improver ({started['stretch_id']}) is running."
    )
    second = client.post("/api/rounds/create", json={"started_by": "HND-01"})
    assert second.status_code == 409


def test_a_proposal_waits_for_a_handler_and_frees_the_run(client):
    started = create(client)
    proposed = propose(client, started["round_id"]).json()
    assert proposed["status"] == "waiting"
    assert proposed["stage"] == "change_waiting"
    assert proposed["pending"]["diff"]
    assert proposed["pending"]["model"]["after"] == "openai/gpt-6-luna"
    assert proposed["pending"]["reasoning_effort"] == {
        "before": "low",
        "after": "medium",
    }
    response = client.post("/api/runs/stop-running")
    assert response.status_code == 200


def test_a_model_that_is_not_allowed_is_refused(client):
    started = create(client)
    refused = propose(client, started["round_id"], model="made/up")
    assert refused.status_code == 422


def test_an_approval_makes_the_next_version(client):
    started = create(client)
    proposed = propose(client, started["round_id"]).json()
    approved = decide(client, proposed["pending"]["id"]).json()
    assert approved["stage"] == "evaluating"
    assert approved["version_tried"] == 2
    assert approved["proposals"][0]["status"] == "approved"
    assert approved["proposals"][0]["decided_by"] == "Dana Whitfield"
    version = client.get("/api/configurations/2").json()
    assert version["instructions"] == "Be brief."
    assert client.get("/api/configurations/current").json()["version"] == 1
    assert decide(client, proposed["pending"]["id"]).status_code == 409


def test_a_rejection_makes_no_version_and_keeps_the_reason(client):
    started = create(client)
    proposed = propose(client, started["round_id"]).json()
    rejected = decide(
        client, proposed["pending"]["id"], "reject", "Too short for a denial."
    ).json()
    assert rejected["stage"] == "improver_working"
    assert rejected["version_tried"] is None
    assert rejected["proposals"][0]["status"] == "rejected"
    assert rejected["proposals"][0]["rejection_reason"] == "Too short for a denial."
    assert client.get("/api/configurations/2").status_code == 404


def test_a_waiting_round_begins_a_new_stretch(client):
    started = create(client)
    propose(client, started["round_id"])
    client.post("/api/runs/stop-running")
    stretch = client.post(f"/api/rounds/{started['round_id']}/stretches")
    assert stretch.status_code == 200
    assert client.get(f"/api/rounds/{started['round_id']}").json()["status"] == (
        "working"
    )
    again = client.post(f"/api/rounds/{started['round_id']}/stretches")
    assert again.status_code == 409


def test_a_recipe_proposal_makes_the_next_recipe(client):
    started = create(client)
    recipe = client.post(
        f"/api/rounds/{started['round_id']}/recipe-proposals",
        json={"recipe": "A better recipe.", "reason": "It asks for less."},
    ).json()
    assert recipe["stage"] == "recipe_waiting"
    approved = decide(client, recipe["pending"]["id"]).json()
    assert approved["stage"] == "improver_working"
    listed = client.get("/api/recipes").json()
    assert [item["version"] for item in listed][-1] == 2


def finish_evaluation(client, round_id, version, evaluation_set, tiny=False):
    body = {
        "concurrency": 3,
        "evaluation_set": evaluation_set,
        "tiny": tiny,
        "version": version,
        "round_id": round_id,
        "model": "m",
        "reasoning_effort": "low",
    }
    run = client.post("/api/runs/evaluation", json=body)
    assert run.status_code == 200, run.text
    run_id = run.json()["run_id"]
    client.post(f"/api/runs/{run_id}/finish", json={"status": "finished"})
    return run_id


def tried(client, monkeypatch, old, new):
    """A round that tried version 2, with scripted scores for the evaluations."""
    started = create(client)
    round_id = started["round_id"]
    proposed = propose(client, round_id).json()
    client.post("/api/runs/stop-running")
    decide(client, proposed["pending"]["id"])
    with db.connect() as connection:
        connection.execute(
            update(rounds)
            .where(rounds.c.round_id == round_id)
            .values(before_scores=json.dumps(old))
        )
    client.post(f"/api/rounds/{round_id}/stretches")
    client.post("/api/runs/stop-running")
    with db.connect() as connection:
        connection.execute(
            update(rounds).where(rounds.c.round_id == round_id).values(status="working")
        )
    runs = [
        finish_evaluation(client, round_id, 2, "evaluation"),
        finish_evaluation(client, round_id, 2, "held_out"),
        finish_evaluation(client, round_id, 2, "held_out"),
    ]
    scripted = {runs[0]: new, runs[1]: new, runs[2]: new}
    monkeypatch.setattr(main, "run_scores", lambda _c, run_id: scripted[run_id])
    return round_id


def test_a_better_version_is_kept_and_made_current(client, monkeypatch):
    round_id = tried(client, monkeypatch, scores(), scores(accuracy=0.9))
    done = client.post(f"/api/rounds/{round_id}/verdict").json()
    assert done["verdict"] == "kept"
    assert done["after"]["accuracy"] == pytest.approx(0.9)
    assert client.get("/api/configurations/current").json()["version"] == 2


def test_a_worse_version_is_rolled_back(client, monkeypatch):
    round_id = tried(client, monkeypatch, scores(), scores(accuracy=0.5))
    done = client.post(f"/api/rounds/{round_id}/verdict").json()
    assert done["verdict"] == "rolled_back"
    assert "objectives" in done["why"]
    assert client.get("/api/configurations/current").json()["version"] == 1


def test_a_version_never_evaluated_is_rolled_back_when_the_round_finishes(client):
    started = create(client)
    proposed = propose(client, started["round_id"]).json()
    client.post("/api/runs/stop-running")
    decide(client, proposed["pending"]["id"])
    client.post(f"/api/rounds/{started['round_id']}/stretches")
    finished = client.post(f"/api/rounds/{started['round_id']}/finish").json()
    assert finished["status"] == "finished"
    assert finished["verdict"] == "rolled_back"
    assert client.get("/api/configurations/current").json()["version"] == 1


def test_a_round_that_spent_its_limit_refuses_new_claims(client):
    started = create(client)
    round_id = started["round_id"]
    run = client.post(
        "/api/runs/evaluation",
        json={
            "concurrency": 3,
            "evaluation_set": "held_out",
            "tiny": True,
            "version": 1,
            "round_id": round_id,
            "model": "m",
            "reasoning_effort": "low",
        },
    )
    assert run.status_code == 200
    run_id = run.json()["run_id"]
    claim_id = run.json()["claims_worked"][0]
    with db.connect() as connection:
        connection.execute(
            insert(improver_calls).values(
                round_id=round_id,
                created_at="2026-11-25T10:00:00+00:00",
                prompt_tokens=1,
                completion_tokens=1,
                cost=0.6,
            )
        )
    refused = client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})
    assert refused.status_code == 409
    assert "limit" in refused.json()["detail"]
    stopped = client.post(f"/api/runs/{run_id}/finish", json={"status": "finished"})
    assert stopped.json()["status"] == "stopped"


def test_a_round_adds_up_the_improver_and_its_evaluations(client):
    started = create(client)
    round_id = started["round_id"]
    client.post(
        f"/api/rounds/{round_id}/model-calls",
        json={"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.02},
    )
    run_id = finish_evaluation(client, round_id, 1, "held_out", tiny=True)
    with db.connect() as connection:
        columns = [c.name for c in model_calls.columns]
        assert "cost" in columns
        assert connection.execute(select(rounds.c.round_id)).first()
        assert run_id
    assert client.get(f"/api/rounds/{round_id}").json()["spent"] >= 0.02


def test_a_senior_stops_a_round_and_a_handler_cannot(client):
    started = create(client)
    round_id = started["round_id"]
    marcus = client.post(
        f"/api/rounds/{round_id}/stop", headers=sign_in.bearer("marcus")
    )
    assert marcus.status_code == 403
    stopped = client.post(f"/api/rounds/{round_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert client.get(f"/api/rounds/{round_id}").json()["status"] == "stopped"


def test_the_improver_sees_what_it_may_and_not_the_key(client):
    started = create(client)
    shown = client.get(f"/api/rounds/{started['round_id']}/view").json()
    assert shown["current_version"]["version"] == 1
    assert shown["rule"] == improvement.RULE
    assert shown["spending_left"] == pytest.approx(0.5)
    assert shown["objectives_on_the_held_out_set"]["accuracy"] == 0.6
    assert "eval_decisions" not in json.dumps(shown)


def test_an_estimate_names_what_is_left(client):
    started = create(client)
    estimate = client.get(f"/api/rounds/{started['round_id']}/estimates/1").json()
    assert estimate["evaluations"] == 3
    assert estimate["left"] == pytest.approx(0.5)
    assert estimate["estimate"] == pytest.approx(3 * estimate["each"])


def test_a_restart_stops_a_working_round_and_leaves_a_waiting_one(client):
    started = create(client)
    client.post("/api/runs/stop-running")
    assert client.get(f"/api/rounds/{started['round_id']}").json()["status"] == (
        "stopped"
    )
