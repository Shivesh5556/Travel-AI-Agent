"""
All the money maths.

Rule we follow in this project: an LLM never does arithmetic. It writes
sentences, we do the sums. Every number on the screen comes from this file,
which means it is testable (see tests/test_budget.py) and it cannot
hallucinate.
"""

# Daily spend per person, in rupees, at three comfort levels.
DAILY_FOOD = {"shoestring": 450, "normal": 900, "comfort": 1800}
DAILY_LOCAL_TRANSPORT = {"shoestring": 150, "normal": 350, "comfort": 900}
DAILY_ACTIVITIES = {"shoestring": 200, "normal": 500, "comfort": 1200}

STYLES = ["shoestring", "normal", "comfort"]

# Always keep a bit aside. Something always costs more than you planned.
BUFFER_PERCENT = 8
# The solver may trim our OWN safety buffer down to this before it cuts what
# the traveller spends on food and activities (see agent/replan.py).
MIN_BUFFER_PERCENT = 4


def compute(travel_total_inr: int, stay_total_inr: int, days: int, travelers: int,
            budget_inr: int, style: str = "normal", buffer_percent: float | None = None) -> dict:
    """
    Add everything up and say whether it fits.

    Returns a dict with every line item, the total, and how far over or under
    budget we are. The agent uses "over_by_inr" to decide whether to re-plan.
    """
    style = style if style in STYLES else "normal"

    food = DAILY_FOOD[style] * days * travelers
    local = DAILY_LOCAL_TRANSPORT[style] * days * travelers
    activities = DAILY_ACTIVITIES[style] * days * travelers

    subtotal = travel_total_inr + stay_total_inr + food + local + activities
    pct = BUFFER_PERCENT if buffer_percent is None else buffer_percent
    buffer = int(subtotal * pct / 100)
    total = subtotal + buffer

    return {
        "style": style,
        "lines": [
            {"label": "Getting there and back", "amount_inr": travel_total_inr},
            {"label": f"Stay ({days - 1 if days > 1 else 1} nights)", "amount_inr": stay_total_inr},
            {"label": "Food", "amount_inr": food},
            {"label": "Local transport", "amount_inr": local},
            {"label": "Entry tickets and activities", "amount_inr": activities},
            {"label": f"Buffer ({pct:g}%)", "amount_inr": buffer},
        ],
        "subtotal_inr": subtotal,
        "buffer_inr": buffer,
        "buffer_percent": pct,
        "total_inr": total,
        "budget_inr": budget_inr,
        "fits": total <= budget_inr,
        "over_by_inr": max(0, total - budget_inr),
        "left_over_inr": max(0, budget_inr - total),
        "per_person_inr": round(total / max(1, travelers)),
        "per_day_inr": round(total / max(1, days)),
    }


def cheapest_possible(days: int, travelers: int) -> int:
    """
    The absolute floor: shoestring daily costs, no travel, no hotel.
    If somebody's budget is below this, no amount of re-planning will help
    and we should tell them straight away instead of looping forever.
    """
    daily = DAILY_FOOD["shoestring"] + DAILY_LOCAL_TRANSPORT["shoestring"] + DAILY_ACTIVITIES["shoestring"]
    return daily * days * travelers


def suggest_downgrade(current_style: str) -> str | None:
    """One step cheaper, or None if we are already at the bottom."""
    index = STYLES.index(current_style) if current_style in STYLES else 1
    return STYLES[index - 1] if index > 0 else None


def suggest_upgrade(current_style: str) -> str | None:
    """One step more comfortable, or None if we are already at the top."""
    index = STYLES.index(current_style) if current_style in STYLES else 1
    return STYLES[index + 1] if index < len(STYLES) - 1 else None


def stay_ceiling(budget_inr: int, travel_total_inr: int, days: int, travelers: int,
                 style: str, nights: int) -> int:
    """
    Working backwards: given the budget and what the travel already cost,
    what is the most we can spend per night on a hotel?

    The budget solver calls this before searching hotels, so we only ever show
    the user places they can actually afford.
    """
    daily = DAILY_FOOD[style] + DAILY_LOCAL_TRANSPORT[style] + DAILY_ACTIVITIES[style]
    on_the_ground = daily * days * travelers

    # undo the buffer: total = subtotal * (1 + b/100), so subtotal = budget / (1 + b/100)
    spendable = budget_inr / (1 + BUFFER_PERCENT / 100)
    left_for_stay = spendable - travel_total_inr - on_the_ground

    if left_for_stay <= 0:
        return 0
    return int(left_for_stay / max(1, nights))


def buffer_that_fits(subtotal_inr: int, budget_inr: int) -> float | None:
    """The largest buffer % (one decimal) that keeps the trip inside the budget,
    or None if even MIN_BUFFER_PERCENT is too much."""
    if subtotal_inr <= 0:
        return None
    pct = int((budget_inr - subtotal_inr) * 1000 / subtotal_inr) / 10.0
    pct = min(pct, BUFFER_PERCENT)
    return pct if pct >= MIN_BUFFER_PERCENT else None
