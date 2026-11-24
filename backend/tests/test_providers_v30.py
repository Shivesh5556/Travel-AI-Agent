"""Regression cases for confirmed locations, transport feasibility and weather state."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import monitor
from schemas import CityLocation, TripRequest
from tools import geo, transport, weather

IST = timezone(timedelta(hours=5, minutes=30))


def location(name="Bilaspur", state="Chhattisgarh", lat=22.08, lon=82.14):
    return CityLocation(id=f"city:{lat},{lon}", name=name, state=state,
                        lat=lat, lon=lon, country_code="in")


def request(**kwargs):
    values = dict(source_city="Delhi", destination_city="Jaipur", start_date="2030-01-10",
                  end_date="2030-01-12", budget_inr=25000)
    return TripRequest(**(values | kwargs))


def test_location_validates_country_and_coordinates():
    with pytest.raises(ValidationError):
        CityLocation(id="x", name="Genoa", lat=44.4, lon=8.9, country_code="it")
    with pytest.raises(ValidationError):
        location(lat=float("nan"))


def test_same_name_locations_keep_distinct_identity():
    req = request(source_city="Bilaspur", destination_city="Bilaspur",
                  source_location=location(),
                  destination_location=location(state="Himachal Pradesh", lat=31.34, lon=76.76))
    assert req.source_city == "Bilaspur, Chhattisgarh"
    assert req.destination_city == "Bilaspur, Himachal Pradesh"


def test_same_place_and_stale_selections_are_rejected():
    with pytest.raises(ValidationError):
        request(source_city="Bilaspur", destination_city="Bilaspur",
                source_location=location(), destination_location=location())
    with pytest.raises(ValidationError):
        request(source_location=location())


def test_children_cannot_exceed_group_without_explicit_adults():
    with pytest.raises(ValidationError, match="at least one adult"):
        request(travelers=2, children=3)
    req = request(adults=2, children=3)
    assert req.travelers == 5 and req.num_adults == 2


@pytest.mark.asyncio
async def test_unresolved_location_never_becomes_india_centre(monkeypatch):
    async def no_data(*args, **kwargs):
        return None
    async def no_alternative(*args, **kwargs):
        return []
    monkeypatch.setattr(geo.webclient, "get_json", no_data)
    monkeypatch.setattr(geo, "_geoapify_autocomplete", no_alternative)
    with pytest.raises(geo.LocationNotFound, match="Could not locate"):
        await geo.geocode("A place that does not exist")


@pytest.mark.asyncio
async def test_selected_coordinates_are_scoped_to_concurrent_plan(monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("A selected city must not be geocoded again")
    monkeypatch.setattr(geo.webclient, "get_json", no_network)

    async def resolve(chosen):
        with geo.use_locations({"Bilaspur": chosen}):
            await asyncio.sleep(0)
            return await geo.geocode("Bilaspur")

    first, second = await asyncio.gather(resolve(location()),
        resolve(location(state="Himachal Pradesh", lat=31.34, lon=76.76)))
    assert first["lat"] == 22.08
    assert second["lat"] == 31.34
    assert not geo._selected_locations.get()


@pytest.mark.asyncio
async def test_india_candidates_have_stable_ids(monkeypatch):
    async def fake_data(*args, **kwargs):
        return [{"class": "place", "type": "city", "lat": "44.4", "lon": "8.9",
                 "address": {"city": "Genoa", "country": "Italy", "country_code": "it"}},
                {"class": "place", "type": "city", "lat": "22.08", "lon": "82.14",
                 "address": {"city": "Bilaspur", "state": "Chhattisgarh", "country": "India",
                             "country_code": "in"}}]
    monkeypatch.setattr(geo.webclient, "get_json", fake_data)
    hits = await geo.search_candidates("Bilaspur")
    assert len(hits) == 1
    assert hits[0]["country_code"] == "in"
    assert hits[0]["id"] == (await geo.search_candidates("Bilaspur"))[0]["id"]


@pytest.mark.asyncio
async def test_islands_have_no_mainland_train_or_bus_estimates():
    assert await transport.search_ground_transport("Delhi", "Port Blair", 2) == []


@pytest.mark.asyncio
async def test_hill_town_has_no_fictional_rail_service():
    options = await transport.search_ground_transport("Delhi", "Munnar", 2)
    assert options and all(o["mode"] == "bus" for o in options)
    assert all(o["price_is_estimate"] and not o["availability_is_verified"] for o in options)


def test_seasonal_weather_uses_each_dates_month():
    result = weather._seasonal_estimate(date(2030, 10, 31), date(2030, 11, 2), 13.08, 80.27)
    assert [d["rain_chance"] for d in result["days"]] == [45, 55, 55]
    assert result["rainy_days"] == ["2030-11-01", "2030-11-02"]


@pytest.mark.asyncio
async def test_mixed_horizon_keeps_available_forecast(monkeypatch):
    today = datetime.now(IST).date()
    start, end, horizon = today + timedelta(days=14), today + timedelta(days=18), today + timedelta(days=15)
    calls = []
    dates = [(start + timedelta(days=i)).isoformat() for i in range(2)]

    async def forecast(url, params=None, **kwargs):
        calls.append(params)
        return {"daily": {"time": dates, "weathercode": [0, 63], "temperature_2m_min": [20, 21],
                           "temperature_2m_max": [30, 31], "precipitation_probability_max": [0, 90]}}

    async def climate(lat, lon, first, last):
        return weather._seasonal_estimate(first, last, lat, lon)

    monkeypatch.setattr(weather.webclient, "get_json", forecast)
    monkeypatch.setattr(weather, "_climate_from_history", climate)
    result = await weather.get_forecast(26.91, 75.78, start.isoformat(), end.isoformat())
    assert calls[0]["end_date"] == horizon.isoformat()
    assert len(result["days"]) == 5
    assert result["has_forecast"] and not result["is_real_forecast"]
    assert result["forecast_dates"] == dates
    assert result["days"][1]["rain_chance"] == 90
    assert result["days"][1]["is_real_forecast"]
    assert not result["days"][-1]["is_real_forecast"]


def trip_with_weather(rainy=False):
    first = (datetime.now(IST).date() + timedelta(days=1)).isoformat()
    second = (date.fromisoformat(first) + timedelta(days=1)).isoformat()
    return SimpleNamespace(id="test-trip", start_date=first, end_date=second,
        plan={"destination": {"lat": 26.91, "lon": 75.78},
              "weather": {"is_real_forecast": True, "rainy_days": [first] if rainy else []},
              "days": [{"date": first, "day_number": 1, "is_rainy": rainy, "indoor_share": 0,
                        "hours": 4, "available_hours": 7, "stops": [{"name": "Fort"}]},
                       {"date": second, "day_number": 2, "is_rainy": False, "indoor_share": 90,
                        "hours": 4, "available_hours": 7, "stops": [{"name": "Museum"}]}]})


def forecast_for(trip, rainy_dates):
    return {"is_real_forecast": True, "rainy_days": rainy_dates,
            "days": [{"date": d["date"], "is_rainy": d["date"] in rainy_dates, "rain_chance": 90
                      if d["date"] in rainy_dates else 0} for d in trip.plan["days"]]}


@pytest.mark.asyncio
async def test_monitor_persists_clear_baseline_without_mutating_old_json(monkeypatch):
    trip = trip_with_weather(rainy=True)
    original = trip.plan
    fresh = forecast_for(trip, [])
    async def get_forecast(*args):
        return fresh
    monkeypatch.setattr(monitor.weather, "get_forecast", get_forecast)
    assert await monitor.check_one_trip(SimpleNamespace(add=lambda obj: None), trip) == []
    assert trip.plan is not original
    assert original["weather"]["rainy_days"]
    assert trip.plan["weather"]["rainy_days"] == []


@pytest.mark.asyncio
async def test_monitor_does_not_repeat_alert_or_use_stale_dry_day(monkeypatch):
    trip = trip_with_weather()
    fresh = forecast_for(trip, [d["date"] for d in trip.plan["days"]])
    async def get_forecast(*args):
        return fresh
    monkeypatch.setattr(monitor.weather, "get_forecast", get_forecast)
    alerts = []
    session = SimpleNamespace(add=alerts.append)
    first = await monitor.check_one_trip(session, trip)
    assert len(first) == 2
    assert all("Swapping" not in a.suggestion for a in first)
    assert await monitor.check_one_trip(session, trip) == []


def test_monitor_rejects_swap_onto_closed_day_or_short_window():
    trip = trip_with_weather()
    rainy, indoor = trip.plan["days"]
    assert monitor._best_swap(trip.plan, rainy["date"]) == indoor
    indoor["stops"][0]["closed_days"] = [date.fromisoformat(rainy["date"]).weekday()]
    assert monitor._best_swap(trip.plan, rainy["date"]) is None
    indoor["stops"][0]["closed_days"] = []
    rainy["available_hours"] = 2
    assert monitor._best_swap(trip.plan, rainy["date"]) is None


@pytest.mark.asyncio
async def test_monitor_commits_successful_checks_even_without_alerts(monkeypatch):
    class Session:
        commits = 0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def execute(self, query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [trip_with_weather()]))
        async def commit(self):
            self.commits += 1
    session = Session()
    async def check(*args):
        return []
    monkeypatch.setattr(monitor, "SessionLocal", lambda: session)
    monkeypatch.setattr(monitor, "check_one_trip", check)
    assert await monitor.run_once() == 0
    assert session.commits == 1
