"""Hotel names: Geoapify + OpenStreetMap merged, no network needed."""

import asyncio

import config
from tools import stays, webclient


def _geo_payload():
    return {"features": [
        {"geometry": {"coordinates": [78.0322, 30.3165]},
         "properties": {"name": "Hotel Pacific", "categories": ["accommodation.hotel"],
                        "suburb": "Rajpur Road", "formatted": "Hotel Pacific, Rajpur Road, Dehradun",
                        "datasource": {"raw": {"stars": "3"}}}},
        {"geometry": {"coordinates": [78.0400, 30.3200]},
         "properties": {"name": "Zostel Dehradun", "categories": ["accommodation.hostel"],
                        "formatted": "Zostel, Dehradun"}},
        {"geometry": {"coordinates": [78.05, 30.32]},
         "properties": {"categories": ["accommodation.hotel"]}},   # no name -> skipped
    ]}


def _osm_payload():
    return {"elements": [
        # Same hotel Geoapify already knows, slightly different spelling.
        {"lat": 30.3166, "lon": 78.0323,
         "tags": {"name": "Hotel  Pacific", "tourism": "hotel", "stars": "3"}},
        {"lat": 30.30, "lon": 78.02,
         "tags": {"name": "Rose Guest House", "tourism": "guest_house",
                  "addr:street": "Mall Road", "addr:city": "Dehradun"}},
    ]}


def test_real_names_merged_and_deduplicated(monkeypatch):
    monkeypatch.setattr(config, "GEOAPIFY_ENABLED", True)
    monkeypatch.setattr(config, "GEOAPIFY_API_KEY", "test")

    async def fake_get_json(url, **kw):
        return _geo_payload()

    async def fake_overpass(query, **kw):
        return _osm_payload()

    monkeypatch.setattr(webclient, "get_json", fake_get_json)
    monkeypatch.setattr(webclient, "overpass", fake_overpass)

    result = asyncio.run(stays.find_stays(30.3165, 78.0322, "Dehradun", 3))
    names = sorted(s["name"] for s in result)

    assert names == ["Hotel Pacific", "Rose Guest House", "Zostel Dehradun"]
    assert not any(s.get("is_price_band") for s in result)
    pacific = next(s for s in result if s["name"] == "Hotel Pacific")
    assert pacific["address"].startswith("Hotel Pacific")
    assert all(s["price_is_estimate"] for s in result)


def test_works_without_geoapify_key(monkeypatch):
    monkeypatch.setattr(config, "GEOAPIFY_ENABLED", False)

    async def fake_overpass(query, **kw):
        return _osm_payload()

    monkeypatch.setattr(webclient, "overpass", fake_overpass)
    result = asyncio.run(stays.find_stays(30.3165, 78.0322, "Dehradun", 2))
    assert {s["name"] for s in result} == {"Hotel  Pacific", "Rose Guest House"}


def test_falls_back_to_price_bands_only_when_everything_is_empty(monkeypatch):
    monkeypatch.setattr(config, "GEOAPIFY_ENABLED", False)

    async def nothing(query, **kw):
        return None

    monkeypatch.setattr(webclient, "overpass", nothing)
    result = asyncio.run(stays.find_stays(30.3, 78.0, "Dehradun", 2))
    assert result and all(s.get("is_price_band") for s in result)


def test_interest_categories_include_nightlife_and_shopping():
    from tools import places
    cats = places._geoapify_categories(["Nightlife", "Shopping", "Relaxation"]).split(",")
    assert "catering.bar" in cats
    assert "commercial.shopping_mall" in cats
    assert "leisure.spa" in cats
    assert cats[:2] == ["tourism.sights", "entertainment"]
