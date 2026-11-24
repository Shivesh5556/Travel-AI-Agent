"""
All the money maths.

Rule we follow in this project: an LLM never does arithmetic. It writes
sentences, we do the sums. Every number on the screen comes from this file,
which means it is testable (see tests/test_replan.py and
tests/test_city_costs.py) and it cannot hallucinate.

v31: daily costs now depend on the destination. Mumbai is no longer priced
like Varanasi. The city price level comes from tools/city_prices.py, the same
index the hotel estimates use, so the hotel ceiling, the solver and the final
bill all share one set of assumptions. Every function takes an optional
`city`; leave it out and you get exactly the old national-average behaviour.
"""

from tools import city_prices

# Daily spend per person, in rupees, at three comfort levels, for an AVERAGE
# Indian city. A destination scales these up or down (see daily_rates).
DAILY_FOOD = {"shoestring": 450, "normal": 900, "comfort": 1800}
DAILY_LOCAL_TRANSPORT = {"shoestring": 150, "normal": 350, "comfort": 900}
DAILY_ACTIVITIES = {"shoestring": 200, "normal": 500, "comfort": 1200}

STYLES = ["shoestring", "normal", "comfort"]

# Always keep a bit aside. Something always costs more than you planned.
BUFFER_PERCENT = 8
# When we know nothing about the city, our daily numbers are a rougher guess,
# so we keep a bigger cushion. Honest uncertainty, in rupees.
UNKNOWN_CITY_BUFFER_PERCENT = 10
# With researched, dated figures for the city we can afford a smaller cushion.
RESEARCHED_BUFFER_PERCENT = 6
# The solver may trim our OWN safety buffer down to this before it cuts what
# the traveller spends on food and activities (see agent/replan.py).
MIN_BUFFER_PERCENT = 4

_BASE = {"food": DAILY_FOOD, "local_transport": DAILY_LOCAL_TRANSPORT,
         "activities": DAILY_ACTIVITIES}


def _round10(value: float) -> int:
    return int(round(value / 10) * 10)


def daily_rates(city: str | None = None) -> dict:
    """
    Per-person daily costs for ALL three styles in this city, plus where the
    numbers came from and how much to trust them.

        {
          "city": "mumbai",
          "rates": {"normal": {"food": 1260, "local_transport": 460,
                               "activities": 620}, ...},
          "basis": "city_index" | "researched" | "national_average",
          "confidence": "high" | "medium" | "low",
          "buffer_percent": 8,
          "multipliers": {"food": 1.4, ...},
          "note": "a sentence the UI can show",
        }

    Built fresh on every call (it is a handful of multiplications), so callers
    can never change each other's numbers by accident.
    """
    key = city_prices.normalise_city(city)
    researched = city_prices.RESEARCHED_DAILY.get(key) if key else None

    if researched:
        rates = {style: {item: int(researched[item][style]) for item in _BASE}
                 for style in STYLES}
        return {"city": key, "rates": rates, "basis": "researched",
                "confidence": "high", "buffer_percent": RESEARCHED_BUFFER_PERCENT,
                "multipliers": None,
                "source": researched.get("source", ""),
                "checked": researched.get("checked", ""),
                "note": (f"Daily costs use researched prices for {key.title()} "
                         f"(checked {researched.get('checked', 'recently')}).")}

    if key and city_prices.is_known(key):
        mult = city_prices.daily_multipliers(key)
        rates = {style: {item: _round10(_BASE[item][style] * mult[item]) for item in _BASE}
                 for style in STYLES}
        return {"city": key, "rates": rates, "basis": "city_index",
                "confidence": "medium", "buffer_percent": BUFFER_PERCENT,
                "multipliers": mult,
                "note": (f"Daily costs adjusted to {key.title()}'s price level "
                         f"(food x{mult['food']:g}, local travel "
                         f"x{mult['local_transport']:g}, activities "
                         f"x{mult['activities']:g} the national average). Estimates.")}

    rates = {style: {item: _BASE[item][style] for item in _BASE} for style in STYLES}
    if not key:
        # No city given: the pre-v31 behaviour, kept so old callers and old
        # tests see exactly the same numbers.
        return {"city": None, "rates": rates, "basis": "national_average",
                "confidence": "low", "buffer_percent": BUFFER_PERCENT,
                "multipliers": None,
                "note": "Daily costs use national averages."}
    return {"city": key, "rates": rates, "basis": "national_average",
            "confidence": "low", "buffer_percent": UNKNOWN_CITY_BUFFER_PERCENT,
            "multipliers": None,
            "note": (f"We have no price data for {key.title()} yet, so daily costs "
                     f"use national averages with a bigger "
                     f"{UNKNOWN_CITY_BUFFER_PERCENT}% safety buffer.")}


