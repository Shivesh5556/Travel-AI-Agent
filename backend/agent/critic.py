"""
The critic.

Before we show the plan to anyone, a second pass looks for things that are
technically valid but actually a bad idea: a trek on the one rainy day, six
hours of driving crammed into an afternoon, a hotel on the wrong side of
the city from everything you want to see.

Two layers:

  1. Python rules (always run). Fast, free, deterministic, and testable.
     These catch the specific mistakes we know this system can make.

  2. An LLM pass (only if a Groq key is configured). Catches the vaguer
     "this feels wrong" things that rules cannot express.

Layer 1 exists because you should never depend on an LLM to catch your bugs.
Layer 2 exists because rules cannot think.
"""

import llm
from tools.geo import haversine_km

# Places where altitude is a genuine health issue, not a nice view.
HIGH_ALTITUDE = {"leh", "spiti", "khardung", "tso moriri", "nubra", "kaza"}

# Thresholds for the rule checks.
MAX_COMFORTABLE_KM_PER_DAY = 45
MAX_COMFORTABLE_HOURS = 8.5
MAX_HOTEL_DISTANCE_KM = 25


def rule_checks(plan: dict, trip_text: str, destination: str) -> list[dict]:
    """
    Run every rule. Returns a list of issues:
        [{"severity": "warning", "issue": "...", "fix": "..."}]
    An empty list means the plan passed.
    """
    issues = []
    days = plan.get("days", [])
    stay = plan.get("stay")
    budget = plan.get("budget")

    # --- weak recommendations ---
    # A polished itinerary can still be built from weak places. Say so.
    for day in days:
        stops = [st for st in day.get("stops", []) if st.get("score") is not None]
        if len(stops) >= 2 and sum(1 for st in stops if st.get("confidence") == "low") * 2 > len(stops):
            issues.append({
                "severity": "warning",
                "kind": "weak_day", "day": day["day_number"],
                "issue": (f"Day {day['day_number']} is built mostly from lower-confidence "
                          f"places - few sources or reviews back them up."),
                "fix": ("Treat that day as flexible, or add an interest (for example "
                        "Culture & Heritage or Nature) to pull in stronger sights."),
            })

    # --- rain ---
    days_with_stops = [d for d in days if d.get("stops")]
    rainy_days = [d for d in days_with_stops if d.get("is_rainy")]

    if days_with_stops and len(rainy_days) == len(days_with_stops):
        # Every single day is wet. Telling the user to "swap with a drier day"
        # four separate times is useless advice repeated four times, so say
        # the one thing that is actually true instead.
        issues.append({
            "severity": "warning",
            "kind": "rain_all_days", "for_traveller": True,
            "issue": (f"Rain is likely on all {len(rainy_days)} days of this trip. "
                      f"Moving days around will not help."),
            "fix": ("Either pick different dates, or accept it and pack for it - "
                    "carry a poncho, book refundable stays, and treat the outdoor "
                    "stops as optional."),
        })
    else:
        for day in rainy_days:
            indoor = day.get("indoor_share", 0)
            if indoor < 40:
                issues.append({
                    "severity": "warning",
                    "kind": "weather", "day": day["day_number"],
                    "issue": (f"Day {day['day_number']} ({day['date']}) is likely to rain, "
                              f"but only {indoor}% of that day's stops are indoors."),
                    "fix": "Swap this day with a museum-heavy day, or keep it flexible.",
                })

    # --- days that are simply too much ---
    for day in days:
        if day.get("total_km", 0) > MAX_COMFORTABLE_KM_PER_DAY and not day.get("day_trip"):
            issues.append({
                "severity": "warning",
                "kind": "too_far", "day": day["day_number"],
                "issue": (f"Day {day['day_number']} covers {day['total_km']}km between stops, "
                          f"which is a lot of time in traffic."),
                "fix": "Drop the furthest stop, or give that area its own day.",
            })

        if day.get("hours", 0) > MAX_COMFORTABLE_HOURS:
            issues.append({
                "severity": "warning",
                "kind": "too_packed", "day": day["day_number"],
                "issue": f"Day {day['day_number']} is about {day['hours']} hours of activity.",
                "fix": "Cut one stop. Rushed sightseeing is worse than seeing less.",
            })

    # --- empty days ---
    # build_day_plan now borrows a spare stop for any day that would
    # otherwise come out empty, so this should only ever fire when no real
    # places were found anywhere near the destination for the whole trip -
    # a data problem, not a deliberate rest day, so treat it as a warning.
    # A planned travel day (arrival after a long journey, an acclimatisation
    # day in Leh) is empty on purpose and is not a problem.
    empty = [d["day_number"] for d in days if not d.get("stops") and not d.get("travel_day")]
    if empty:
        issues.append({
            "severity": "warning",
            "kind": "too_light", "day": empty[0],
            "issue": f"Day(s) {', '.join(str(n) for n in empty)} have nothing planned.",
            "fix": "No verified places were found for this destination - widen your "
                   "interests, increase the search radius, or pick a bigger nearby "
                   "town as a base.",
        })

    # --- lunch far from the day's stops (repairable) ---
    # The agent acts on these BEFORE writing the plan up: it searches for
    # restaurants near that day's stops and swaps the lunch pick.
    for day in days:
        lunch = day.get("lunch") or {}
        if lunch.get("is_far") and day.get("stops"):
            issues.append({
                "severity": "info", "kind": "lunch_far", "day": day["day_number"],
                "issue": (f"Lunch on Day {day['day_number']} ({lunch.get('name')}) is about "
                          f"{lunch.get('distance_km', lunch.get('walk_km', 0)):.1f} km from that day's stops."),
                "fix": "Eat near the stops instead.",
                "action": {"type": "find_lunch_nearby", "day": day["day_number"]},
            })

    # --- places moved because they are closed ---
    for day in days:
        for note in day.get("notes", []) or []:
            issues.append({"severity": "info", "kind": "closed_info", "issue": note, "fix": ""})

    # --- hotel far from everything ---
    if stay and days:
        far_days = []
        for day in days:
            for stop in day.get("stops", []):
                distance = haversine_km(stay["lat"], stay["lon"], stop["lat"], stop["lon"])
                if distance > MAX_HOTEL_DISTANCE_KM:
                    far_days.append(day["day_number"])
                    break
        if len(far_days) > len(days) / 2:
            issues.append({
                "severity": "warning",
                "kind": "hotel_far", "for_traveller": True,
                "issue": f"{stay['name']} is more than {MAX_HOTEL_DISTANCE_KM}km from most of your stops.",
                "fix": "Look for a stay closer to the centre, even if it costs a little more.",
            })

    # --- budget ---
    if budget and not budget.get("fits"):
        issues.append({
            "severity": "warning",
            "kind": "over_budget", "for_traveller": True,
            "issue": f"The plan is Rs {budget['over_by_inr']:,} over the stated budget.",
            "fix": "Cut a day, travel by train, or raise the budget.",
        })

    # --- altitude ---
    if destination.strip().lower() in HIGH_ALTITUDE and len(days) < 4:
        issues.append({
            "severity": "warning",
            "kind": "altitude", "for_traveller": True,
            "issue": f"{destination.title()} is at high altitude and this trip is only {len(days)} days.",
            "fix": "Give yourself two slow days on arrival before doing anything strenuous.",
        })

    # --- overnight transport eating a day ---
    transport = plan.get("transport")
    if transport and transport.get("mode") in ("train", "bus"):
        duration = transport.get("duration", "")
        hours = _parse_hours(duration)
        if hours and hours > 10 and len(days) <= 3:
            issues.append({
                "severity": "warning",
                "kind": "long_journey", "for_traveller": True,
                "issue": (f"The {transport['carrier']} takes about {duration} each way, "
                          f"which is most of a {len(days)}-day trip."),
                "fix": "Either fly, or make it a longer trip. Two days of travel for one day there.",
            })

    return issues


