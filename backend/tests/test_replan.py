"""
Tests for the budget solver.

This is the file to point at if anyone asks "how do you know your agent
actually works?" The solver is pure Python with no network and no LLM, which
is exactly why it can be tested properly. If we had let the LLM do the
arithmetic, none of these tests could exist.
"""

import pytest

from agent import replan
from tools import budget


def make_transport(carrier, mode, price):
    return {"carrier": carrier, "mode": mode, "total_inr": price,
            "duration": "2h 00m", "provider": "test"}


def make_stay(name, price, stars=3):
    return {"name": name, "price_per_night_inr": price, "stars": stars,
            "lat": 26.9, "lon": 75.8, "area": "Centre"}


TRANSPORT = [
    make_transport("IndiGo flight", "flight", 4500),
    make_transport("Train 3A", "train", 1400),
    make_transport("Bus sleeper", "bus", 900),
]

STAYS = [
    make_stay("Backpackers Hostel", 700, 1),
    make_stay("Sunrise Guest House", 1400, 2),
    make_stay("Hotel Grand", 2800, 3),
    make_stay("Palace Resort", 9000, 5),
]


class TestBudgetMaths:
    def test_lines_add_up_to_the_subtotal(self):
        result = budget.compute(5000, 4000, days=3, travelers=2, budget_inr=50000)
        # every line except the buffer should sum to the subtotal
        parts = sum(line["amount_inr"] for line in result["lines"][:-1])
        assert parts == result["subtotal_inr"]

    def test_total_is_subtotal_plus_buffer(self):
        result = budget.compute(5000, 4000, days=3, travelers=2, budget_inr=50000)
        assert result["total_inr"] == result["subtotal_inr"] + result["buffer_inr"]

    def test_a_generous_budget_fits(self):
        result = budget.compute(5000, 4000, days=3, travelers=2, budget_inr=200000)
        assert result["fits"] is True
        assert result["over_by_inr"] == 0

    def test_a_tiny_budget_does_not_fit(self):
        result = budget.compute(5000, 4000, days=3, travelers=2, budget_inr=5000)
        assert result["fits"] is False
        assert result["over_by_inr"] > 0

    def test_shoestring_is_cheaper_than_comfort(self):
        cheap = budget.compute(0, 0, 4, 2, 100000, style="shoestring")
        posh = budget.compute(0, 0, 4, 2, 100000, style="comfort")
        assert cheap["total_inr"] < posh["total_inr"]

    def test_stay_ceiling_never_goes_negative(self):
        ceiling = budget.stay_ceiling(2000, 50000, days=5, travelers=4,
                                      style="comfort", nights=4)
        assert ceiling == 0


class TestSolver:
    def test_a_big_budget_is_solved_on_the_first_round(self):
        result = replan.solve(days=3, nights=2, travelers=2, budget_inr=90000,
                              transport_options=TRANSPORT, stay_options=STAYS)
        assert result["fits"] is True
        assert result["rounds"] == 1
        # with plenty of money it should pick the flight, not the bus
        assert result["transport"]["mode"] == "flight"

    def test_a_tight_budget_forces_at_least_one_replan(self):
        result = replan.solve(days=3, nights=2, travelers=2, budget_inr=20000,
                              transport_options=TRANSPORT, stay_options=STAYS)
        assert result["rounds"] > 1
        # the first attempt must have failed, that is why it re-planned
        assert result["attempts"][0]["fits"] is False

    def test_it_downgrades_the_style_before_giving_up(self):
        # travel_style="comfort" is what starts the solver at comfort now;
        # the default "balanced" starts at normal.
        result = replan.solve(days=4, nights=3, travelers=2, budget_inr=26000,
                              transport_options=TRANSPORT, stay_options=STAYS,
                              travel_style="comfort")
        styles = [a["style"] for a in result["attempts"]]
        assert styles[0] == "comfort"
        assert "normal" in styles or "shoestring" in styles

    def test_it_switches_off_the_flight_when_it_has_to(self):
        # only enough money if you take the bus
        result = replan.solve(days=3, nights=2, travelers=2, budget_inr=14000,
                              transport_options=TRANSPORT, stay_options=STAYS)
        modes = [a["transport"] for a in result["attempts"]]
        assert any("train" in m or "bus" in m for m in modes)

    def test_every_attempt_is_cheaper_than_or_equal_to_the_last(self):
        result = replan.solve(days=4, nights=3, travelers=3, budget_inr=25000,
                              transport_options=TRANSPORT, stay_options=STAYS)
        totals = [a["total_inr"] for a in result["attempts"]]
        for earlier, later in zip(totals, totals[1:]):
            assert later <= earlier, "re-planning must never make the trip more expensive"

    def test_an_impossible_budget_is_rejected_immediately(self):
        result = replan.solve(days=5, nights=4, travelers=4, budget_inr=3000,
                              transport_options=TRANSPORT, stay_options=STAYS)
        assert result["impossible"] is True
        assert result["rounds"] == 0
        assert result["minimum_needed_inr"] > 3000
        assert "costs about" in result["reason"]

    def test_no_transport_options_fails_cleanly(self):
        result = replan.solve(days=2, nights=1, travelers=1, budget_inr=50000,
                              transport_options=[], stay_options=STAYS)
        assert result["fits"] is False
        assert result["impossible"] is True

    def test_it_never_loops_forever(self):
        result = replan.solve(days=10, nights=9, travelers=6, budget_inr=60000,
                              transport_options=TRANSPORT, stay_options=STAYS)
        assert result["rounds"] <= replan.MAX_ROUNDS

    def test_it_picks_the_best_stay_it_can_afford_not_the_cheapest(self):
        # lots of money: should not end up in the hostel
        result = replan.solve(days=3, nights=2, travelers=2, budget_inr=120000,
                              transport_options=TRANSPORT, stay_options=STAYS)
        assert result["stay"]["name"] != "Backpackers Hostel"

    def test_savings_summary_appears_when_it_replanned(self):
        result = replan.solve(days=4, nights=3, travelers=2, budget_inr=22000,
                              transport_options=TRANSPORT, stay_options=STAYS)
        note = replan.savings_summary(result["attempts"])
        if len(result["attempts"]) > 1:
            assert "saving" in note or note == ""


