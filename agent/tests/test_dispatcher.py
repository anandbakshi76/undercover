import re
import threading
import time
from pathlib import Path

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError

from agent import claim as claim_agent
from agent import dispatcher, main
from agent.tests.stubs import ClaimStub, claim, new_dispatch, new_run, recorded_steps

LIMIT = ModelRateLimitError("429 Too Many Requests")
ERROR = RuntimeError("model unavailable")


def work(monkeypatch, **options):
    model = ClaimStub(messages=iter([]), **options)
    monkeypatch.setattr(main, "model", lambda: model)
    return model


def statuses():
    return {c["claim_id"]: c["status"] for c in main.call("GET", "/api/claims")}


def test_never_more_than_n_claims_are_worked_at_once(monkeypatch):
    for concurrency in [1, 3, 25]:
        active, peak = [0], [0]
        guard = threading.Lock()

        def slow(claim_id, run_id, active=active, peak=peak, guard=guard):
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with guard:
                active[0] -= 1
            return f"{claim_id}: done"

        monkeypatch.setattr(claim_agent, "work_claim", slow)
        run_id = new_dispatch(concurrency)
        results = dispatcher.dispatch(run_id, concurrency)
        assert len(results) == 25
        assert peak[0] == min(concurrency, 25)
        main.call(
            "POST", f"/api/runs/{run_id}/finish", {"status": "finished", "summary": ""}
        )


def test_a_dispatch_works_every_claim_with_one_decision_each(monkeypatch):
    work(monkeypatch)
    run_id = new_dispatch(5)
    results = dispatcher.dispatch(run_id, 5)
    assert len(results) == 25
    assert set(statuses().values()) == {"decided"}
    run = main.call("GET", f"/api/runs/{run_id}")
    assert len(run["claims_worked"]) == 25
    kinds = [s["text"].split("(")[0] for s in recorded_steps("CLM-1001")]
    assert kinds.count("record_decision") == 1
    assert kinds.count("pay_claim") == 1


def test_claims_in_progress_go_first_then_waiting_ones_oldest_first(monkeypatch):
    first = new_run()
    main.call("POST", "/api/claims/CLM-1007/start", {"run_id": first})
    order = []
    monkeypatch.setattr(
        claim_agent, "work_claim", lambda claim_id, run_id: order.append(claim_id) or ""
    )
    run_id = new_dispatch(1)
    dispatcher.dispatch(run_id, 1)
    queue = {c["claim_id"]: c for c in main.call("GET", "/api/claims")}
    order = list(dict.fromkeys(order))
    assert order[0] == "CLM-1007"
    rest = order[1:]
    assert rest == sorted(rest, key=lambda c: (queue[c]["submitted_on"], c))
    assert len(order) == 25


def test_a_rate_limit_carries_the_claim_on_as_the_same_attempt(monkeypatch):
    work(monkeypatch, failures={"CLM-1001": [LIMIT] * 3})
    run_id = new_dispatch(2)
    dispatcher.dispatch(run_id, 2)
    done = claim("CLM-1001")
    assert done["status"] == "decided"
    assert done["attempts"] == 0
    assert done["thread_id"] == f"CLM-1001-{run_id}-1"
    kinds = [s["text"].split("(")[0] for s in recorded_steps("CLM-1001")]
    assert kinds.count("record_decision") == 1
    assert kinds.count("pay_claim") == 1
    assert kinds.count("get_claim") == 1


def test_a_timeout_is_waited_out_the_same_way(monkeypatch):
    work(monkeypatch, failures={"CLM-1001": [ModelTimeoutError("slow")] * 3})
    dispatcher.dispatch(new_dispatch(2), 2)
    assert claim("CLM-1001")["status"] == "decided"
    assert claim("CLM-1001")["attempts"] == 0


def test_a_claim_still_rate_limited_stays_in_progress_and_the_rest_finish(
    monkeypatch,
):
    work(monkeypatch, failures={"CLM-1001": [LIMIT] * 40})
    run_id = new_dispatch(4)
    results = dispatcher.dispatch(run_id, 4)
    held = claim("CLM-1001")
    assert held["status"] == "in_progress"
    assert held["attempts"] == 0
    assert held["decision"] is None
    assert any(
        r.startswith("CLM-1001: in progress, left for the next dispatch")
        for r in results
    )
    assert sum(1 for s in statuses().values() if s == "decided") == 24
    main.call(
        "POST", f"/api/runs/{run_id}/finish", {"status": "finished", "summary": ""}
    )
    work(monkeypatch)
    again = new_dispatch(2)
    dispatcher.dispatch(again, 2)
    carried = claim("CLM-1001")
    assert carried["status"] == "decided"
    assert carried["thread_id"] == held["thread_id"]
    assert carried["attempts"] == 0
    assert "CLM-1001" in main.call("GET", f"/api/runs/{again}")["claims_worked"]


def test_a_claim_that_fails_goes_back_and_is_tried_once_more(monkeypatch):
    work(monkeypatch, failures={"CLM-1001": [ERROR] * 3})
    run_id = new_dispatch(3)
    dispatcher.dispatch(run_id, 3)
    done = claim("CLM-1001")
    assert done["status"] == "decided"
    assert done["attempts"] == 1
    assert done["thread_id"] == f"CLM-1001-{run_id}-2"
    assert set(statuses().values()) == {"decided"}


def test_a_claim_that_fails_twice_is_left_for_a_handler(monkeypatch):
    work(monkeypatch, failures={"CLM-1001": [ERROR] * 6})
    dispatcher.dispatch(new_dispatch(3), 3)
    assert claim("CLM-1001")["status"] == "needs_handler"
    others = {k: v for k, v in statuses().items() if k != "CLM-1001"}
    assert set(others.values()) == {"decided"}


def test_a_claim_being_resumed_is_skipped(monkeypatch):
    work(monkeypatch)
    lock = claim_agent.LOCKS.setdefault("CLM-1001", threading.Lock())
    with lock:
        dispatcher.dispatch(new_dispatch(5), 5)
    assert claim("CLM-1001")["status"] == "waiting"
    assert sum(1 for s in statuses().values() if s == "decided") == 24


def test_each_claims_trace_is_tagged_with_its_run(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    config = main.tracing("CLM-1001-RUN-1-1", ["RUN-1"])
    assert config.get("metadata", {})["langfuse_tags"] == ["RUN-1"]


def test_the_dispatcher_touches_the_claim_agent_only_through_work_claim():
    source = Path(dispatcher.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r"[ (]claim[.](\w+)", source))
    assert used == {"work_claim", "is_working"}
    assert "queue_agent" not in source
    assert "write_todos" not in source