def _parse_hours(duration: str) -> float | None:
    """Turn '13h 20m' into 13.33. Returns None if we cannot read it."""
    try:
        parts = duration.replace("m", "").split("h")
        return int(parts[0].strip()) + int(parts[1].strip()) / 60
    except (ValueError, IndexError):
        return None


CRITIC_PROMPT = """You are the quality checker inside a travel-planning agent. Your
findings are NOT shown to the traveller - the agent reads them and FIXES the plan
itself. So report only problems the agent can act on, each tied to a day.

Problem types you may use (pick the closest):
  travel_time  - a day visits places far from the stay and the drive is missing,
                 too long for the day, or falls on an arrival/departure day
  too_packed   - a day has too much to do for the time available
  too_light    - a day has very little to do while others are full
  weather      - outdoor stops on a day that is likely to be rainy
  order        - stops in a bad order for the time of day (sunset spot first...)
  other        - anything else practical

How to read each day line: "km" includes driving there and back; "h" is the total
time INCLUDING time at each place and the driving. A day marked "(travel: Day
trip ...)" already has its drive planned - do not report that drive as missing.
The last day ends with the journey home; that is planned too.

Do NOT report: estimated prices, safety advice, anything you cannot see in the data.

Reply with JSON only:
{"issues": [{"day": <day number or null>, "type": "<one of the types>",
             "issue": "...", "fix": "..."}]}
If the plan is fine, reply {"issues": []}. Do not invent problems."""

