"""Offline regressions for budget feasibility and final itinerary constraints."""

import pytest

import dayplanner
import recommend
from agent import critic, repair, replan


HOTEL = (28.61, 77.20)
DATES = ["2026-11-01", "2026-11-02"]


def sight(name, **kwargs):
    return {"name": name, "kind": "museum", "lat": HOTEL[0], "lon": HOTEL[1],
            "visit_hours": 0.4, "score": 80, **kwargs}


def test_solver_searches_past_the_old_round_limit_and_keeps_prices_consistent():
    options = [{"carrier": f"Air {price}", "mode": "flight", "total_inr": price,
                "stops": "Non-stop" if price == 10000 else "1 stop"}
               for price in [10000, 9000, 8000, 7000, 6000]]
    options.append({"carrier": "Train", "mode": "train", "total_inr": 500})
    stay = {"name": "Guest House", "kind": "guest house", "price_per_night_inr": 1000,
            "lat": HOTEL[0], "lon": HOTEL[1]}
    result = replan.solve(3, 2, 1, 15000, options, [stay], travel_style="comfort")
    assert result["fits"]
    assert result["transport"]["mode"] == "train"
    assert result["budget"]["lines"][0]["amount_inr"] == result["transport"]["total_inr"] * 2
    assert result["budget"]["lines"][1]["amount_inr"] == recommend.group_price(result["stay"]) * 2
    assert sum(line["amount_inr"] for line in result["budget"]["lines"]) == result["budget"]["total_inr"]
    assert result["budget"]["total_inr"] <= 15000


@pytest.mark.parametrize("curation", [{"source": "curated"}, {"curated_match": True}])
def test_curated_places_still_obey_explicit_exclusions(curation):
    place = sight("Temple", kind="place of worship", **curation)
    assert recommend.score_attraction(place, [], ctx={"avoid_kinds": ["place of worship"]}) is None


def test_review_preserves_requested_stop_cap_even_without_findings():
    pool = [sight(f"Museum {i}", lat=HOTEL[0] + i * 0.0001) for i in range(12)]
    days = dayplanner.build_day_plan(pool, [], DATES, *HOTEL, max_stops_per_day=3)
    assert [len(day["stops"]) for day in days] == [3, 3]
    plan = {"days": days, "preferences": {"max_stops_per_day": 3}}
    repair.fix_plan(plan, [], pool, [], HOTEL)
    assert all(len(day["stops"]) <= 3 for day in days)


def test_weather_repair_keeps_a_dated_event_on_its_date():
    days = [
        {"day_number": 1, "date": DATES[0], "stops": [sight("Museum")],
         "available_hours": 7, "is_rainy": False, "indoor_share": 100},
        {"day_number": 2, "date": DATES[1], "stops": [sight(
            "Dated Event", kind="event", only_on=DATES[1])],
         "available_hours": 7, "is_rainy": True, "indoor_share": 0}]
    fixer = repair.Fixer({"days": days}, [], [], HOTEL)
    fixer.weather(days[1])
    assert days[1]["stops"][0]["name"] == "Dated Event"


def test_too_light_does_not_add_an_event_on_a_different_date():
    day = {"day_number": 1, "date": DATES[0], "stops": [sight("Museum")],
           "available_hours": 7}
    dayplanner.refresh_day(day, *HOTEL)
    pool = [sight("Tomorrow's event", only_on=DATES[1], score=99)]
    repair.fix_plan({"days": [day]}, [{"kind": "too_light", "day": 1}], pool, [], HOTEL)
    assert [s["name"] for s in day["stops"]] == ["Museum"]


def test_unreachable_single_stop_is_optional_instead_of_overfilling_a_short_day():
    windows = [{"start_hour": 14, "available_hours": 2, "travel_note": "Late arrival"}]
    days = dayplanner.build_day_plan([sight("Distant museum", lat=30.0, visit_hours=1)],
                                     [], DATES[:1], *HOTEL, windows=windows)
    assert days[0]["stops"] == []
    assert days[0]["hours"] <= 2