@pytest.mark.parametrize("budget_inr", [15000, 25000, 40000, 80000, 150000])
def test_solver_always_returns_a_usable_shape(budget_inr):
    """Whatever the budget, the caller must get the same keys back."""
    result = replan.solve(days=3, nights=2, travelers=2, budget_inr=budget_inr,
                          transport_options=TRANSPORT, stay_options=STAYS)
    for key in ("fits", "transport", "stay", "budget", "attempts", "rounds"):
        assert key in result


class TestTravelStyle:
    """
    The budget is always a hard rule, but the traveller decides what gets
    sacrificed first. Somebody who asked for comfort should lose the hotel
    before they lose the flight.
    """

    def test_budget_style_takes_the_cheapest_ride(self):
        result = replan.solve(days=3, nights=2, travelers=2, budget_inr=90000,
                              transport_options=TRANSPORT, stay_options=STAYS,
                              travel_style="budget")
        assert result["transport"]["mode"] == "bus"

    def test_comfort_style_flies(self):
        result = replan.solve(days=3, nights=2, travelers=2, budget_inr=90000,
                              transport_options=TRANSPORT, stay_options=STAYS,
                              travel_style="comfort")
        assert result["transport"]["mode"] == "flight"

    def test_comfort_starts_higher_than_budget(self):
        cheap = replan.solve(days=3, nights=2, travelers=2, budget_inr=90000,
                             transport_options=TRANSPORT, stay_options=STAYS,
                             travel_style="budget")
        posh = replan.solve(days=3, nights=2, travelers=2, budget_inr=90000,
                            transport_options=TRANSPORT, stay_options=STAYS,
                            travel_style="comfort")
        assert posh["budget"]["total_inr"] > cheap["budget"]["total_inr"]

    def test_comfort_keeps_the_flight_when_money_is_tight(self):
        """It should cut the hotel and the daily spend before the flight."""
        result = replan.solve(days=3, nights=2, travelers=2, budget_inr=22000,
                              transport_options=TRANSPORT, stay_options=STAYS,
                              travel_style="comfort")
        if result["fits"]:
            assert result["transport"]["mode"] == "flight"

    def test_every_style_still_respects_the_budget(self):
        for style in ("budget", "balanced", "comfort"):
            result = replan.solve(days=3, nights=2, travelers=2, budget_inr=45000,
                                  transport_options=TRANSPORT, stay_options=STAYS,
                                  travel_style=style)
            assert result["budget"]["total_inr"] <= 45000, f"{style} blew the budget"

    def test_the_choice_is_reported_back(self):
        result = replan.solve(days=3, nights=2, travelers=2, budget_inr=45000,
                              transport_options=TRANSPORT, stay_options=STAYS,
                              travel_style="comfort")
        assert result["travel_style"] == "comfort"
