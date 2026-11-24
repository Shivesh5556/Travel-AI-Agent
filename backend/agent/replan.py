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
          anchor: tuple[float, float] | None = None,
          city: str | None = None) -> dict:
    """Evaluate every supplied combination before declaring a trip unaffordable.

    `city` makes food, local transport and activities cost what they cost in
    that destination (tools/budget.daily_rates). It is passed to EVERY money
    function below - the floor, the hotel ceiling and the totals - so they all
    use the same rates. Leave it out for national averages.

    The search is finite (rides x stays x three spending levels). Displayed
    attempts describe the initial choice and the selected result, rather than
    limiting the actual feasibility search to an arbitrary number of rounds.
    """
    floor = budget_tool.cheapest_possible(days, travelers, city)
    full_buffer = budget_tool.full_buffer_percent(city)
    empty = {"fits": False, "impossible": True, "travel_style": travel_style,
             "transport": None, "stay": None, "budget": None, "attempts": [],
             "rounds": 0, "upgrade_rounds": 0, "minimum_needed_inr": floor}
    if budget_inr < floor:
        return {**empty, "reason": (
            f"Even at the most basic level, {days} days for {travelers} "
            f"traveller(s) costs about Rs {floor:,} in food, local transport "
            "and entry tickets alone, before you get there or sleep anywhere.")}
    if not transport_options:
        return {**empty, "reason": "No way to get to this destination was found."}
    if nights > 0 and not stay_options:
        return {**empty, "reason": "No stay was found; accommodation costs cannot be verified."}

    initial_ride = _preferred_transport(transport_options, travel_style)
    initial_style = START_STYLE.get(travel_style, "normal")
    factor = 2 if round_trip else 1

    def evaluate(ride, stay, style, buffer_percent=None):
        return budget_tool.compute(
            travel_total_inr=ride["total_inr"] * factor,
            stay_total_inr=recommend.group_price(stay) * nights if stay else 0,
            days=days, travelers=travelers, budget_inr=budget_inr,
            style=style, buffer_percent=buffer_percent, city=city)

    def attempt(ride, stay, result, number, **extra):
        return {"round": number, "transport": f"{ride['carrier']} ({ride['mode']})",
                "transport_inr": ride["total_inr"] * factor,
                "stay": stay["name"] if stay else "none",
                "stay_inr": recommend.group_price(stay) * nights if stay else 0,
                "style": result["style"], "total_inr": result["total_inr"],
                "fits": result["fits"], "over_by_inr": result["over_by_inr"],
                "change_made": None, **extra}

    ceiling = budget_tool.stay_ceiling(
        budget_inr, initial_ride["total_inr"] * factor, days, travelers, initial_style, nights,
        city)
    initial_stay = _pick_stay(stay_options, ceiling, travel_style, anchor)
    initial_result = evaluate(initial_ride, initial_stay, initial_style)
    attempts = [attempt(initial_ride, initial_stay, initial_result, 1)]

    # Keep the requested ride before sacrificing daily spending. Comfort
    # prefers direct flights, balanced prefers the cheapest flight, and budget
    # starts with the cheapest ride. Every cheaper alternative is still checked.
    def ride_key(ride):
        price = ride["total_inr"]
        if travel_style == "budget":
            return (0, price)
        if ride.get("mode") == "flight":
            direct = "non-stop" in str(ride.get("stops", "")).lower()
            return (0 if travel_style != "comfort" or direct else 1, price)
        return (2, price)

    styles = budget_tool.STYLES[:budget_tool.STYLES.index(initial_style) + 1][::-1]
    candidates = []
    minimum = None
    for ride in sorted(transport_options, key=ride_key):
        for style_rank, style in enumerate(styles):
            ceiling = budget_tool.stay_ceiling(
                budget_inr, ride["total_inr"] * factor, days, travelers, style, nights, city)
            ranked = recommend.rank_stays(stay_options, ceiling, travel_style, anchor)
            for stay in ranked or [None]:
                result = evaluate(ride, stay, style)
                lowest = evaluate(ride, stay, style, budget_tool.MIN_BUFFER_PERCENT)
                item = (ride, stay, lowest)
                if minimum is None or lowest["total_inr"] < minimum[2]["total_inr"]:
                    minimum = item
                if not result["fits"]:
                    trimmed = budget_tool.buffer_that_fits(
                        result["subtotal_inr"], budget_inr, full_buffer)
                    if trimmed is not None:
                        result = evaluate(ride, stay, style, trimmed)
                if result["fits"]:
                    # Prefer the full safety buffer before a marginal stay
                    # upgrade; score stays against the same spending context.
                    quality = stay.get("score", 0) if stay else 0
                    key = (ride_key(ride), style_rank,
                           result["buffer_percent"] < full_buffer,
                           -quality, result["total_inr"])
                    candidates.append((key, ride, stay, result))

    if candidates:
        _, transport, stay, result = min(candidates, key=lambda c: c[0])
    else:
        transport, stay, result = minimum

    if (transport != initial_ride or stay != initial_stay or result != initial_result):
        trimmed_only = (transport == initial_ride and
                        (recommend.group_price(stay) if stay else 0) ==
                        (recommend.group_price(initial_stay) if initial_stay else 0) and
                        result["style"] == initial_style and
                        result["buffer_percent"] < full_buffer)
        attempts[0]["change_made"] = (
            f"Trimming our own safety buffer from {full_buffer:g}% to "
            f"{result['buffer_percent']:g}% instead of cutting your daily spending."
            if trimmed_only else
            "Compared all available travel, stay and daily-spending combinations; "
            "selected the best affordable combination." if result["fits"] else
            "Compared all combinations; this is the lowest available total.")
        attempts.append(attempt(transport, stay, result, 2))

    # Preserve the existing opt-in-by-style upgrade behavior: budget travellers
    # retain savings; other styles can improve daily spending with ample room.
    if result["fits"] and travel_style != "budget":
        spare_floor = max(1, int(budget_inr * UPGRADE_HEADROOM))
        while result["left_over_inr"] >= spare_floor:
            better = budget_tool.suggest_upgrade(result["style"])
            if better is None:
                break
            trial = evaluate(transport, stay, better)
            if not trial["fits"]:
                break
            attempts[-1]["change_made"] = (
                f"Rs {result['left_over_inr']:,} still unspent, so raising daily "
                f"spending from {result['style']} to {better} - it still fits.")
            result = trial
            attempts.append(attempt(transport, stay, result, len(attempts) + 1, upgrade=True))

    return {"fits": result["fits"], "impossible": False, "travel_style": travel_style,
            "reason": "" if result["fits"] else _explain_failure(result, budget_inr),
            "transport": transport, "stay": stay, "budget": result, "attempts": attempts,
            "rounds": len([a for a in attempts if not a.get("upgrade")]),
            "upgrade_rounds": len([a for a in attempts if a.get("upgrade")]),
            "minimum_needed_inr": minimum[2]["total_inr"]}


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
                      anchor: tuple[float, float] | None = None, top: int = 4,
                      city: str | None = None) -> dict:
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
    ceiling = budget_tool.stay_ceiling(budget_inr, transport_total, days, travelers, style,
                                       nights, city)
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
