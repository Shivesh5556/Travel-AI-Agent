"""Contract checks at the boundary between selected inputs and plan output."""

from datetime import date, timedelta

import pytest

from agent import loop
from schemas import TripRequest
from tools import geo
import preferences


def request(**changes):
    start = date.today() + timedelta(days=5)
    return TripRequest(**({"source_city": "Delhi", "destination_city": "Jaipur",
                           "start_date": start, "end_date": start + timedelta(days=2),
                           "budget_inr": 15000} | changes))


def test_doubled_return_is_estimated_even_when_outbound_is_a_live_offer():
    original = {"total_inr": 5000, "price_is_estimate": False, "source": "provider"}
    item = loop._with_return_fare(original, 2)
    assert item["return_total_inr"] == 10000
    assert item["return_per_person_inr"] == 5000
    assert item["price_is_estimate"] and item["return_price_is_estimate"]
    assert not item["outbound_price_is_estimate"]
    assert "not been checked" in item["return_fare_note"]
    assert original == {"total_inr": 5000, "price_is_estimate": False, "source": "provider"}


def test_transport_exclusions_are_never_silently_relaxed():
    options = [{"mode": "flight", "depart": "06:00", "total_inr": 5000}]
    kept, note = preferences.filter_transport(options, {"avoid_modes": ["flight"]})
    assert kept == [] and note
    kept, note = preferences.filter_transport(options, {"no_flights_before": 8})
    assert kept == [] and note
    kept, _ = preferences.filter_transport([{"mode": "flight"}], {"no_flights_before": 8})
    assert kept == []


@pytest.mark.asyncio
async def test_job_uses_selected_coordinates_and_releases_context(monkeypatch):
    req = request(destination_location={"id": "selected:jaipur", "name": "Jaipur",
                                        "lat": 26.92, "lon": 75.81, "country_code": "in"})

    async def no_network(*args, **kwargs):
        raise AssertionError("Selected coordinates must survive every lookup")

    async def inspect_job(trip_id, body):
        chosen = await geo.geocode(body.destination_city)
        assert chosen["lat"] == 26.92 and chosen["lon"] == 75.81
        return {"ok": True}

    monkeypatch.setattr(geo.webclient, "get_json", no_network)
    monkeypatch.setattr(loop, "_plan_trip", inspect_job)
    assert await loop.plan_trip("scoped-test", req) == {"ok": True}
    assert geo._selected_locations.get() == {}


@pytest.mark.asyncio
async def test_unfitted_decision_never_becomes_a_successful_itinerary(monkeypatch):
    async def understand(*args):
        return {"understood": []}

    async def gather(trip_id, req, context):
        context.update(destination={"lat": 26.91, "lon": 75.78}, transport_options=[],
                       stay_options=[], attractions=[])

    monkeypatch.setattr(loop.config, "LLM_ENABLED", False)
    monkeypatch.setattr(loop.config, "STRICT_MODE", False)
    monkeypatch.setattr(loop.preferences, "understand", understand)
    monkeypatch.setattr(loop, "_fill_gaps", gather)
    monkeypatch.setattr(loop.replan, "solve", lambda **kwargs: {
        "fits": False, "impossible": False, "reason": "No complete combination fits",
        "attempts": [], "minimum_needed_inr": 17000})
    result = await loop._plan_trip("budget-failure", request())
    assert result["ok"] is False
    assert result["reason"] == "No complete combination fits"
