"""
Tests for the three optional real-data upgrades: Foursquare, Geoapify and
Ticketmaster (see config.py and tools/places.py).

The rule these tests exist to pin down: with no key configured, each
function returns [] immediately and makes NO network call. Set a key and
merge it into a plan, and the shape of what comes back is normalized the
same way as every other source (name/kind/lat/lon/source).
"""

import pytest

from tools import places


@pytest.mark.asyncio
async def test_foursquare_search_disabled_without_key(monkeypatch):
    monkeypatch.setattr(places.config, "FOURSQUARE_ENABLED", False)

    async def boom(*a, **kw):
        raise AssertionError("should not call the network without a key")
    monkeypatch.setattr(places.webclient, "get_json", boom)

    assert await places._foursquare_search(26.91, 75.78, "restaurant", 5000, 10) == []


@pytest.mark.asyncio
async def test_geoapify_search_disabled_without_key(monkeypatch):
    monkeypatch.setattr(places.config, "GEOAPIFY_ENABLED", False)

    async def boom(*a, **kw):
        raise AssertionError("should not call the network without a key")
    monkeypatch.setattr(places.webclient, "get_json", boom)

    assert await places._geoapify_search(26.91, 75.78, "catering.restaurant", 5000, 10) == []


FOURSQUARE_RESPONSE = {
    "results": [
        {
            "name": "City Palace",
            "geocodes": {"main": {"latitude": 26.9258, "longitude": 75.8237}},
            "categories": [{"name": "Historic Site"}],
            "location": {"formatted_address": "Jaipur, Rajasthan"},
        }
    ]
}


@pytest.mark.asyncio
async def test_foursquare_search_normalizes_a_real_looking_response(monkeypatch):
    monkeypatch.setattr(places.config, "FOURSQUARE_ENABLED", True)
    monkeypatch.setattr(places.config, "FOURSQUARE_API_KEY", "fake_key")

    async def fake_get_json(url, params=None, headers=None, label="", ttl=None, **kw):
        # The current Places API (the v3 one was retired on 15 May 2026)
        assert headers["Authorization"] == "Bearer fake_key"
        assert headers["X-Places-Api-Version"]
        assert "fsq_place_id" in params["fields"] and "geocodes" not in params["fields"]
        return FOURSQUARE_RESPONSE
    monkeypatch.setattr(places.webclient, "get_json", fake_get_json)

    result = await places._foursquare_search(26.91, 75.78, "attraction", 5000, 10)
    assert result[0]["name"] == "City Palace"
    assert result[0]["lat"] == 26.9258
    assert result[0]["source"] == "foursquare"


GEOAPIFY_RESPONSE = {
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [75.8265, 26.9124]},
            "properties": {
                "name": "Anokhi Cafe",
                "categories": ["catering.cafe"],
                "formatted": "Anokhi Cafe, Jaipur",
            },
        }
    ]
}


@pytest.mark.asyncio
async def test_geoapify_search_normalizes_a_real_looking_response(monkeypatch):
    monkeypatch.setattr(places.config, "GEOAPIFY_ENABLED", True)
    monkeypatch.setattr(places.config, "GEOAPIFY_API_KEY", "fake_key")

    async def fake_get_json(url, params=None, label="", ttl=None, **kw):
        assert params["apiKey"] == "fake_key"
        return GEOAPIFY_RESPONSE
    monkeypatch.setattr(places.webclient, "get_json", fake_get_json)

    result = await places._geoapify_search(26.91, 75.78, "catering.cafe", 5000, 10)
    assert result[0]["name"] == "Anokhi Cafe"
    assert result[0]["kind"] == "cafe"
    assert result[0]["lon"] == 75.8265
    assert result[0]["source"] == "geoapify"


TICKETMASTER_RESPONSE = {
    "_embedded": {
        "events": [
            {
                "name": "Jaipur Literature Festival",
                "dates": {"start": {"localDate": "2026-01-22", "localTime": "10:00:00"}},
                "priceRanges": [{"min": 0, "max": 0, "currency": "INR"}],
                "url": "https://example.com/event",
                "_embedded": {"venues": [{"name": "Diggi Palace", "location": {}}]},
            }
        ]
    }
}


@pytest.mark.asyncio
async def test_ticketmaster_events_disabled_without_key(monkeypatch):
    monkeypatch.setattr(places.config, "TICKETMASTER_ENABLED", False)

    async def boom(*a, **kw):
        raise AssertionError("should not call the network without a key")
    monkeypatch.setattr(places.webclient, "get_json", boom)

    assert await places._ticketmaster_events(26.91, 75.78, 10) == []


@pytest.mark.asyncio
async def test_ticketmaster_events_normalizes_a_real_looking_response(monkeypatch):
    monkeypatch.setattr(places.config, "TICKETMASTER_ENABLED", True)
    monkeypatch.setattr(places.config, "TICKETMASTER_API_KEY", "fake_key")

    async def fake_get_json(url, params=None, label="", ttl=None, **kw):
        assert params["apikey"] == "fake_key"
        return TICKETMASTER_RESPONSE
    monkeypatch.setattr(places.webclient, "get_json", fake_get_json)

    result = await places._ticketmaster_events(26.91, 75.78, 10)
    assert "Jaipur Literature Festival" in result[0]["name"]
    assert result[0]["kind"] == "event"
    assert result[0]["blurb"] == "Diggi Palace"
    assert result[0]["source"] == "ticketmaster"


@pytest.mark.asyncio
async def test_find_attractions_merges_in_ticketmaster_events(monkeypatch):
    """The fold-in this feature is actually for: find_attractions is the
    SAME tool the agent already calls, it just now has three more optional
    sources feeding into it - no separate tool, no separate agent step."""
    monkeypatch.setattr(places.config, "TICKETMASTER_ENABLED", True)
    monkeypatch.setattr(places.config, "TICKETMASTER_API_KEY", "fake_key")
    monkeypatch.setattr(places.config, "FOURSQUARE_ENABLED", False)
    monkeypatch.setattr(places.config, "GEOAPIFY_ENABLED", False)

    async def fake_overpass(*a, **kw):
        return None

    async def fake_get_json(url, params=None, label="", ttl=None, **kw):
        if "ticketmaster" in url:
            return TICKETMASTER_RESPONSE
        return None

    monkeypatch.setattr(places.webclient, "overpass", fake_overpass)
    monkeypatch.setattr(places.webclient, "get_json", fake_get_json)

    result = await places.find_attractions(26.91, 75.78, [], city="Jaipur", num_days=1)
    assert any(p["source"] == "ticketmaster" for p in result)
