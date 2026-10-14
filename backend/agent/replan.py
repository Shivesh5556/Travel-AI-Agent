"""
The budget solver. This is the most interesting file in the project.

MakeMyTrip gives you a search box and lets you find out at the end that you
cannot afford it. We treat the budget as a hard constraint and re-plan until
the trip fits, exactly like a human would:

    total is Rs 4,200 over
      -> try a cheaper hotel
    still Rs 1,900 over
      -> eat and travel around town more cheaply
    still Rs 600 over
      -> take the overnight train instead of flying, saves Rs 6,800
    fits, with Rs 6,200 left over

Every one of those rounds is shown live in the UI. That loop is what people
mean by "agentic" - the system notices its own plan is wrong and fixes it.

Note that this file is pure Python with no network calls and no LLM. It takes
lists of options in and gives a decision out, which means we can unit test it
properly (see tests/test_replan.py).
"""

import recommend
from tools import budget as budget_tool

MAX_ROUNDS = 5

# If at least this share of the budget is left over after fitting, the agent
# spends some of it (a better daily spending level) instead of leaving it idle.
UPGRADE_HEADROOM = 0.12


def _pick_stay(stay_options: list[dict], ceiling: int | None,
               travel_style: str = "balanced",
               anchor: tuple[float, float] | None = None) -> dict | None:
    """
    Choose the BEST place to stay that the budget allows - not the cheapest and
    not merely the most stars.

    ceiling is the most the budget leaves per night:
      None  no budget pressure known -> best overall
      0     the budget has nothing left for a bed -> the cheapest, honestly
      >0    best recommendation score among the affordable options

    "Best" is recommend.score_stay: budget fit, quality, distance to where the
    good attractions are, evidence, and fit with the travel style.
    """
    if not stay_options:
        return None

    if ceiling is not None and ceiling <= 0:
        return min(stay_options, key=recommend.group_price)

    ranked = recommend.rank_stays(stay_options, ceiling, travel_style, anchor)

    if ceiling is not None:
        affordable = [s for s in ranked if recommend.group_price(s) <= ceiling]
        if affordable:
            return affordable[0]
        # Nothing fits: take the cheapest and let the budget report the overshoot.
        return min(stay_options, key=recommend.group_price)

    return ranked[0]


def _cheaper_transport(current: dict, options: list[dict]) -> dict | None:
    """The next option down the price list, or None if we are already cheapest."""
    cheaper = [o for o in options if o["total_inr"] < current["total_inr"]]
    if not cheaper:
        return None
    return max(cheaper, key=lambda o: o["total_inr"])   # the closest one below


# Where each travel style starts before any re-planning happens.
START_STYLE = {
    "budget": "shoestring",
    "balanced": "normal",
    "comfort": "comfort",
}


def _preferred_transport(options: list[dict], travel_style: str) -> dict | None:
    """
    What we try FIRST, before we know whether it fits.

    This is where the traveller's preference actually bites:

      budget    start with the cheapest thing that moves, usually a bus or
                sleeper train. Do not even try the flight.
      balanced  start with the cheapest flight if there is one. Most people
                would rather fly and will drop to a train if they must.
      comfort   start with a flight, and prefer a non-stop over a marginally
                cheaper one with a connection. Somebody who said "comfort"
                does not want a 2am layover to save Rs 400.
    """
    if not options:
        return None

    # Sort here rather than trusting the caller. Relying on "the list arrives
    # cheapest first" is the kind of assumption that silently breaks the day
    # somebody adds a new transport source.
    by_price = sorted(options, key=lambda o: o["total_inr"])
    flights = [o for o in by_price if o["mode"] == "flight"]

    if travel_style == "budget" or not flights:
        return by_price[0]

    if travel_style == "comfort":
        direct = [f for f in flights if "non-stop" in str(f.get("stops", "")).lower()]
        if direct:
            return direct[0]

    return flights[0]


