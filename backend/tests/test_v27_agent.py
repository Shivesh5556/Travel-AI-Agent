"""
v27: the agent fixes what its reviewer finds, day trips carry their drive, and
the slow / failing network calls from the v26 server log are handled.
"""

import asyncio
import time

import httpx
import pytest

import config
import dayplanner
from agent import critic, repair
from tools import places, stays, webclient

HOTEL = (30.3290, 78.0410)          # Dehradun
DATES = ["2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27"]


def _dehradun_plan(windows=None):
    ranked = places.rank_places(places._curated_attractions("dehradun"),
                                ["Culture & Heritage", "Nature"], {},
                                ctx={"anchor": (30.3165, 78.0322)})
    dayplanner.set_base_elevation(HOTEL, None)
    windows = windows or dayplanner.day_windows(
        5, {"mode": "train", "carrier": "Train", "duration": "5h 21m"})
    return ranked, dayplanner.build_day_plan(ranked, [], DATES, *HOTEL, windows=windows)


# ---------------------------------------------------------------------------
# Day trips: the drive is part of the plan
# ---------------------------------------------------------------------------

def test_mussoorie_day_has_its_drive_and_is_not_the_departure_day():
    _, days = _dehradun_plan()
    mussoorie = [d for d in days if any("Mussoorie" in s["name"] for s in d["stops"])]
    assert mussoorie, "Mussoorie should still be in a 5-day Dehradun trip"
    for d in mussoorie:
        assert d["day_trip"] and "Day trip" in d["travel_note"]
        assert d["day_number"] != 5                       # not on the way home
        # city stops are not mixed into the hill outing
        assert all(dayplanner._same_kind_of_day(s, d["stops"], HOTEL) for s in d["stops"])


def test_hill_drive_uses_hill_roads_when_elevation_says_so():
    stops = [{"name": "Gun Hill, Mussoorie", "lat": 30.4560, "lon": 78.0664, "visit_hours": 0.75,
              "leg_km": 14.2, "elevation_m": 2100}]
    dayplanner.set_base_elevation(HOTEL, 650)
    hill = dayplanner.day_metrics(stops, *HOTEL)["day_trip"]
    dayplanner.set_base_elevation(HOTEL, 2050)              # same height: a flat drive
    flat = dayplanner.day_metrics(stops, *HOTEL)["day_trip"]
    dayplanner.set_base_elevation(HOTEL, None)
    assert hill["hours"] > flat["hours"] * 1.5
    assert 25 <= hill["km"] <= 40                           # ~33 km by the real road


def test_day_hours_include_the_way_back():
    stops = [{"name": "A", "lat": 30.33, "lon": 78.05, "visit_hours": 1.0, "leg_km": 1.0}]
    m = dayplanner.day_metrics(stops, *HOTEL)
    assert m["return_km"] > 0 and m["hours"] > 1.0 + 1.0 / 20.0


# ---------------------------------------------------------------------------
# The agent acts on review findings
# ---------------------------------------------------------------------------

def _plan_with_trip_on_last_day():
    ranked, days = _dehradun_plan()
    trip_day = next(d for d in days if d["day_trip"])
    last = days[-1]
    repair._swap_stops(trip_day, last)                   # recreate the v26 mistake
    for d in (trip_day, last):
        dayplanner.refresh_day(d, *HOTEL)
    return ranked, {"days": days}


def test_review_finding_about_travel_time_moves_the_day_trip():
    ranked, plan = _plan_with_trip_on_last_day()
    assert plan["days"][-1]["day_trip"]
    finding = {"kind": "travel_time", "day": 5, "from_llm": True,
               "issue": "Day 5 lists Mussoorie attractions but does not include travel time"}
    log = repair.fix_plan(plan, [finding], ranked, [], HOTEL)
    assert "moved the day trip" in log[0]["action"]
    assert not plan["days"][-1]["day_trip"]


def test_too_packed_drops_the_weakest_non_must_see_stop():
    day = {"day_number": 1, "date": DATES[1], "available_hours": 3.0, "stops": [
        {"name": "Big Fort", "lat": 30.33, "lon": 78.04, "visit_hours": 2.5, "score": 90,
         "must_see": True},
        {"name": "Small Park", "lat": 30.331, "lon": 78.041, "visit_hours": 1.5, "score": 40}]}
    plan = {"days": [day]}
    dayplanner.refresh_day(day, *HOTEL)
    log = repair.fix_plan(plan, [{"kind": "too_packed", "day": 1}], [], [], HOTEL)
    assert [s["name"] for s in day["stops"]] == ["Big Fort"]
    assert "Small Park" in day["extra_ideas"] and "dropped Small Park" in log[0]["action"]


