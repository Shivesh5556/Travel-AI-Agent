"""Tourist-worthiness ranking: no more vegetable markets on Day 1."""

import asyncio

import config
from tools import places, webclient


def _p(name, kind, source="openstreetmap", **extra):
    return {"name": name, "kind": kind, "lat": 30.32, "lon": 78.04,
            "visit_hours": 1.5, "source": source, **extra}


def test_wholesale_and_utility_places_are_dropped():
    pool = [_p("Sabzi Mandi", "marketplace"), _p("Anaj Mandi Road", "marketplace"),
            _p("Vegetable Market", "marketplace"), _p("ISBT Bus Stand", "attraction"),
            _p("Sahastradhara", "waterfall", "curated")]
    ranked = places.rank_places(pool, [], min_keep=0)
    assert [p["name"] for p in ranked] == ["Sahastradhara"]


def test_mandir_is_not_mistaken_for_mandi():
    assert not places._is_utility_place("Birla Mandir")
    assert not places._is_utility_place("Shiv Mandir")
    assert places._is_utility_place("Sabzi Mandi")
    assert places._is_utility_place("Subzi Mandi, Dehradun")


def test_landmarks_outrank_generic_markets_and_small_temples():
    pool = [_p("Ram Leela Bazar", "marketplace"), _p("Shiv Mandir", "place of worship"),
            _p("Robber's Cave", "ruins", "curated"), _p("Malsi Deer Park", "park", "wikivoyage")]
    ranked = places.rank_places(pool, [], min_keep=0)
    names = [p["name"] for p in ranked]
    assert names[0] == "Robber's Cave"
    assert names.index("Malsi Deer Park") < names.index("Ram Leela Bazar") if "Ram Leela Bazar" in names else True
    assert ranked[0]["must_see"] is True


def test_famous_market_survives_and_shopping_interest_lifts_markets():
    famous = places.score_place(_p("Paltan Bazaar", "marketplace"), [])
    plain = places.score_place(_p("Ram Leela Bazar", "marketplace"), [])
    assert famous > plain
    shopper = places.score_place(_p("Ram Leela Bazar", "marketplace"), ["Shopping"])
    assert shopper > plain


def test_live_events_and_multi_source_agreement_add_points():
    base = places.score_place(_p("Some Viewpoint", "viewpoint"), [])
    event = places.score_place(_p("Garba Night (2026-10-14)", "event", "ticketmaster"), ["Nightlife"])
    votes = {places._normalize_name("Some Viewpoint"): 3}
    agreed = places.score_place(_p("Some Viewpoint", "viewpoint"), [], votes)
    assert event > base
    assert agreed > base


def test_thin_pool_keeps_best_leftovers_but_never_utility():
    pool = [_p("Odd Little Spot", "place of worship"), _p("Another Spot", "marketplace"),
            _p("Sabzi Mandi", "marketplace")]
    ranked = places.rank_places(pool, [], min_keep=2)
    assert len(ranked) == 2
    assert all("Sabzi" not in p["name"] for p in ranked)


def test_curated_entry_matching_live_place_boosts_it_instead_of_duplicating():
    live = [{"name": "Sahastradhara", "kind": "waterfall", "lat": 30.3810, "lon": 78.1240,
             "visit_hours": 1.5, "source": "openstreetmap"}]
    extra = places._curated_attractions("dehradun", existing=live)
    assert "sahastradhara" not in [_n["name"].lower() for _n in extra]
    assert live[0]["curated_match"] is True
    assert places.score_place(live[0], []) >= 9 + 8


def test_find_attractions_puts_real_sights_first(monkeypatch):
    monkeypatch.setattr(config, "GEOAPIFY_ENABLED", False)
    monkeypatch.setattr(config, "TICKETMASTER_ENABLED", False)
    monkeypatch.setattr(config, "FOURSQUARE_ENABLED", False)

    osm = {"elements": [
        {"lat": 30.3260, "lon": 78.0420, "tags": {"name": "Sabzi Mandi", "shop": "marketplace"}},
        {"lat": 30.3262, "lon": 78.0424, "tags": {"name": "RAM LEELA BAZAR", "amenity": "marketplace"}},
        {"lat": 30.3810, "lon": 78.1240, "tags": {"name": "Sahastradhara", "natural": "waterfall"}},
    ]}

    async def fake_overpass(query, **kw):
        return osm

    async def nothing(*a, **kw):
        return []

    monkeypatch.setattr(webclient, "overpass", fake_overpass)
    monkeypatch.setattr(places, "wikipedia_nearby", nothing)
    monkeypatch.setattr(places, "wikivoyage_nearby", nothing)

    found = asyncio.run(places.find_attractions(30.3165, 78.0322, [], num_days=3, city="Dehradun"))
    names = [p["name"] for p in found]
    assert "Sabzi Mandi" not in names
    assert names[0] in {"Robber's Cave (Guchhupani)", "Sahastradhara", "Forest Research Institute",
                        "Kempty Falls", "Mindrolling Monastery"}
    assert any(p.get("must_see") for p in found)
