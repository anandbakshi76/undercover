import pytest

from backend import payout


def line(description, amount, kind="item", **extra):
    return {
        "description": description,
        "amount": amount,
        "kind": kind,
        "quantity": 1,
        "nights": 0,
        "hours": 0,
    } | extra


def work_out(client, policy_id, benefit, lines, paid_by_others=0):
    response = client.post(
        f"/api/policies/{policy_id}/payout",
        json={"benefit": benefit, "lines": lines, "paid_by_others": paid_by_others},
    )
    assert response.status_code == 200
    return response.json()


def test_limits_what_others_paid_and_the_deductible(client):
    result = work_out(
        client,
        "POL-2001",
        "baggage",
        [line("Suitcase", 400), line("Shirts", 75, quantity=3)],
        paid_by_others=100,
    )
    assert result["payout"] == 225.0
    assert result["calculation"][0] == {
        "step": "Suitcase, limited to $300.00",
        "amount": 300.0,
    }
    assert result["calculation"][-1]["step"] == "Less the deductible"
    assert not result["needs_sign_off"]


def test_electronics_plus_raises_the_electronics_limit(client):
    laptop = [line("Laptop", 1100, kind="electronics")]
    assert work_out(client, "POL-2001", "baggage", laptop)["payout"] == 250.0
    plus = work_out(client, "POL-2009", "baggage", laptop)
    assert plus["payout"] == 1050.0
    assert plus["needs_sign_off"]


def test_cash_is_capped_at_100_in_all(client):
    cash = [line("Wallet", 80, kind="cash"), line("Purse", 70, kind="cash")]
    assert work_out(client, "POL-2001", "baggage", cash)["payout"] == 50.0


def test_hotel_and_meal_allowances(client):
    meals = [line("Meals", 200, kind="meals_without_receipts", hours=30)]
    assert work_out(client, "POL-2001", "trip_delay", meals)["payout"] == 70.0
    hotel = [line("Hotel", 3000, kind="hotel", nights=2)]
    result = work_out(client, "POL-2015", "trip_delay", hotel)
    assert result["calculation"][0]["amount"] == 2100.0
    assert result["payout"] == 1500.0
    assert result["calculation"][-1]["step"].startswith("Less the amount over")


def test_trip_cancellation_is_capped_at_the_trip_cost(client):
    expense = [line("Prepaid tour", 2000, kind="expense")]
    result = work_out(client, "POL-2019", "trip_cancellation", expense)
    assert result["payout"] == 1400.0


def test_nothing_left_pays_zero(client):
    expense = [line("Taxi", 100, kind="expense")]
    result = work_out(client, "POL-2001", "trip_delay", expense, paid_by_others=500)
    assert result["payout"] == 0.0
    assert result["calculation"][-1]["step"] == "Nothing is left to pay"


@pytest.mark.parametrize(
    ("limit", "needs"), [(1000.0, True), (1050.0, False), (2000.0, False)]
)
def test_sign_off_follows_the_approval_limit(limit, needs):
    policy = {"plan": "premium", "add_ons": ["electronics_plus"], "travelers": ["A"]}
    plans = {
        "plans": [{"plan": "premium", "per_item_limit": 300.0, "baggage_max": 1500.0}],
        "add_ons": [
            {"add_on": "electronics_plus", "electronics_per_item_limit": 1050.0}
        ],
        "deductible_applies_to": [],
    }
    request = payout.PayoutRequest(
        benefit="baggage",
        lines=[line("Laptop", 1050, kind="electronics")],
        paid_by_others=0,
    )
    result = payout.work_out_payout(policy, plans, limit, request)
    assert result["payout"] == 1050.0
    assert result["needs_sign_off"] is needs


def test_bad_requests(client):
    body = {"benefit": "baggage", "lines": [], "paid_by_others": 0}
    assert client.post("/api/policies/POL-0000/payout", json=body).status_code == 404
    bad_benefit = body | {"benefit": "pets"}
    assert (
        client.post("/api/policies/POL-2001/payout", json=bad_benefit).status_code
        == 422
    )
    bad_line = body | {"lines": [line("Thing", 10, kind="jewel")]}
    assert (
        client.post("/api/policies/POL-2001/payout", json=bad_line).status_code == 422
    )