def test_levelling_preserves_valid_stops_when_geography_prevents_balancing():
    stops = [[sight("A"), sight("B"), sight("C")],
             [sight("Remote day trip", lat=HOTEL[0] + 0.3)]]
    dayplanner.level_stop_counts(stops, DATES, [7, 7], [], HOTEL)
    assert sum(map(len, stops)) == 4


def test_multiday_journey_blocks_every_day_before_arrival():
    windows = dayplanner.day_windows(3, {"mode": "train", "duration": "55h 00m", "carrier": "Train"})
    assert [w["available_hours"] for w in windows[:2]] == [0, 0]
    assert windows[2]["available_hours"] > 0


def test_elapsed_time_includes_lunch_and_waiting_until_lunchtime():
    assert dayplanner.day_elapsed_hours([sight("Museum", visit_hours=1)], *HOTEL,
                                        start_hour=9, lunch=True) == 4.5


def test_short_morning_window_omits_lunch_and_full_days_reserve_it():
    restaurant = {"name": "Cafe", "lat": HOTEL[0], "lon": HOTEL[1]}
    windows = [{"start_hour": 9, "available_hours": 2, "travel_note": "Leave early"}]
    morning = dayplanner.build_day_plan([sight("Museum", visit_hours=1)], [restaurant],
                                        DATES[:1], *HOTEL, windows=windows)[0]
    assert morning["lunch"] is None
    assert morning["elapsed_hours"] <= 2
    full = dayplanner.build_day_plan([sight("A", visit_hours=3), sight("B", visit_hours=3.2)],
                                     [restaurant], DATES[:1], *HOTEL)[0]
    assert full["elapsed_hours"] <= full["available_hours"]


def test_repair_does_not_reintroduce_lunch_on_a_changed_morning_only_day():
    restaurant = {"name": "Cafe", "lat": HOTEL[0], "lon": HOTEL[1]}
    day = {"day_number": 1, "date": DATES[0], "start_hour": 9,
           "available_hours": 2, "stops": [sight("Museum", visit_hours=1)],
           "lunch": restaurant}
    plan = {"days": [day], "destination": {"lat": HOTEL[0], "lon": HOTEL[1]}}
    repair.fix_plan(plan, [{"kind": "order", "day": 1}], [], [restaurant], HOTEL)
    assert day["lunch"] is None
    assert day["elapsed_hours"] <= 2
    assert critic.validate_plan(plan) == []


def test_final_validator_checks_constraints_and_budget_contract():
    place = sight("Closed event", kind="place of worship", only_on=DATES[1])
    day = {"day_number": 1, "date": DATES[0], "stops": [place, place],
           "hours": 5, "available_hours": 2}
    plan = {"days": [day], "preferences": {"max_stops_per_day": 1,
             "avoid_kinds": ["place of worship"]},
            "budget": {"lines": [{"amount_inr": 10}, {"amount_inr": 1}],
                       "subtotal_inr": 10, "buffer_inr": 1, "total_inr": 50,
                       "budget_inr": 20, "fits": True}}
    kinds = {v["kind"] for v in critic.validate_plan(plan)}
    assert {"stop_limit", "duplicate_stop", "unavailable_stop", "excluded_stop",
            "day_capacity", "budget_inconsistent"} <= kinds


def test_final_validator_accepts_a_repaired_plan():
    pool = [sight(f"Museum {i}", lat=HOTEL[0] + i * 0.001) for i in range(8)]
    days = dayplanner.build_day_plan(pool, [], DATES, *HOTEL, max_stops_per_day=3)
    plan = {"days": days, "destination": {"lat": HOTEL[0], "lon": HOTEL[1]},
            "preferences": {"max_stops_per_day": 3}}
    repair.fix_plan(plan, [], pool, [], HOTEL)
    assert critic.validate_plan(plan) == []
