"""
Tests for combining places from more than one free source.

find_attractions/find_restaurants used to be a fallback chain (try OSM, only
try Wikipedia if that came back thin). They now fan out to THREE sources at
once - OpenStreetMap, Wikipedia, Wikivoyage - and merge the results, so a
plan reflects more than one site's opinion instead of whichever answered
first. These tests pin down the merge itself and the new Wikivoyage source.
"""

import pytest

from tools import places


def test_merge_sources_dedupes_by_name_case_and_spacing_insensitive():
    a = [{"name": "Amer Fort"}, {"name": "Hawa Mahal"}]
    b = [{"name": "amer fort"}, {"name": "City Palace"}]   # same place, different case
    c = [{"name": "Nahargarh Fort"}]

    merged = places._merge_sources(a, b, c, limit=10)

    assert [p["name"] for p in merged] == [
        "Amer Fort", "Hawa Mahal", "City Palace", "Nahargarh Fort",
    ]


def test_merge_sources_respects_limit():
    a = [{"name": f"Place {i}"} for i in range(5)]
    merged = places._merge_sources(a, limit=3)
    assert len(merged) == 3


WIKIVOYAGE_GEOSEARCH = {"query": {"pages": {"1": {"title": "Jaipur"}}}}
WIKIVOYAGE_WIKITEXT_SEE_EAT = {
    "parse": {"wikitext": {"*": """
{{see
| name=Amer Fort
| lat=26.9855
| long=75.8513
| content=A hilltop [[Rajput]] fort with mirror-work halls.
}}
{{eat
| name=LMB
| content=Famous vegetarian [[thali|thali]] since 1954.
| price=₹300-600
}}
"""}}
}


@pytest.fixture
def fake_wikivoyage(monkeypatch):
    async def fake_get_json(url, params=None, label="", ttl=None, **kw):
        if params and params.get("action") == "query":
            return WIKIVOYAGE_GEOSEARCH
        if params and params.get("action") == "parse":
            return WIKIVOYAGE_WIKITEXT_SEE_EAT
        return None
    monkeypatch.setattr(places.webclient, "get_json", fake_get_json)


@pytest.mark.asyncio
async def test_wikivoyage_nearby_extracts_see_listings(fake_wikivoyage):
    result = await places.wikivoyage_nearby(26.91, 75.78, wanted_kinds=("see",))
    assert result[0]["name"] == "Amer Fort"
    assert result[0]["lat"] == 26.9855
    assert result[0]["source"] == "wikivoyage"
    # the [[Rajput]] wikilink should be cleaned to plain text
    assert "[[" not in result[0]["blurb"]


@pytest.mark.asyncio
async def test_wikivoyage_nearby_extracts_eat_listings_as_restaurants(fake_wikivoyage):
    result = await places.wikivoyage_nearby(26.91, 75.78, wanted_kinds=("eat", "drink"))
    assert result[0]["name"] == "LMB"
    assert result[0]["kind"] == "restaurant"
    assert "300-600" in result[0]["blurb"]


@pytest.mark.asyncio
async def test_wikivoyage_nearby_handles_no_geosearch_result(monkeypatch):
    async def empty_get_json(*a, **kw):
        return None
    monkeypatch.setattr(places.webclient, "get_json", empty_get_json)
    assert await places.wikivoyage_nearby(0, 0) == []


def test_find_templates_ignores_nested_templates():
    text = "{{see|name=A|content=x {{cite|y=z}} tail}}"
    inner = places._find_templates(text, {"see"})
    assert len(inner) == 1
    assert "cite" in inner[0]           # nested template preserved, not truncated


def test_split_fields_does_not_split_inside_wikilink():
    inner = "see|name=A|content=[[MG Road|MG Road]] area"
    fields = places._split_fields(inner)
    assert len(fields) == 3
