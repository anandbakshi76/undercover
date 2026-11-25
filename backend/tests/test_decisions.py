import pytest

from backend.tests import sign_in


def decide(client, claim_id, decision="approve", payout=420.0):
    return client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": decision,
            "payout": payout,
            "reason": "The delay was covered.",
            "policy_section": "Section 3, Trip Delay",
        },
    )


def pay(client, claim_id, amount):
    return client.post(f"/api/claims/{claim_id}/payment", json={"amount": amount})


def sign_off(client, claim_id, who="dana"):
    return client.post(f"/api/claims/{claim_id}/sign-off", headers=sign_in.bearer(who))


def test_approval_within_the_limit_is_in_progress_until_it_is_paid(client):
    claim = decide(client, "CLM-1001").json()
    assert claim["status"] == "in_progress"
    assert claim["decision"]["payout"] == 420.0
    assert claim["decision"]["policy_section"] == "Section 3, Trip Delay"
    paid = pay(client, "CLM-1001", 420.0).json()
    assert paid["payment"]["amount"] == 420.0
    assert paid["status"] == "decided"


def test_approval_over_the_limit_waits_and_blocks_payment(client):
    claim = decide(client, "CLM-1002", payout=1250.0).json()
    assert claim["status"] == "awaiting_sign_off"
    response = pay(client, "CLM-1002", 1250.0)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "A payout over $1,000.00 needs a handler's sign-off before it can be paid."
    )
    assert client.get("/api/claims/CLM-1002").json()["payment"] is None


def test_sign_off_records_who_and_when_and_leaves_the_payment_to_the_agent(client):
    decide(client, "CLM-1002", payout=1250.0)
    claim = sign_off(client, "CLM-1002").json()
    assert claim["status"] == "in_progress"
    assert claim["sign_off"]["handler_id"] == "HND-01"
    assert claim["payment"] is None
    paid = pay(client, "CLM-1002", 1250.0).json()
    assert paid["status"] == "decided"
    assert paid["payment"]["signed_off_by"] == "HND-01"
    assert paid["payment"]["signed_off_at"] == claim["sign_off"]["signed_off_at"]
    assert pay(client, "CLM-1002", 1250.0).json()["payment"] == paid["payment"]


@pytest.mark.parametrize("decision", ["decline", "refer"])
def test_declined_and_referred_claims_cannot_be_paid(client, decision):
    claim = decide(client, "CLM-1001", decision=decision, payout=0).json()
    assert claim["status"] == "decided"
    response = pay(client, "CLM-1001", 100.0)
    assert response.status_code == 409
    assert response.json()["detail"] == "Only an approved claim can be paid."


def test_undecided_claim_cannot_be_paid(client):
    assert pay(client, "CLM-1001", 5000.0).status_code == 409


def test_payment_must_equal_the_payout(client):
    decide(client, "CLM-1001")
    response = pay(client, "CLM-1001", 400.0)
    assert response.status_code == 409
    assert "$420.00" in response.json()["detail"]


def test_decline_with_a_payout_is_rejected(client):
    assert decide(client, "CLM-1001", decision="decline", payout=50).status_code == 422


def test_unknown_decision_is_rejected(client):
    assert decide(client, "CLM-1001", decision="maybe").status_code == 422


def test_a_repeated_decision_counts_as_done(client):
    first = decide(client, "CLM-1001").json()
    again = decide(client, "CLM-1001")
    assert again.status_code == 200
    assert again.json()["decision"] == first["decision"]


def test_a_different_decision_is_refused(client):
    decide(client, "CLM-1001")
    assert decide(client, "CLM-1001", payout=400.0).status_code == 409
    assert decide(client, "CLM-1001", decision="decline", payout=0).status_code == 409
    assert client.get("/api/claims/CLM-1001").json()["decision"]["payout"] == 420.0


def test_a_repeated_payment_counts_as_done(client):
    decide(client, "CLM-1001")
    paid = pay(client, "CLM-1001", 420.0).json()["payment"]
    again = pay(client, "CLM-1001", 420.0)
    assert again.status_code == 200
    assert again.json()["payment"] == paid


def test_a_different_payment_is_refused(client):
    decide(client, "CLM-1001")
    pay(client, "CLM-1001", 420.0)
    response = pay(client, "CLM-1001", 400.0)
    assert response.status_code == 409
    assert response.json()["detail"] == "This claim has already been paid."


def test_sign_off_needs_a_signed_in_handler(client, anonymous):
    decide(client, "CLM-1002", payout=1250.0)
    path = "/api/claims/CLM-1002/sign-off"
    assert anonymous.post(path).status_code == 401
    assert sign_off(client, "CLM-1002", "nobody@example.com").status_code == 401
    assert client.get("/api/claims/CLM-1002").json()["sign_off"] is None


def test_sign_off_needs_a_handler_who_can_approve(client):
    decide(client, "CLM-1002", payout=1250.0)
    refused = sign_off(client, "CLM-1002", "marcus")
    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        "Marcus Oyelaran can't sign off a payout over the limit."
    )
    claim = client.get("/api/claims/CLM-1002").json()
    assert claim["status"] == "awaiting_sign_off"
    assert claim["sign_off"] is None


def test_sign_off_needs_a_claim_waiting_for_it(client):
    decide(client, "CLM-1001")
    assert sign_off(client, "CLM-1001").status_code == 409


def test_steps_come_back_in_order(client):
    for kind, text in [("tool_call", "get_claim"), ("reply", "Approved.")]:
        client.post("/api/claims/CLM-1001/steps", json={"kind": kind, "text": text})
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert [(s["kind"], s["text"]) for s in steps] == [
        ("tool_call", "get_claim"),
        ("reply", "Approved."),
    ]


def test_steps_for_an_unknown_claim_are_404(client):
    assert client.get("/api/claims/CLM-0000/steps").status_code == 404


def test_a_sign_off_tells_the_agent_once_it_is_saved(client, agent):
    decide(client, "CLM-1002", payout=1250.0)
    claim = sign_off(client, "CLM-1002").json()
    assert agent.calls == [("POST", "/api/claims/CLM-1002/resume", None)]
    assert claim["status"] == "in_progress"
    assert claim["payment"] is None


def test_a_sign_off_stands_when_the_agent_cannot_be_reached(client, agent):
    agent.down = True
    decide(client, "CLM-1002", payout=1250.0)
    response = sign_off(client, "CLM-1002")
    assert response.status_code == 200
    saved = client.get("/api/claims/CLM-1002").json()
    assert saved["sign_off"]["handler_id"] == "HND-01"
    assert saved["status"] == "in_progress"
    assert saved["payment"] is None


def test_a_refused_sign_off_tells_the_agent_nothing(client, agent):
    decide(client, "CLM-1001")
    assert sign_off(client, "CLM-1001").status_code == 409
    assert agent.calls == []