def test_too_light_adds_the_best_unused_place_nearby_and_open():
    day = {"day_number": 2, "date": "2026-10-23", "available_hours": 7.0, "stops": [
        {"name": "Clock Tower", "lat": 30.3226, "lon": 78.0359, "visit_hours": 1.0, "score": 60}]}
    plan = {"days": [day]}
    dayplanner.refresh_day(day, *HOTEL)
    pool = [{"name": "Closed Fri Museum", "lat": 30.323, "lon": 78.036, "visit_hours": 1.5,
             "score": 95, "closed_days": [4]},          # 23 Oct 2026 is a Friday
            {"name": "Open Garden", "lat": 30.325, "lon": 78.037, "visit_hours": 1.0, "score": 70},
            {"name": "Far Hill", "lat": 30.46, "lon": 78.07, "visit_hours": 1.0, "score": 99}]
    repair.fix_plan(plan, [{"kind": "too_light", "day": 2}], pool, [], HOTEL)
    names = [s["name"] for s in day["stops"]]
    assert "Open Garden" in names                       # the day is re-ordered after adding
    assert "Closed Fri Museum" not in names and "Far Hill" not in names


def test_traveller_only_sees_what_no_plan_change_can_fix():
    plan = {"days": [{"day_number": 1, "date": DATES[0], "hours": 11.0, "total_km": 80,
                      "stops": [{"name": "A", "lat": 30.3, "lon": 78.0}]}],
            "budget": {"fits": False, "over_by_inr": 1200}}
    issues = critic.rule_checks(plan, "", "Dehradun")
    shown = [i for i in issues if i.get("for_traveller")]
    assert [i["kind"] for i in shown] == ["over_budget"]
    assert any(i["kind"] == "too_packed" for i in issues if not i.get("for_traveller"))


def test_llm_review_is_structured_and_clipped(monkeypatch):
    monkeypatch.setattr(critic.llm.config, "LLM_ENABLED", True)

    async def fake_json(*a, **k):
        return {"issues": [{"day": "5", "type": "travel_time", "issue": "x" * 500, "fix": "y"},
                           {"day": None, "type": "nonsense", "issue": "z"}]}
    monkeypatch.setattr(critic.llm, "ask_json", fake_json)
    out = asyncio.run(critic.llm_review("plan", "trip"))
    assert out[0]["kind"] == "travel_time" and out[0]["day"] == 5 and len(out[0]["issue"]) == 300
    assert out[1]["kind"] == "other"


# ---------------------------------------------------------------------------
# Stays that are not open to tourists
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,ok", [("Central Government Touring Officers Guest House", False),
                                     ("ONGC Guest House", False), ("Officers Mess", False),
                                     ("Hotel Pacific", True), ("Zostel Dehradun", True)])
def test_government_and_institution_guest_houses_are_skipped(name, ok):
    assert stays.open_to_tourists({"name": name}) is ok


# ---------------------------------------------------------------------------
# Speed: the v26 log
# ---------------------------------------------------------------------------

def test_overpass_races_mirrors_instead_of_waiting_for_each(monkeypatch):
    """Three mirrors that hang for 2 s and one that answers fast: v26 waited
    for every hanging one in turn; now the fast one wins quickly."""
    monkeypatch.setattr(config, "OVERPASS_MIRRORS",
                        [f"https://m{i}.example/api" for i in range(4)])
    monkeypatch.setattr(webclient, "HEDGE_SECONDS", 0.2)
    webclient.reset_mirrors()
    webclient._mirror_speed.clear()

    async def fake_post(url, **kw):
        if url.startswith("https://m3"):
            await asyncio.sleep(0.05)
            return {"elements": [{"id": 1}]}
        await asyncio.sleep(2.0)
        return None

    monkeypatch.setattr(webclient, "post_json", fake_post)
    started = time.monotonic()
    data = asyncio.run(webclient.overpass("[out:json];node(1);out;", label="t"))
    assert data == {"elements": [{"id": 1}]}
    assert time.monotonic() - started < 1.5
    assert webclient._live_mirrors()[0].startswith("https://m3")   # remembered as fastest


