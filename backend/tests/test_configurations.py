import difflib

from sqlalchemy import insert, update

from backend import db
from backend.db import configurations, improvement_state, recipes, runs


def add_version(version, instructions, model="openai/gpt-6-luna", effort="low"):
    with db.connect() as connection:
        connection.execute(
            insert(configurations).values(
                version=version,
                instructions=instructions,
                model=model,
                reasoning_effort=effort,
                reason=f"Reason {version}",
                proposed_by="The improver",
                approved_by="Dana Whitfield",
                approved_at="2026-11-25T10:00:00+00:00",
            )
        )


def make_current(version):
    with db.connect() as connection:
        connection.execute(
            update(improvement_state)
            .where(improvement_state.c.key == "current_version")
            .values(value=version)
        )


def kinds(lines):
    return [line["kind"] for line in lines]


def test_version_1_is_the_claim_agent_as_it_was(client):
    claim_md = (db.INSTRUCTIONS / "claim.md").read_text(encoding="utf-8")
    versions = client.get("/api/configurations").json()
    assert len(versions) == 1
    first = versions[0]
    assert first["version"] == 1
    assert first["is_current"] is True
    assert (first["model"], first["reasoning_effort"]) == ("openai/gpt-6-luna", "low")
    assert first["proposed_by"] == first["approved_by"] == "UnderCover"
    assert first["approved_at"]
    assert first["scores"] == {"evaluation": None, "held_out": None}
    fetched = client.get("/api/configurations/1").json()
    assert fetched["instructions"] == claim_md


def test_the_claim_agents_version_holds_no_scores(client):
    fetched = client.get("/api/configurations/1").json()
    assert set(fetched) == {"version", "instructions", "model", "reasoning_effort"}


def test_the_recipe_starts_as_the_labs_first_recipe(client):
    recipe_md = (db.INSTRUCTIONS / "recipe.md").read_text(encoding="utf-8")
    listed = client.get("/api/recipes").json()
    assert [(r["version"], r["is_current"]) for r in listed] == [(1, True)]
    diff = client.get("/api/recipes/1/diff").json()
    assert diff["previous"] is None
    assert diff["lines"] == []
    with db.connect() as connection:
        stored = connection.execute(recipes.select()).mappings().one()
    assert stored["recipe"] == recipe_md
    assert "Improve this recipe" in stored["recipe"]


def test_a_diff_shows_what_a_version_changed_from_the_one_before(client):
    first = client.get("/api/configurations/1").json()["instructions"]
    second = first.replace("# Working a claim", "# Working one claim")
    add_version(2, second, "google/gemini-3.5-flash-lite", "minimal")
    diff = client.get("/api/configurations/2/diff").json()
    assert diff["previous"] == 1
    assert diff["model"] == {
        "before": "openai/gpt-6-luna",
        "after": "google/gemini-3.5-flash-lite",
    }
    assert diff["reasoning_effort"] == {"before": "low", "after": "minimal"}
    changed = [line for line in diff["instructions"] if line["kind"] != "same"]
    assert [(c["kind"], c["text"]) for c in changed if c["kind"] != "gap"] == [
        ("removed", "# Working a claim"),
        ("added", "# Working one claim"),
    ]
    expected = [
        line
        for line in difflib.unified_diff(
            first.splitlines(), second.splitlines(), lineterm="", n=2
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    shown = [
        ("+" if line["kind"] == "added" else "-") + line["text"]
        for line in diff["instructions"]
        if line["kind"] in ("added", "removed")
    ]
    assert shown == expected
    assert diff["reason"] == "Reason 2"
    assert diff["is_current"] is False


def test_the_diff_marks_gaps_and_context(client):
    add_version(2, "a\nb\nc\nd\ne\nf\ng\nh")
    add_version(3, "a\nb\nc\nd\ne\nf\ng\nH")
    lines = client.get("/api/configurations/3/diff").json()["instructions"]
    assert kinds(lines) == ["gap", "same", "same", "removed", "added"]


def test_the_first_version_has_no_diff(client):
    diff = client.get("/api/configurations/1/diff").json()
    assert diff["previous"] is None
    assert diff["instructions"] == []
    assert diff["model"]["before"] is None
    assert diff["model"]["after"] == "openai/gpt-6-luna"


def test_a_recipe_diff_matches_difflib(client):
    with db.connect() as connection:
        first = connection.execute(recipes.select()).mappings().one()["recipe"]
        connection.execute(
            insert(recipes).values(
                version=2,
                recipe=first + "6. Also stop.\n",
                reason="More",
                proposed_by="The improver",
                approved_by="Dana Whitfield",
                approved_at="2026-11-25T10:00:00+00:00",
            )
        )
    diff = client.get("/api/recipes/2/diff").json()
    assert diff["previous"] == 1
    added = [line["text"] for line in diff["lines"] if line["kind"] == "added"]
    assert added == ["6. Also stop."]


def test_an_unknown_version_is_not_found(client):
    assert client.get("/api/configurations/9").status_code == 404
    assert client.get("/api/configurations/9/diff").status_code == 404
    assert client.get("/api/recipes/9/diff").status_code == 404


def test_the_current_version_is_marked_and_served(client):
    add_version(2, "Version two.")
    assert client.get("/api/configurations/current").json()["version"] == 1
    make_current(2)
    current = client.get("/api/configurations/current").json()
    assert current["version"] == 2
    assert current["instructions"] == "Version two."
    listed = client.get("/api/configurations").json()
    assert {v["version"]: v["is_current"] for v in listed} == {1: False, 2: True}


def dispatch_body(**changes):
    body = {"concurrency": 3, "version": 1, "model": "m", "reasoning_effort": "low"}
    return body | changes


def test_a_dispatch_and_an_evaluation_record_their_version(client):
    add_version(2, "Version two.")
    dispatch = client.post("/api/runs/dispatch", json=dispatch_body(version=2)).json()
    assert dispatch["version"] == 2
    client.post(f"/api/runs/{dispatch['run_id']}/finish", json={"status": "finished"})
    evaluation = client.post(
        "/api/runs/evaluation",
        json=dispatch_body(version=1, evaluation_set="held_out", tiny=True),
    ).json()
    assert evaluation["version"] == 1
    shown = client.get(f"/api/evaluations/{evaluation['run_id']}").json()
    assert shown["version"] == 1
    assert client.get("/api/evaluations").json()[0]["version"] == 1


def test_a_version_that_does_not_exist_cannot_be_run(client):
    refused = client.post("/api/runs/dispatch", json=dispatch_body(version=9))
    assert refused.status_code == 404
    with db.connect() as connection:
        assert connection.execute(runs.select()).first() is None


def test_a_claim_run_uses_the_current_version_and_the_claim_shows_it(client):
    add_version(2, "Version two.")
    make_current(2)
    started = client.post("/api/claims/CLM-1001/start-run").json()
    run = client.get(f"/api/runs/{started['run_id']}").json()
    assert (run["version"], run["kind"]) == (2, "claim")
    assert client.get("/api/claims/CLM-1001").json()["version"] == 2
