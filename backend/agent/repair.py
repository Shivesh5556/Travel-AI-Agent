"""
The agent fixing its own plan.

v26 showed review findings to the traveller ("Day 5 lists Mussoorie attractions
but does not include travel time...", flagged by the review pass). That is the
agent admitting a mistake and leaving the user to deal with it. Now every
finding - from the Python rules or the LLM reviewer - is mapped to an ACTION
the agent takes on the plan itself:

    travel_time  a long drive on an arrival/departure day -> swap that day with
                 a middle day; a day too long with its drive -> drop its
                 weakest stop
    too_packed   drop the weakest non-must-see stop (it becomes an idea)
    too_far      same as too_packed
    too_light    add the best unused place nearby that is open that day
    weather      swap a rainy outdoor day with a dry day that has more indoor
                 stops
    order        re-order the day (morning sights first, sunset last)
    other        recorded, nothing changed

The traveller only ever sees the fixed plan. What was found and done is kept in
plan["agent_review"] (and printed in the server log) for debugging and the viva.

Pure Python, no network: every action is unit-tested (tests/test_v27_agent.py).
"""

import dayplanner
from tools.geo import haversine_km


def _day(plan: dict, number: int | None) -> dict | None:
    if number is None:
        return None
    return next((d for d in plan.get("days", []) if d.get("day_number") == number), None)


def _open_on(stops: list[dict], date_iso: str) -> bool:
    return not any(dayplanner._closed_on(s, date_iso) for s in stops)


def _is_edge_day(plan: dict, day: dict) -> bool:
    days = plan.get("days", [])
    return day is days[0] or day is days[-1] or \
        day.get("available_hours", dayplanner.MAX_HOURS_PER_DAY) < dayplanner.MAX_HOURS_PER_DAY


def _swap_stops(a: dict, b: dict) -> None:
    """Swap two days' programmes - the lunch spot travels with its stops."""
    a["stops"], b["stops"] = b["stops"], a["stops"]
    a["extra_ideas"], b["extra_ideas"] = b.get("extra_ideas", []), a.get("extra_ideas", [])
    a["lunch"], b["lunch"] = b.get("lunch"), a.get("lunch")