def solve(days: int, nights: int, travelers: int, budget_inr: int,
          transport_options: list[dict], stay_options: list[dict],
          round_trip: bool = True, travel_style: str = "balanced",
          anchor: tuple[float, float] | None = None) -> dict:
    """
    Find a combination that fits the budget.

    Returns everything, including the failed attempts, because showing your
    working is the whole point.
    """
    attempts: list[dict] = []

    # Sanity check first: is this budget possible at all? If they want 5 days
    # for Rs 2,000 there is no plan, and saying so immediately is more useful
    # than five rounds of pretending.
    floor = budget_tool.cheapest_possible(days, travelers)
    if budget_inr < floor:
        return {
            "fits": False,
            "impossible": True,
            "reason": (f"Even at the most basic level, {days} days for {travelers} "
                       f"traveller(s) costs about Rs {floor:,} in food and local "
                       f"transport alone, before you get there or sleep anywhere."),
            "minimum_needed_inr": floor,
            "transport": None,
            "stay": None,
            "budget": None,
            "attempts": [],
            "rounds": 0,
        }

    transport = _preferred_transport(transport_options, travel_style)
    if transport is None:
        return {"fits": False, "impossible": True,
                "reason": "No way to get to this destination was found.",
                "transport": None, "stay": None, "budget": None,
                "attempts": [], "rounds": 0, "minimum_needed_inr": floor}

    style = START_STYLE.get(travel_style, "normal")
    stay = None
    result = None

    # Moves we are allowed to make, tried in this order. Cheaper hotel first
    # because it annoys the traveller least; changing how you travel is the
    # biggest change so it is the last resort before giving up on style.
    for round_number in range(MAX_ROUNDS):
        travel_total = transport["total_inr"] * (2 if round_trip else 1)

        ceiling = budget_tool.stay_ceiling(
            budget_inr, travel_total, days, travelers, style, nights)
        # NB: a ceiling of 0 means "no money left for a bed" and must reach
        # _pick_stay as 0 (cheapest), not be mistaken for "no limit".
        stay = _pick_stay(stay_options, ceiling, travel_style, anchor)

        # what the whole group pays (rooms x rate, beds x rate for a hostel)
        stay_total = recommend.group_price(stay) * nights if stay else 0

        result = budget_tool.compute(
            travel_total_inr=travel_total,
            stay_total_inr=stay_total,
            days=days,
            travelers=travelers,
            budget_inr=budget_inr,
            style=style,
        )

        attempts.append({
            "round": round_number + 1,
            "transport": f"{transport['carrier']} ({transport['mode']})",
            "transport_inr": travel_total,
            "stay": stay["name"] if stay else "none",
            "stay_inr": stay_total,
            "style": style,
            "total_inr": result["total_inr"],
            "fits": result["fits"],
            "over_by_inr": result["over_by_inr"],
            "change_made": None,
        })

        if result["fits"]:
            break

        # --- a small overshoot: trim OUR safety buffer, not their spending ---
        # v26 cut daily spending from "normal" to "shoestring" (half the food
        # budget) to save Rs 552 on a Rs 25,000 trip. The 8% buffer is our own
        # padding; using part of it (never below 4%) is the gentler fix.
        trimmed = budget_tool.buffer_that_fits(result["subtotal_inr"], budget_inr)
        if trimmed is not None:
            attempts[-1]["change_made"] = (
                f"Over by Rs {result['over_by_inr']:,}. Trimming our own safety buffer from "
                f"{budget_tool.BUFFER_PERCENT}% to {trimmed:g}% instead of cutting your "
                f"daily spending.")
            result = budget_tool.compute(
                travel_total_inr=travel_total, stay_total_inr=stay_total, days=days,
                travelers=travelers, budget_inr=budget_inr, style=style, buffer_percent=trimmed)
            attempts.append({
                "round": round_number + 2,
                "transport": f"{transport['carrier']} ({transport['mode']})",
                "transport_inr": travel_total,
                "stay": stay["name"] if stay else "none",
                "stay_inr": stay_total,
                "style": style,
                "total_inr": result["total_inr"],
                "fits": result["fits"],
                "over_by_inr": result["over_by_inr"],
                "change_made": None,
            })
            break

        # --- it does not fit, so decide what to change for the next round ---

        cheaper_style = budget_tool.suggest_downgrade(style)
        cheaper_ride = _cheaper_transport(transport, transport_options)

        # Order of sacrifice depends on what they asked for. Somebody who
        # picked "comfort" would rather eat more cheaply than lose the
        # flight; somebody on "budget" already took the cheapest ride, so
        # there is nothing to give up there anyway.
        if travel_style == "budget" and cheaper_ride is not None:
            saved = (transport["total_inr"] - cheaper_ride["total_inr"]) * (2 if round_trip else 1)
            attempts[-1]["change_made"] = (
                f"Over by Rs {result['over_by_inr']:,}. Switching from "
                f"{transport['carrier']} to {cheaper_ride['carrier']}, saving Rs {saved:,}.")
            transport = cheaper_ride

        elif cheaper_style is not None:
            attempts[-1]["change_made"] = (
                f"Over by Rs {result['over_by_inr']:,}. Dropping daily spending "
                f"from {style} to {cheaper_style} and looking for a cheaper stay.")
            style = cheaper_style

        elif cheaper_ride is not None:
            saved = (transport["total_inr"] - cheaper_ride["total_inr"]) * (2 if round_trip else 1)
            attempts[-1]["change_made"] = (
                f"Over by Rs {result['over_by_inr']:,}. Switching from "
                f"{transport['carrier']} to {cheaper_ride['carrier']}, saving Rs {saved:,}.")
            transport = cheaper_ride

        else:
            attempts[-1]["change_made"] = (
                f"Nothing cheaper left to try. Still Rs {result['over_by_inr']:,} over.")
            break

    # --- the other direction: spend the money that is left ---
    # Fitting the budget is the hard rule, but a plan that finishes Rs 30,000
    # under budget has wasted the traveller's money. If there is real headroom
    # and they did not ask for the cheapest trip, move daily spending up a
    # level for as long as the trip still fits. A "budget" traveller keeps
    # the saving - that is what they asked for.
    if result and result["fits"] and travel_style != "budget":
        spare_floor = max(1, int(budget_inr * UPGRADE_HEADROOM))
        while result["left_over_inr"] >= spare_floor:
            better = budget_tool.suggest_upgrade(style)
            if better is None:
                break
            trial = budget_tool.compute(
                travel_total_inr=travel_total, stay_total_inr=stay_total, days=days,
                travelers=travelers, budget_inr=budget_inr, style=better)
            if not trial["fits"]:
                break
            attempts[-1]["change_made"] = (
                f"Rs {result['left_over_inr']:,} still unspent, so raising daily "
                f"spending from {style} to {better} - it still fits.")
            attempts.append({
                "round": len(attempts) + 1,
                "transport": f"{transport['carrier']} ({transport['mode']})",
                "transport_inr": travel_total,
                "stay": stay["name"] if stay else "none",
                "stay_inr": stay_total,
                "style": better,
                "total_inr": trial["total_inr"],
                "fits": True,
                "over_by_inr": 0,
                "change_made": None,
                "upgrade": True,
            })
            style, result = better, trial

    return {
        "fits": result["fits"] if result else False,
        "impossible": False,
        "travel_style": travel_style,
        "reason": "" if (result and result["fits"]) else _explain_failure(result, budget_inr),
        "transport": transport,
        "stay": stay,
        "budget": result,
        "attempts": attempts,
        "rounds": len([a for a in attempts if not a.get("upgrade")]),
        "upgrade_rounds": len([a for a in attempts if a.get("upgrade")]),
        "minimum_needed_inr": floor,
    }