REVIEW_TYPES = {"travel_time", "too_packed", "too_light", "weather", "order", "other"}


async def llm_review(plan_summary: str, trip_text: str, trip_id: str | None = None) -> list[dict]:
    """The second layer. Returns [] if the LLM is not configured or errors out."""
    if not llm.config.LLM_ENABLED:
        return []

    try:
        response = await llm.ask_json(
            CRITIC_PROMPT,
            f"The traveller asked for:\n{trip_text}\n\nThe draft plan:\n{plan_summary}",
            temperature=0.2,
            label="llm_critic",
            trip_id=trip_id,
        )
    except Exception as e:
        print(f"[critic] LLM review skipped: {e}")
        return []

    issues = response.get("issues", [])

    # Trust but verify: keep only well-formed entries, cap at 4 so the UI
    # does not turn into a wall of complaints.
    clean = []
    for issue in issues[:5]:
        if isinstance(issue, dict) and issue.get("issue"):
            kind = issue.get("type") if issue.get("type") in REVIEW_TYPES else "other"
            try:
                day = int(issue["day"]) if issue.get("day") is not None else None
            except (TypeError, ValueError):
                day = None
            clean.append({
                "severity": "info",
                "kind": kind,
                "day": day,
                "issue": str(issue["issue"])[:300],
                "fix": str(issue.get("fix", ""))[:300],
                "from_llm": True,
            })
    return clean


def plan_to_text(plan: dict) -> str:
    """A compact text version of the plan, small enough to put in a prompt."""
    lines = []

    transport = plan.get("transport")
    if transport:
        lines.append(f"Travel: {transport['carrier']} ({transport['mode']}), "
                     f"{transport.get('duration', '')}, Rs {transport['total_inr']:,} each way")

    stay = plan.get("stay")
    if stay:
        lines.append(f"Stay: {stay['name']} in {stay.get('area', 'town')}, "
                     f"Rs {stay.get('group_price_per_night_inr', stay['price_per_night_inr']):,} "
                     f"per night for the group")

    understood = (plan.get("preferences") or {}).get("understood") or []
    if understood:
        lines.append("Traveller constraints already applied: " + "; ".join(understood))

    for day in plan.get("days", []):
        weather_note = " (rain likely)" if day.get("is_rainy") else ""
        stop_names = ", ".join(s["name"] for s in day.get("stops", [])) or "nothing planned"
        travel = f" (travel: {day['travel_note']})" if day.get("travel_note") else ""
        lines.append(f"Day {day['day_number']} {day['date']}{weather_note}{travel}: {stop_names} "
                     f"[{day.get('total_km', 0)}km including the way back to the stay, about "
                     f"{day.get('hours', 0)}h "
                     f"in total including time at each place]")

    budget = plan.get("budget")
    if budget:
        lines.append(f"Total: Rs {budget['total_inr']:,} against a budget of Rs {budget['budget_inr']:,}")

    return "\n".join(lines)


# Topics the Python rules already cover. If the model raises one of these too,
# it is repeating us.
_COVERED_TOPICS = {
    "rain": ("rain", "wet", "monsoon", "shower"),
    "budget": ("budget", "over by", "expensive", "afford"),
    "packed": ("packed", "too many", "rushed", "tight schedule"),
    "empty": ("empty", "nothing planned", "free day"),
    "altitude": ("altitude", "acclimat"),
}


def _topics(text: str) -> set[str]:
    lowered = text.lower()
    return {topic for topic, words in _COVERED_TOPICS.items()
            if any(word in lowered for word in words)}


def drop_duplicates(rule_issues: list[dict], llm_issues: list[dict]) -> list[dict]:
    """
    Keep only the model's issues that say something new.

    The rules and the model both look at the same plan, so they overlap a lot.
    Whatever the rules already flagged, we trust the rules version - it is
    specific ("Day 3 is 0% indoors") where the model is vague ("consider the
    weather").
    """
    already = set()
    for issue in rule_issues:
        already |= _topics(issue.get("issue", "") + " " + issue.get("fix", ""))

    fresh = []
    for issue in llm_issues:
        mine = _topics(issue.get("issue", "") + " " + issue.get("fix", ""))
        if mine and mine <= already:
            continue          # says nothing the rules did not already say
        fresh.append(issue)

    return fresh
