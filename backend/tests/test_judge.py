import time

import pytest

from backend import judge
from backend.tests.stub_judge import LEVELS, StubJev, answer


def create(client, tiny=True):
    body = {
        "concurrency": 3,
        "evaluation_set": "held_out",
        "tiny": tiny,
        "version": 1,
        "model": "m",
        "reasoning_effort": "low",
    }
    return client.post("/api/runs/evaluation", json=body).json()


def start(client, run_id, claim_id):
    client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})


def decide(client, claim_id, reason="The agent's reason.", payout=100):
    body = {
        "decision": "approve",
        "payout": payout,
        "reason": reason,
        "policy_section": "Section 3",
    }
    client.post(f"/api/claims/{claim_id}/decision", json=body)


def finish(client, run_id, status="finished"):
    return client.post(f"/api/runs/{run_id}/finish", json={"status": status}).json()


def evaluation(client, decided, reason="The agent's reason."):
    """An evaluation of the five tiny claims, with the first few decided."""
    run = create(client)
    copies = run["claims_worked"]
    for copy in copies[:decided]:
        start(client, run["run_id"], copy)
        decide(client, copy, reason)
    return run["run_id"], copies


def shown(client, run_id):
    return client.get(f"/api/evaluations/{run_id}").json()


def rows(client, run_id):
    return {r["claim_id"]: r for r in shown(client, run_id)["claims"]}


def test_the_letter_is_the_most_likely_level_not_the_rounded_score(client, jev):
    jev.reply = lambda state: answer({"0": 0.35, "1": 0.0, "2": 0.0, "3": 0.65})
    run_id, copies = evaluation(client, 1)
    finish(client, run_id)
    grade = rows(client, run_id)[copies[0]]["grade"]
    assert grade["score"] == pytest.approx(1.95)
    assert round(grade["score"]) == 2
    assert grade["letter"] == "A"


def test_the_two_checks_are_kept_as_probabilities(client, jev):
    jev.reply = lambda state: answer(grounds=0.93, section=0.07)
    run_id, copies = evaluation(client, 1)
    finish(client, run_id)
    grade = rows(client, run_id)[copies[0]]["grade"]
    assert grade["same_grounds"] == pytest.approx(0.93)
    assert grade["same_section"] == pytest.approx(0.07)


def test_the_call_is_made_with_a_timeout_no_retries_and_the_address_set_on_it(
    client, jev
):
    run_id, copies = evaluation(client, 1, "Because the delay was long.")
    finish(client, run_id)
    (call,) = jev.calls
    assert call["model"] == "typesafe/jev-1.13"
    assert call["timeout_ms"] == 10_000
    assert call["retries"].strategy == "none"
    assert call["server_url"] == "https://openrouter.ai"
    assert set(call["questions"]) == {"grade", "same_grounds", "same_section"}
    assert call["questions"]["grade"]["type"] == "score"
    assert call["questions"]["same_grounds"]["type"] == "noul"
    handler = call["state"]["handler"]
    assert set(handler) == {"decision", "reason", "reference"}
    assert handler["reason"]
    assert call["state"]["agent"] == {
        "decision": "approve",
        "rationale": "Because the delay was long.",
    }


def test_the_address_is_read_once_from_the_environment(client, jev, monkeypatch):
    monkeypatch.setenv("JUDGE_SERVER_URL", "http://127.0.0.1:9")
    judge.server_url.cache_clear()
    try:
        run_id, _ = evaluation(client, 1)
        finish(client, run_id)
        assert jev.calls[0]["server_url"] == "http://127.0.0.1:9"
    finally:
        monkeypatch.delenv("JUDGE_SERVER_URL")
        judge.server_url.cache_clear()


def test_an_undecided_claim_is_graded_f_without_asking_the_judge(client, jev):
    run_id, copies = evaluation(client, 0)
    finish(client, run_id)
    assert jev.calls == []
    for row in shown(client, run_id)["claims"]:
        assert row["grade"] == {
            "score": 0.0,
            "letter": "F",
            "same_grounds": 0.0,
            "same_section": 0.0,
        }
    assert shown(client, run_id)["grade"] == 0.0