def test_a_refusing_host_is_paused_after_the_first_401(monkeypatch):
    webclient.reset_blocked_hosts()
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, **kw):
            calls["n"] += 1
            request = httpx.Request("GET", url)
            return httpx.Response(401, request=request)

    monkeypatch.setattr(webclient.httpx, "AsyncClient", FakeClient)
    for i in range(5):
        asyncio.run(webclient.get_json("https://api.foursquare.com/v3/places/search",
                                       params={"q": i}, label="fsq"))
    assert calls["n"] == 1
    webclient.reset_blocked_hosts()


def test_user_agent_always_carries_a_contact_field():
    assert "contact:" in config.USER_AGENT


def test_tool_calls_in_one_turn_run_in_parallel(monkeypatch):
    from datetime import date, timedelta
    from agent import loop
    from schemas import TripRequest

    start = date.today() + timedelta(days=5)
    req = TripRequest(source_city="Delhi", destination_city="Dehradun", start_date=start,
                      end_date=start + timedelta(days=2), budget_inr=30000)
    calls = [{"id": str(i), "function": {"name": n, "arguments": "{}"}} for i, n in enumerate(
        ["get_weather", "search_transport", "find_attractions", "find_restaurants",
         "search_stays", "get_local_tips"])]
    turns = {"n": 0}

    async def fake_ask_tools(messages, tools, trip_id=None):
        turns["n"] += 1
        return {"role": "assistant", "content": "", "tool_calls": calls}

    async def slow_tool(name, args, extra=None):
        await asyncio.sleep(0.3)
        result = {"get_weather": {"days": [], "rainy_days": []},
                  "search_transport": {"options": [], "count": 0},
                  "find_attractions": {"attractions": [], "count": 0},
                  "find_restaurants": {"restaurants": [], "count": 0},
                  "search_stays": {"options": [], "count": 0},
                  "get_local_tips": {"tips": [], "count": 0}}[name]
        return {"ok": True, "result": result}

    monkeypatch.setattr(loop.llm, "ask_tools", fake_ask_tools)
    monkeypatch.setattr(loop, "run_tool", slow_tool)
    context = {}
    t0 = time.monotonic()
    asyncio.run(loop._gather_with_llm("par", req, context))
    assert time.monotonic() - t0 < 1.0           # 6 x 0.3 s would be 1.8 s in a row
    assert turns["n"] == 1                       # no extra "Done." round trip
    assert all(k in context for k in loop.GATHER_KEYS)


def test_legacy_foursquare_key_is_recognised(monkeypatch):
    import importlib
    monkeypatch.setenv("FOURSQUARE_API_KEY", "fsq3abcdefghijklmnop=")
    monkeypatch.setenv("FOURSQUARE_URL", "https://places-api.foursquare.com/places/search")
    fresh = importlib.reload(config)
    try:
        assert fresh.FOURSQUARE_LEGACY_KEY and not fresh.FOURSQUARE_ENABLED
    finally:
        monkeypatch.delenv("FOURSQUARE_API_KEY")
        monkeypatch.delenv("FOURSQUARE_URL")
        importlib.reload(config)


# ---------------------------------------------------------------------------
# Found by running the real .env with every provider unreachable
# ---------------------------------------------------------------------------

def test_small_overshoot_trims_our_buffer_not_the_travellers_spending():
    from agent import replan
    # food etc. 17,500 + train 1,120 + stay 5,180 = 23,800; +8% buffer = 25,704,
    # i.e. Rs 704 over a Rs 25,000 budget - the case from the v26 server log
    stay = [{"name": "Guest House", "kind": "guest house", "price_per_night_inr": 1295,
             "total_inr": 1295 * 4, "lat": 30.33, "lon": 78.04, "stars": 2}]
    train = [{"carrier": "Train", "mode": "train", "total_inr": 560}]
    result = replan.solve(days=5, nights=4, travelers=2, budget_inr=25000,
                          transport_options=train, stay_options=stay)
    assert result["fits"]
    assert result["budget"]["style"] == "normal"                # food budget NOT halved
    assert 4 <= result["budget"]["buffer_percent"] < 8
    assert any("safety buffer" in (a["change_made"] or "") for a in result["attempts"])