class Fixer:
    def __init__(self, plan: dict, pool: list[dict], restaurants: list[dict],
                 hotel: tuple[float, float]):
        self.plan = plan
        self.pool = pool or []
        self.restaurants = restaurants or []
        self.hotel = hotel
        self.changed: set[int] = set()

    # -- helpers ---------------------------------------------------------------
    def refresh(self, day: dict) -> None:
        dayplanner.refresh_day(day, *self.hotel)
        self.changed.add(day["day_number"])

    def used_names(self) -> set[str]:
        return {s["name"] for d in self.plan.get("days", []) for s in d.get("stops", [])}

    def drop_weakest(self, day: dict) -> str | None:
        stops = day.get("stops", [])
        if len(stops) <= 1:
            return None
        candidates = [s for s in stops if not s.get("must_see")] or stops
        weakest = min(candidates, key=lambda s: s.get("score", 0))
        day["stops"] = [s for s in stops if s is not weakest]
        day.setdefault("extra_ideas", []).insert(0, weakest["name"])
        self.refresh(day)
        return weakest["name"]

    # -- actions ---------------------------------------------------------------
    def travel_time(self, day: dict) -> str:
        days = self.plan.get("days", [])
        if day.get("day_trip") and _is_edge_day(self.plan, day):
            middle = [d for d in days if not _is_edge_day(self.plan, d) and not d.get("day_trip")
                      and d.get("stops")]
            for other in middle:
                if _open_on(day["stops"], other["date"]) and _open_on(other["stops"], day["date"]):
                    _swap_stops(day, other)
                    self.refresh(day)
                    self.refresh(other)
                    return (f"moved the day trip from Day {day['day_number']} to Day "
                            f"{other['day_number']} (not an arrival/departure day)")
        dropped = []
        while day.get("hours", 0) > day.get("available_hours", 7) + 0.5:
            name = self.drop_weakest(day)
            if not name:
                break
            dropped.append(name)
        if dropped:
            return f"dropped {', '.join(dropped)} so the drive fits the day"
        if not day.get("day_trip"):
            self.refresh(day)              # recompute with road distances
            return "recomputed the day's driving time"
        return "drive was already planned (day-trip note on the day)"

    def too_packed(self, day: dict) -> str:
        limit = day.get("available_hours", dayplanner.MAX_HOURS_PER_DAY)
        dropped = []
        if day.get("hours", 0) <= limit:
            name = self.drop_weakest(day)           # the reviewer says it feels rushed
            dropped += [name] if name else []
        while day.get("hours", 0) > limit + 0.5:
            name = self.drop_weakest(day)
            if not name:
                break
            dropped.append(name)
        return f"dropped {', '.join(dropped)}" if dropped else "nothing safe to drop"

    def too_light(self, day: dict) -> str:
        if day.get("travel_day"):
            return "planned travel day - left light on purpose"
        used = self.used_names()
        centre = dayplanner._centre(day.get("stops", [])) or self.hotel
        room = day.get("available_hours", 7) - day.get("hours", 0)
        options = [p for p in self.pool if p["name"] not in used
                   and not dayplanner._closed_on(p, day["date"])
                   and p.get("visit_hours", 1.5) <= room + 0.25
                   and haversine_km(p["lat"], p["lon"], centre[0], centre[1]) <= dayplanner.MAX_MOVE_KM
                   and dayplanner._same_kind_of_day(p, day.get("stops", []), self.hotel)]
        if not options:
            return "no suitable unused place nearby"
        best = max(options, key=lambda p: p.get("score", 0))
        day["stops"] = day.get("stops", []) + [best]
        day["extra_ideas"] = [n for n in day.get("extra_ideas", []) if n != best["name"]]
        self.refresh(day)
        return f"added {best['name']}"

    def weather(self, day: dict) -> str:
        if not day.get("is_rainy"):
            return "day is not rainy"
        days = self.plan.get("days", [])
        for other in sorted(days, key=lambda d: -d.get("indoor_share", 0)):
            if other is day or other.get("is_rainy") or not other.get("stops"):
                continue
            if other.get("indoor_share", 0) <= day.get("indoor_share", 0):
                continue
            if bool(other.get("day_trip")) and _is_edge_day(self.plan, day):
                continue
            if _open_on(day["stops"], other["date"]) and _open_on(other["stops"], day["date"]):
                _swap_stops(day, other)
                self.refresh(day)
                self.refresh(other)
                return f"swapped with Day {other['day_number']} (more indoor stops on the rainy day)"
        return "no drier day with more indoor stops to swap with"

    def order(self, day: dict) -> str:
        self.refresh(day)
        return "re-ordered the day"

    # -- entry point -----------------------------------------------------------
    def apply(self, finding: dict) -> str:
        kind = finding.get("kind") or "other"
        day = _day(self.plan, finding.get("day"))
        if day is None or not day.get("stops") and kind != "too_light":
            return "noted"
        handler = {
            "travel_time": self.travel_time, "too_packed": self.too_packed,
            "too_far": self.too_packed, "too_light": self.too_light,
            "weather": self.weather, "order": self.order,
        }.get(kind)
        return handler(day) if handler else "noted"

    def level_counts(self) -> None:
        """Fixes can drop or add a stop; keep the number of activities even."""
        days = self.plan.get("days", [])
        used = self.used_names()
        pool = sorted((p for p in self.pool if p["name"] not in used),
                      key=lambda p: -p.get("score", 0))
        stop_lists = [d.get("stops") or [] for d in days]
        changed = dayplanner.level_stop_counts(
            stop_lists, [d["date"] for d in days],
            [d.get("available_hours", dayplanner.MAX_HOURS_PER_DAY) for d in days],
            pool, self.hotel)
        for i in changed:
            days[i]["stops"] = stop_lists[i]
            self.refresh(days[i])

    def finish(self) -> None:
        """Even out the number of stops, then new lunch picks for every day that
        changed (no repeats)."""
        self.level_counts()
        used = set()
        for day in self.plan.get("days", []):
            stops = day.get("stops") or []
            if day["day_number"] in self.changed and stops:
                lunch = day.get("lunch")
                mid = stops[len(stops) // 2]
                far = (not lunch or lunch.get("lat") is None or haversine_km(
                    mid["lat"], mid["lon"], lunch["lat"], lunch["lon"]) > dayplanner.LUNCH_NEAR_KM)
                if far:
                    better = dayplanner.pick_lunch(self.restaurants, stops, used)
                    if better and (not lunch or better.get("distance_km", 99) <
                                   (lunch.get("distance_km") or 99)):
                        day["lunch"] = better
            if day.get("lunch"):
                used.add(day["lunch"]["name"])
        self.plan["total_km"] = dayplanner.total_distance(self.plan.get("days", []))


def fix_plan(plan: dict, findings: list[dict], pool: list[dict], restaurants: list[dict],
             hotel: tuple[float, float]) -> list[dict]:
    """Apply every finding; return the review log (finding + what was done)."""
    fixer = Fixer(plan, pool, restaurants, hotel)
    log = []
    seen = set()
    for f in findings:
        key = (f.get("kind"), f.get("day"))
        if key in seen:                      # the rules and the LLM often agree
            log.append({**f, "action": "already handled"})
            continue
        seen.add(key)
        log.append({**f, "action": fixer.apply(f)})
    fixer.finish()
    return log