def _explain_failure(result: dict | None, budget_inr: int) -> str:
    """A useful message, not just 'failed'."""
    if result is None:
        return "Could not build a plan for this trip."

    over = result["over_by_inr"]
    suggested = budget_inr + over
    return (f"The cheapest realistic version of this trip still comes to "
            f"Rs {result['total_inr']:,}, which is Rs {over:,} over your budget. "
            f"Either raise the budget to about Rs {suggested:,}, cut a day, "
            f"or pick somewhere closer.")


def savings_summary(attempts: list[dict]) -> str:
    """'Started at Rs 42,000 and got it down to Rs 24,800' - nice line for the UI."""
    attempts = [a for a in attempts if not a.get("upgrade")]
    if len(attempts) < 2:
        return ""
    first = attempts[0]["total_inr"]
    last = attempts[-1]["total_inr"]
    if last >= first:
        return ""
    return (f"Re-planning brought the trip from Rs {first:,} down to "
            f"Rs {last:,}, saving Rs {first - last:,}.")


def stay_alternatives(stay_options: list[dict], chosen: dict | None, *, budget_inr: int,
                      transport_total: int, days: int, nights: int, travelers: int,
                      style: str, travel_style: str,
                      anchor: tuple[float, float] | None = None, top: int = 4) -> dict:
    """
    What the traveller can choose INSTEAD of our pick, all scored for them:

      picks    the best few stays within the nightly ceiling, each with its
               price difference over the whole trip versus our pick
      stretch  the best-scoring stay that costs more than the budget allows,
               with the honest price of stretching ("Rs 3,200 over") - the
               budget stays the rule, but the traveller sees what a little
               more money would buy

    Pure function: no network, easy to test.
    """
    ceiling = budget_tool.stay_ceiling(budget_inr, transport_total, days, travelers, style, nights)
    ranked = recommend.rank_stays(stay_options, ceiling, travel_style, anchor)
    chosen_price = recommend.group_price(chosen) if chosen else 0
    chosen_name = chosen["name"] if chosen else None

    def _decorate(stay: dict) -> dict:
        item = dict(stay)
        item["delta_total_inr"] = (recommend.group_price(stay) - chosen_price) * nights
        item["is_pick"] = stay["name"] == chosen_name
        return item

    affordable = [s for s in ranked if recommend.group_price(s) <= max(ceiling, 0)] \
        if ceiling > 0 else []
    picks = [_decorate(s) for s in (affordable or ranked[:1])][:top]

    stretch = None
    limit = max(ceiling, chosen_price)
    over = [s for s in ranked if limit < recommend.group_price(s) <= limit * 1.6]
    if over and chosen:
        # Judge them as if the money were there, so the genuinely better bed wins.
        best = max(recommend.rank_stays(over, None, travel_style, anchor),
                   key=lambda s: s["score"])
        if best["score"] > chosen.get("score", 0):
            stretch = {"stay": _decorate(best),
                       "extra_total_inr": (recommend.group_price(best) - chosen_price) * nights}
    return {"ceiling_per_night_inr": ceiling, "picks": picks, "stretch": stretch}