def test_a_claim_the_judge_couldnt_grade_is_left_out_of_the_average(client, jev):
    def reply(state):
        rationale = state["agent"]["rationale"]
        if rationale == "breaks":
            raise TimeoutError("Jev took too long")
        if rationale == "top":
            return answer({"0": 0, "1": 0, "2": 0, "3": 1})
        return answer({"0": 0, "1": 0.5, "2": 0.5, "3": 0})

    jev.reply = reply
    run = create(client)
    copies = run["claims_worked"]
    for copy, reason in zip(copies[:3], ["top", "middling", "breaks"], strict=True):
        start(client, run["run_id"], copy)
        decide(client, copy, reason)
    finish(client, run["run_id"])
    by_claim = rows(client, run["run_id"])
    assert by_claim[copies[0]]["grade"]["score"] == pytest.approx(3.0)
    assert by_claim[copies[1]]["grade"]["score"] == pytest.approx(1.5)
    assert by_claim[copies[2]]["grade"] is None
    undecided = [by_claim[c]["grade"]["score"] for c in copies[3:]]
    assert undecided == [0.0, 0.0]
    assert shown(client, run["run_id"])["grade"] == pytest.approx((3 + 1.5 + 0 + 0) / 4)


def test_an_evaluation_with_no_graded_claim_has_no_grade(client, jev):
    def reply(state):
        raise ConnectionError("no network")

    jev.reply = reply
    run_id, copies = evaluation(client, 5)
    finish(client, run_id)
    assert shown(client, run_id)["grade"] is None
    assert all(row["grade"] is None for row in shown(client, run_id)["claims"])
    assert client.get("/api/evaluations").json()[0]["grade"] is None


def test_the_claims_are_graded_in_parallel_as_the_evaluation_finishes(client, jev):
    jev.delay = 0.3
    run_id, _ = evaluation(client, 5)
    started = time.monotonic()
    run = finish(client, run_id)
    assert time.monotonic() - started < 1.2
    assert jev.peak == 5
    assert len(jev.calls) == 5
    assert run["status"] == "finished"


def test_the_judges_cost_is_recorded_apart_from_the_claim_agents(client, jev):
    jev.reply = lambda state: answer(cost=0.01)
    run_id, copies = evaluation(client, 3)
    for copy in copies:
        body = {"run_id": run_id, "prompt_tokens": 1, "completion_tokens": 1}
        client.post(f"/api/claims/{copy}/model-calls", json=body | {"cost": 0.1})
    finish(client, run_id)
    view = shown(client, run_id)
    assert view["judge_cost"] == pytest.approx(0.03)
    assert view["cost_per_claim"] == pytest.approx(0.1)
    assert client.get("/api/evaluations").json()[0]["judge_cost"] == pytest.approx(0.03)
    assert rows(client, run_id)[copies[0]]["judge_cost"] == pytest.approx(0.01)


def test_the_average_grade_shows_on_the_list_and_each_claims_grade_on_the_page(
    client, jev
):
    run_id, copies = evaluation(client, 2)
    finish(client, run_id)
    listed = client.get("/api/evaluations").json()[0]
    one = sum(int(level) * chance for level, chance in LEVELS.items())
    expected = (2 * one) / 5
    assert listed["grade"] == pytest.approx(expected)
    page = rows(client, run_id)
    assert page[copies[0]]["grade"]["letter"] == "B"


def test_a_stopped_or_failed_evaluation_is_not_graded(client, jev):
    run_id, _ = evaluation(client, 3)
    client.post("/api/runs/stop-running")
    assert shown(client, run_id)["status"] == "stopped"
    assert shown(client, run_id)["grade"] is None
    assert jev.calls == []
    run_id, _ = evaluation(client, 3)
    finish(client, run_id, "failed")
    assert jev.calls == []


def test_with_model_name_set_the_judge_isnt_called_and_claims_show_not_graded(
    client, jev, monkeypatch
):
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    run_id, _ = evaluation(client, 3)
    finish(client, run_id)
    assert jev.calls == []
    assert shown(client, run_id)["grade"] is None
    assert all(row["grade"] is None for row in shown(client, run_id)["claims"])


def test_finishing_a_dispatch_does_not_call_the_judge(client, jev):
    body = {"concurrency": 3, "version": 1, "model": "m", "reasoning_effort": "low"}
    run = client.post("/api/runs/dispatch", json=body).json()
    finish(client, run["run_id"])
    assert jev.calls == []


def test_finishing_twice_grades_once(client, jev):
    run_id, _ = evaluation(client, 2)
    finish(client, run_id)
    finish(client, run_id)
    assert len(rows(client, run_id)) == 5
    assert shown(client, run_id)["grade"] is not None


def test_the_stub_is_what_the_tests_use(jev):
    assert isinstance(jev, StubJev)
    assert judge.client() is jev
