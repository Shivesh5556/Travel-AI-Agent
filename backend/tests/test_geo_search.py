"""
Tests for the "which place did you actually mean" feature.

Typing a bare name like "Goa" is genuinely ambiguous - it's an Indian state,
and there are unrelated places elsewhere that share the name. These tests
pin down that geo.search_candidates() returns distinct, correctly-labelled
candidates instead of silently picking one, and that the /api/geo/search
endpoint the frontend's autocomplete calls wires that up correctly.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import main
from tools import geo

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


FAKE_NOMINATIM_HITS = [
    {
        "class": "place", "type": "city",
        "lat": "15.2993", "lon": "74.1240",
        "display_name": "Goa, India",
        "address": {"state": "Goa", "country": "India"},
    },
    {
        "class": "place", "type": "city",
        "lat": "44.4056", "lon": "8.9463",
        "display_name": "Genoa, Liguria, Italy",
        "address": {"city": "Genoa", "state": "Liguria", "country": "Italy"},
    },
    {
        # not a settlement - must be filtered out
        "class": "shop", "type": "supermarket",
        "lat": "1.0", "lon": "1.0",
        "display_name": "Goa Supermarket",
        "address": {},
    },
]


@pytest.fixture(autouse=True)
def fake_nominatim(monkeypatch):
    async def fake_get_json(url, params=None, label="", ttl=None, **kw):
        return FAKE_NOMINATIM_HITS
    monkeypatch.setattr(geo.webclient, "get_json", fake_get_json)


async def test_disambiguates_same_name_different_places():
    results = await geo.search_candidates("goa")
    names = [r["name"] for r in results]

    assert any("Goa" in n for n in names)
    assert any("Genoa" in n for n in names)
    # each candidate should carry enough context to tell them apart
    india = next(r for r in results if r["name"] == "Goa")
    italy = next(r for r in results if "Genoa" in r["name"])
    assert india["country"] == "India"
    assert italy["country"] == "Italy"
    assert india["subtitle"] != italy["subtitle"]


async def test_filters_out_non_place_results():
    results = await geo.search_candidates("goa")
    assert not any("Supermarket" in r["name"] for r in results)


async def test_short_query_returns_nothing():
    assert await geo.search_candidates("g") == []
    assert await geo.search_candidates("") == []


async def test_builtin_table_is_instant_and_has_an_airport_code():
    results = await geo.search_candidates("jaipur")
    builtin = next((r for r in results if r["source"] == "builtin"), None)
    assert builtin is not None
    assert builtin["iata"] == "JAI"


async def test_geo_search_endpoint(client):
    response = await client.get("/api/geo/search", params={"q": "goa"})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "goa"
    assert len(body["results"]) >= 2
    assert any("Genoa" in r["name"] for r in body["results"])