def test_unreachable_groq_fails_fast_after_the_first_call(monkeypatch):
    import llm
    calls = {"n": 0}

    class Refused:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, **kw):
            calls["n"] += 1
            raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "AsyncClient", Refused)
    monkeypatch.setattr(llm, "_unreachable_until", 0.0)
    monkeypatch.setattr(llm.config, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(llm.config, "LLM_ENABLED", True)
    t0 = time.monotonic()
    for _ in range(3):
        with pytest.raises(llm.LLMUnavailable):
            asyncio.run(llm.ask_text("s", "u"))
    assert calls["n"] == 2                      # one call + one retry, then paused
    assert time.monotonic() - t0 < 3.0          # v26: ~15 s per call
    monkeypatch.setattr(llm, "_unreachable_until", 0.0)


def test_unreachable_host_is_paused(monkeypatch):
    webclient.reset_blocked_hosts()
    calls = {"n": 0}

    class Refused:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, **kw):
            calls["n"] += 1
            raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(webclient.httpx, "AsyncClient", Refused)
    for i in range(4):
        asyncio.run(webclient.get_json("https://api.example-down.com/x", params={"i": i}))
    assert calls["n"] == 1
    webclient.reset_blocked_hosts()


def test_weather_label_says_why_there_is_no_forecast(monkeypatch):
    from datetime import date, timedelta
    from tools import weather

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(weather.webclient, "get_json", nothing)
    soon = date.today() + timedelta(days=10)
    out = asyncio.run(weather.get_forecast(30.3, 78.0, soon.isoformat(),
                                           (soon + timedelta(days=2)).isoformat()))
    assert "did not answer" in out["source"] and "16-day" not in out["source"]


# ---------------------------------------------------------------------------
# v29: the same number of activities every day
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days", [3, 4, 5])
def test_every_city_gets_an_even_number_of_stops_per_day(days):
    import eval_recommendations
    for row in eval_recommendations.evaluate_offline(days):
        city, _, _, _, low, high = row
        assert high - low <= 1, f"{city}: {low}-{high} stops per day"


def test_levelling_moves_stops_before_it_drops_any():
    ranked, days = _dehradun_plan()
    counts = [len(d["stops"]) for d in days]
    assert max(counts) - min(counts) <= 1
    assert sum(counts) >= len(ranked) - 2          # v29 first draft trimmed it to 10 of 14


def test_level_stop_counts_fills_a_light_day_from_spare_places():
    near = lambda name, i, score=50: {"name": name, "lat": 30.33 + i * 0.002, "lon": 78.04,
                                      "visit_hours": 1.0, "score": score}
    day_stops = [[near("A", 0), near("B", 1), near("C", 2), near("D", 3)], [near("E", 4)]]
    pool = [near("Spare", 5, score=80)]
    dayplanner.level_stop_counts(day_stops, DATES[1:3], [7.0, 7.0], pool, HOTEL)
    assert [len(d) for d in day_stops] in ([2, 3], [3, 2], [2, 2], [3, 3])
    assert "Spare" in {p["name"] for d in day_stops for p in d}


def test_levelling_tops_up_days_while_good_places_sit_unused():
    """v29: Jaipur had 12 places but got 2,1,2,2 stops - days within one of each
    other, yet half the places unused. Now the lightest days take spare places
    nearby while the day stays comfortable."""
    near = lambda name, i: {"name": name, "lat": HOTEL[0] + 0.004 * i, "lon": HOTEL[1],
                            "visit_hours": 1.0, "score": 60 - i}
    day_stops = [[near("A", 1), near("B", 2)], [near("C", 3)], [near("D", 4), near("E", 5)]]
    pool = [near("F", 6), near("G", 7), near("H", 8)]
    dayplanner.level_stop_counts(day_stops, DATES[1:4], [7.0, 7.0, 7.0], pool, HOTEL)
    counts = [len(d) for d in day_stops]
    assert max(counts) - min(counts) <= 1
    assert sum(counts) == 8                      # every spare place got used


def test_a_place_next_door_fits_an_out_of_town_day():
    """Jal Mahal (inside the town line) belongs on the Amer Fort day (just past it)."""
    km = lambda d: d / 111.0
    amer = {"name": "Amer Fort", "lat": HOTEL[0] + km(10.5), "lon": HOTEL[1], "visit_hours": 3.0}
    jal = {"name": "Jal Mahal", "lat": HOTEL[0] + km(7.5), "lon": HOTEL[1], "visit_hours": 0.5}
    city = {"name": "City temple", "lat": HOTEL[0] + km(1.0), "lon": HOTEL[1], "visit_hours": 1.0}
    assert dayplanner._same_kind_of_day(jal, [amer], HOTEL)
    assert not dayplanner._same_kind_of_day(city, [amer], HOTEL)
