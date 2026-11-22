"""UnderCover's payout rules for an approved claim.

The rules come from section 10 of the policy wording, the plan limits and
sections 6, 7 and 10 of the claims manual.
"""

from math import ceil, inf
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

CASH_LIMIT = 100.0
HOTEL_NIGHT_LIMIT = 350.0
MEALS_PER_TRAVELER_PER_DAY = 35.0

Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]

BENEFIT_MAXIMUM = {
    "trip_cancellation": "trip_cancellation_max",
    "trip_interruption": "trip_cancellation_max",
    "trip_delay": "trip_delay_max",
    "baggage_delay": "baggage_delay_max",
    "baggage": "baggage_max",
    "passport": "passport_max",
    "emergency_medical": "emergency_medical_max",
}


class Line(BaseModel):
    """One covered item or expense on a claim."""

    description: str = Field(description="What the item or expense is")
    amount: float = Field(
        description="The total claimed for the line in US dollars, before any "
        "limits. For several items, the total for all of them"
    )
    quantity: int = Field(
        ge=1,
        description="How many separate items the line holds, such as 3 for three "
        "shirts. A pair or a set is one item",
    )
    kind: Literal[
        "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
    ] = Field(
        description=(
            "item: a belonging, or a pair or set that counts as one item. "
            "electronics: only a phone, tablet, laptop, camera or its lens, "
            "e-reader or smartwatch; any other device is an item. "
            "cash: cash that was lost or stolen. "
            "expense: any other cost with a receipt, such as a medical bill, a "
            "meal, a taxi, a fee or a prepaid trip cost. "
            "hotel: a hotel during a trip delay. "
            "meals_without_receipts: meals during a trip delay with no receipts."
        )
    )
    nights: int = Field(ge=0, description="For a hotel, the number of nights, else 0")
    hours: float = Field(
        ge=0,
        description="For meals without receipts, the length of the delay in hours, "
        "else 0",
    )


class Step(TypedDict):
    step: str
    amount: float


class Payout(TypedDict):
    calculation: list[Step]
    payout: float
    needs_sign_off: bool


def step(description: str, amount: float) -> Step:
    return {"step": description, "amount": round(amount, 2)}


def limit_for(line: Line, plan: dict[str, Any], add_ons: list[dict[str, Any]], n: int):
    """Return the most UnderCover pays for one line, or infinity if it has none."""
    match line.kind:
        case "item":
            return plan["per_item_limit"] * line.quantity
        case "electronics":
            per_item = plan["per_item_limit"]
            for add_on in add_ons:
                per_item = add_on.get("electronics_per_item_limit", per_item)
            return per_item * line.quantity
        case "hotel":
            return HOTEL_NIGHT_LIMIT * ceil(n / 2) * line.nights
        case "meals_without_receipts":
            return MEALS_PER_TRAVELER_PER_DAY * n * ceil(line.hours / 24)
        case _:
            return inf


class PayoutRequest(BaseModel):
    benefit: Benefit
    lines: list[Line]
    paid_by_others: float = Field(ge=0)


def eligible_steps(
    lines: list[Line], plan: dict[str, Any], add_ons: list[dict[str, Any]], n: int
) -> list[Step]:
    """Return the eligible amount of each line, with cash capped in all."""
    calculation: list[Step] = []
    for line in lines:
        if line.kind == "cash":
            continue
        limit = limit_for(line, plan, add_ons, n)
        if line.amount > limit:
            text = f"{line.description}, limited to ${limit:,.2f}"
            calculation.append(step(text, limit))
        else:
            calculation.append(step(line.description, line.amount))
    cash = sum(line.amount for line in lines if line.kind == "cash")
    if cash:
        text = f"Cash, up to ${CASH_LIMIT:,.0f} in all"
        calculation.append(step(text, min(cash, CASH_LIMIT)))
    return calculation


def work_out_payout(
    policy: dict[str, Any],
    plans: dict[str, Any],
    approval_limit: float,
    request: PayoutRequest,
) -> Payout:
    """Work out the payout on an approved claim, following wording section 10."""
    plan = next(plan for plan in plans["plans"] if plan["plan"] == policy["plan"])
    add_ons = [a for a in plans["add_ons"] if a["add_on"] in policy["add_ons"]]
    calculation = eligible_steps(request.lines, plan, add_ons, len(policy["travelers"]))
    total = sum(entry["amount"] for entry in calculation)

    others = request.paid_by_others
    if others:
        calculation.append(step("Less what others have paid or refunded", -others))
        total -= others

    benefit = request.benefit
    if benefit in plans["deductible_applies_to"] and total > 0:
        deductible = min(plan["deductible"], total)
        if deductible:
            calculation.append(step("Less the deductible", -deductible))
            total -= deductible

    maximum = plan[BENEFIT_MAXIMUM[benefit]]
    if benefit in ("trip_cancellation", "trip_interruption"):
        maximum = min(maximum, policy["trip"]["trip_cost"])
    if total > maximum:
        text = f"Less the amount over the maximum of ${maximum:,.0f}"
        calculation.append(step(text, maximum - total))
        total = maximum

    if total < 0:
        calculation.append(step("Nothing is left to pay", -total))
        total = 0.0

    payout = round(total, 2)
    return {
        "calculation": calculation,
        "payout": payout,
        "needs_sign_off": payout > approval_limit,
    }
