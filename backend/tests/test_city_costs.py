"""
v31: destination-aware daily costs.

What we promise, and test:
  1. No city -> exactly the old numbers (nothing that worked before breaks).
  2. An expensive city costs more per day than a cheap one, at every style.
  3. Shoestring < normal < comfort inside every city.
  4. "Jaipur, Rajasthan" (what the autocomplete sends) is priced as Jaipur.
  5. An unknown city falls back to averages, says so, and keeps a bigger buffer.
  6. The hotel ceiling, the floor, the totals and the solver agree.
"""

import pytest

from agent import replan
from tools import budget, city_prices, stays


def test_no_city_keeps_the_old_numbers():
    old = budget.compute(5000, 4000, days=3, travelers=2, budget_inr=50000)
    food = next(l for l in old["lines"] if l["label"] == "Food")["amount_inr"]
    assert food == 900 * 3 * 2
    assert old["buffer_percent"] == 8
    assert budget.cheapest_possible(3, 2) == (450 + 150 + 200) * 3 * 2


@pytest.mark.parametrize("style", budget.STYLES)
def test_mumbai_costs_more_per_day_than_varanasi(style):
    assert budget.per_day(style, "Mumbai") > budget.per_day(style, None) > \
        budget.per_day(style, "Varanasi")


@pytest.mark.parametrize("city", ["Mumbai", "Goa", "Hampi", "Jaipur", "Nowhereville", None])
def test_styles_stay_in_order_inside_every_city(city):
    shoe, normal, comfort = (budget.per_day(s, city) for s in budget.STYLES)
    assert shoe < normal < comfort


@pytest.mark.parametrize("raw, key", [
    ("Jaipur, Rajasthan", "jaipur"),
    ("  Bangalore ", "bengaluru"),
    ("New Delhi, Delhi", "delhi"),
    ("Panaji", "goa"),
    ("Goa (15.49930, 73.82780)", "goa"),
])
def test_city_names_are_normalised(raw, key):
    assert city_prices.normalise_city(raw) == key
    assert budget.daily_rates(raw)["rates"] == budget.daily_rates(key)["rates"]


def test_autocomplete_name_gets_the_city_hotel_price_too():
    # Before v31 "Jaipur, Rajasthan" missed the multiplier and got average rates.
    assert stays.estimate_price("hotel", 3, "Jaipur, Rajasthan") == \
        stays.estimate_price("hotel", 3, "Jaipur")
    assert stays.estimate_price("hotel", 3, "Jaipur") > stays.estimate_price("hotel", 3, "Nowhere")


def test_unknown_city_is_labelled_and_gets_a_bigger_buffer():
    info = budget.daily_rates("Nowhereville")
    assert info["basis"] == "national_average"
    assert info["confidence"] == "low"
    assert info["buffer_percent"] == budget.UNKNOWN_CITY_BUFFER_PERCENT
    result = budget.compute(0, 0, 3, 2, 100000, city="Nowhereville")
    assert result["buffer_percent"] == budget.UNKNOWN_CITY_BUFFER_PERCENT
    assert result["daily_basis"]["confidence"] == "low"


def test_known_city_is_labelled_as_an_index_estimate():
    result = budget.compute(0, 0, 3, 2, 100000, city="Mumbai")
    assert result["daily_basis"]["basis"] == "city_index"
    assert result["daily_basis"]["confidence"] == "medium"
    assert "Mumbai" in result["daily_basis"]["note"]


def test_daily_multiplier_is_clamped():
    city_prices.HOTEL_INDEX["typo city"] = 9.0          # somebody fat-fingers 9 for 0.9
    try:
        mult = city_prices.daily_multipliers("typo city")
        assert max(mult.values()) <= city_prices.MAX_DAILY_MULTIPLIER
    finally:
        del city_prices.HOTEL_INDEX["typo city"]


def test_researched_figures_win_over_the_index():
    city_prices.RESEARCHED_DAILY["testpur"] = {
        "food": {"shoestring": 300, "normal": 700, "comfort": 1500},
        "local_transport": {"shoestring": 100, "normal": 250, "comfort": 700},
        "activities": {"shoestring": 100, "normal": 300, "comfort": 900},
        "source": "unit test", "checked": "2026-11",
    }
    try:
        info = budget.daily_rates("Testpur")
        assert info["basis"] == "researched" and info["confidence"] == "high"
        assert info["rates"]["normal"]["food"] == 700
        assert info["buffer_percent"] == budget.RESEARCHED_BUFFER_PERCENT
    finally:
        del city_prices.RESEARCHED_DAILY["testpur"]


@pytest.mark.parametrize("city", ["Mumbai", "Varanasi", "Nowhereville", None])
def test_a_hotel_at_the_ceiling_really_fits(city):
    # If stay_ceiling and compute used different rates or buffers, a hotel
    # priced exactly at the ceiling could still blow the budget.
    budget_inr, travel, days, nights, people = 60000, 9000, 4, 3, 2
    for style in budget.STYLES:
        ceiling = budget.stay_ceiling(budget_inr, travel, days, people, style, nights, city)
        if ceiling <= 0:
            continue
        result = budget.compute(travel, ceiling * nights, days, people, budget_inr,
                                style=style, city=city)
        assert result["fits"], (city, style, result["over_by_inr"])


def test_floor_matches_compute():
    for city in ("Mumbai", "Hampi", None):
        result = budget.compute(0, 0, 3, 2, 10**7, style="shoestring", city=city)
        assert result["subtotal_inr"] == budget.cheapest_possible(3, 2, city)


def _ride(price, mode="train"):
    return {"carrier": "Test", "mode": mode, "total_inr": price, "stops": "non-stop"}


def _stay(name, price):
    return {"name": name, "price_per_night_inr": price, "group_price_per_night_inr": price,
            "lat": 0, "lon": 0, "kind": "hotel", "stars": 3}


def test_solver_uses_city_rates_end_to_end():
    args = dict(days=3, nights=2, travelers=2, budget_inr=30000,
                transport_options=[_ride(2000)], stay_options=[_stay("Mid", 2500)])
    cheap = replan.solve(**args, city="Varanasi")
    pricey = replan.solve(**args, city="Mumbai")
    assert cheap["fits"]
    assert pricey["budget"]["daily_basis"]["city"] == "mumbai"
    # Same ride, same hotel, same style -> Mumbai's daily costs make it dearer.
    assert pricey["minimum_needed_inr"] > cheap["minimum_needed_inr"]
    if pricey["fits"]:
        assert pricey["budget"]["total_inr"] <= 30000


def test_floor_depends_on_the_city():
    tight = dict(days=4, nights=3, travelers=2, budget_inr=7000,
                 transport_options=[_ride(100)], stay_options=[_stay("Bed", 100)])
    assert replan.solve(**tight, city="Hampi")["impossible"] is False
    assert replan.solve(**tight, city="Mumbai")["impossible"] is True