def full_buffer_percent(city: str | None = None) -> float:
    """The normal (untrimmed) safety buffer for this city."""
    return daily_rates(city)["buffer_percent"]


def per_day(style: str, city: str | None = None) -> int:
    """Food + local transport + activities for ONE person for ONE day."""
    style = style if style in STYLES else "normal"
    rate = daily_rates(city)["rates"][style]
    return rate["food"] + rate["local_transport"] + rate["activities"]


def compute(travel_total_inr: int, stay_total_inr: int, days: int, travelers: int,
            budget_inr: int, style: str = "normal", buffer_percent: float | None = None,
            city: str | None = None) -> dict:
    """
    Add everything up and say whether it fits.

    Returns a dict with every line item, the total, and how far over or under
    budget we are. The agent uses "over_by_inr" to decide whether to re-plan.
    """
    style = style if style in STYLES else "normal"
    info = daily_rates(city)
    rate = info["rates"][style]

    food = rate["food"] * days * travelers
    local = rate["local_transport"] * days * travelers
    activities = rate["activities"] * days * travelers

    subtotal = travel_total_inr + stay_total_inr + food + local + activities
    pct = info["buffer_percent"] if buffer_percent is None else buffer_percent
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
        # Where the daily numbers came from, so the UI can be honest about it.
        "daily_basis": {
            "city": info["city"],
            "basis": info["basis"],
            "confidence": info["confidence"],
            "per_person_per_day_inr": dict(rate),
            "note": info["note"],
        },
    }


def cheapest_possible(days: int, travelers: int, city: str | None = None) -> int:
    """
    The absolute floor: shoestring daily costs, no travel, no hotel.
    If somebody's budget is below this, no amount of re-planning will help
    and we should tell them straight away instead of looping forever.
    """
    return per_day("shoestring", city) * days * travelers


def suggest_downgrade(current_style: str) -> str | None:
    """One step cheaper, or None if we are already at the bottom."""
    index = STYLES.index(current_style) if current_style in STYLES else 1
    return STYLES[index - 1] if index > 0 else None


def suggest_upgrade(current_style: str) -> str | None:
    """One step more comfortable, or None if we are already at the top."""
    index = STYLES.index(current_style) if current_style in STYLES else 1
    return STYLES[index + 1] if index < len(STYLES) - 1 else None


def stay_ceiling(budget_inr: int, travel_total_inr: int, days: int, travelers: int,
                 style: str, nights: int, city: str | None = None) -> int:
    """
    Working backwards: given the budget and what the travel already cost,
    what is the most we can spend per night on a hotel?

    The budget solver calls this before searching hotels, so we only ever show
    the user places they can actually afford. It uses the SAME city rates and
    the SAME buffer as compute(), otherwise a hotel at the ceiling would not fit.
    """
    on_the_ground = per_day(style, city) * days * travelers

    # undo the buffer: total = subtotal * (1 + b/100), so subtotal = budget / (1 + b/100)
    spendable = budget_inr / (1 + full_buffer_percent(city) / 100)
    left_for_stay = spendable - travel_total_inr - on_the_ground

    if left_for_stay <= 0:
        return 0
    return int(left_for_stay / max(1, nights))


def buffer_that_fits(subtotal_inr: int, budget_inr: int,
                     max_percent: float = BUFFER_PERCENT) -> float | None:
    """The largest buffer % (one decimal, at most max_percent) that keeps the
    trip inside the budget, or None if even MIN_BUFFER_PERCENT is too much."""
    if subtotal_inr <= 0:
        return None
    pct = int((budget_inr - subtotal_inr) * 1000 / subtotal_inr) / 10.0
    pct = min(pct, max_percent)
    return pct if pct >= MIN_BUFFER_PERCENT else None
