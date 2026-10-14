"""The recommendation engine: worth-visiting scores, stay ranking, budget use."""

import asyncio

import config
import recommend
from agent import replan
from dayplanner import build_day_plan, shortlist_by_quality
from tools import places, webclient
from tools import stays as stays_tool


def _p(name, kind, source="openstreetmap", lat=30.32, lon=78.04, **extra):
    return {"name": name, "kind": kind, "lat": lat, "lon": lon,
            "visit_hours": 1.5, "source": source, **extra}


def _stay(name, kind, stars, price, lat=30.3165, lon=78.0322, tagged=True, sources=1,
          website="", **extra):
    return {"name": name, "kind": kind, "area": "Centre", "stars": stars, "stars_tagged": tagged,
            "source_count": sources, "lat": lat, "lon": lon, "website": website,
            "address": "", "price_per_night_inr": price, "total_inr": price * 2,
            "price_is_estimate": True, **extra}


# ---------------------------------------------------------------- attractions

class TestAttractionScore:
    def test_score_is_0_to_100_with_every_part_explained(self):
        rec = recommend.score_attraction(_p("Amer Fort", "fort", "curated"), ["Culture & Heritage"])
        assert 0 <= rec["score"] <= 100
        assert set(rec["parts"]) == set(recommend.ATTRACTION_WEIGHTS)
        assert sum(recommend.ATTRACTION_WEIGHTS.values()) == 100
        assert rec["reasons"] and rec["why"].endswith(".")

    def test_every_style_weighting_for_stays_sums_to_100(self):
        for style, weights in recommend.STAY_WEIGHTS_BY_STYLE.items():
            assert sum(weights.values()) == 100, style

    def test_same_destination_ranks_differently_for_different_travellers(self):
        pool = [_p("Sahastradhara", "waterfall", "curated", curated_rank=1),
                _p("Paltan Bazaar", "marketplace", "openstreetmap"),
                _p("Robber's Cave", "ruins", "curated", curated_rank=0)]
        nature = places.rank_places(pool, ["Nature", "Adventure"], min_keep=0)
        shopper = places.rank_places(pool, ["Shopping", "Food"], min_keep=0)
        assert nature[0]["name"] in {"Robber's Cave", "Sahastradhara"}
        assert shopper[0]["name"] == "Paltan Bazaar"

    def test_route_proximity_cannot_beat_quality(self):
        close_but_plain = _p("Ram Leela Bazar", "marketplace", lat=30.3166, lon=78.0323)
        far_but_great = _p("Robber's Cave", "ruins", "curated", lat=30.39, lon=78.1, curated_rank=0)
        ctx = {"anchor": (30.3165, 78.0322)}
        a = recommend.score_attraction(close_but_plain, [], None, ctx)["score"]
        b = recommend.score_attraction(far_but_great, [], None, ctx)["score"]
        assert b > a + 10

    def test_negative_filters_reject_non_tourist_places(self):
        for name in ["ISBT Bus Stand", "Dehradun Railway Station", "Max Super Speciality Hospital",
                     "Sabzi Mandi", "HP Petrol Pump", "District Collectorate", "Bank of Baroda",
                     "Industrial Area Phase 2"]:
            assert recommend.score_attraction(_p(name, "attraction"), []) is None, name

    def test_schools_and_colleges_only_survive_with_evidence(self):
        assert recommend.score_attraction(_p("Sunrise Public School", "attraction"), []) is None
        kept = recommend.score_attraction(_p("Doon School", "attraction", "wikipedia"), [])
        assert kept is not None

    def test_legit_names_are_not_caught_by_the_filters(self):
        for name in ["Government Museum", "Birla Mandir", "Municipal Garden", "Chandni Chowk",
                     "Clock Tower", "Lakshman Jhula"]:
            assert recommend.score_attraction(_p(name, "attraction", "wikipedia"), []) is not None, name

    def test_generic_restaurant_is_damped_unless_food_is_an_interest(self):
        spot = _p("Some Cafe", "cafe")
        plain = recommend.score_attraction(spot, ["Nature"])["score"]
        foodie = recommend.score_attraction(spot, ["Food"])["score"]
        assert foodie > plain

    def test_foursquare_rating_is_actually_used(self):
        base = _p("Hill Viewpoint", "viewpoint")
        rated = dict(base, fsq_rating=9.1, fsq_popularity=0.9)
        poor = dict(base, fsq_rating=1.0, fsq_popularity=0.0)
        s = lambda p: recommend.score_attraction(p, [])["score"]
        assert s(rated) > s(base) > s(poor)
        assert "Rated 9.1/10" in recommend.score_attraction(rated, [])["why"] or \
            "Rated 9.1/10" in " ".join(recommend.score_attraction(rated, [])["reasons"])

    def test_budget_fit_penalises_pricey_places_for_a_shoestring_trip(self):
        club = _p("Neon Nightclub", "nightclub", "ticketmaster")
        cheap = recommend.score_attraction(club, ["Nightlife"], None, {"spend_style": "shoestring"})
        rich = recommend.score_attraction(club, ["Nightlife"], None, {"spend_style": "comfort"})
        assert rich["parts"]["budget_fit"] > cheap["parts"]["budget_fit"]

    def test_rainy_forecast_favours_indoor_and_waterfalls(self):
        wet = {"rain_share": 0.8}
        museum = recommend.score_attraction(_p("City Museum", "museum"), [], None, wet)
        peak = recommend.score_attraction(_p("Tall Peak", "peak"), [], None, wet)
        falls = recommend.score_attraction(_p("Big Falls", "waterfall"), [], None, wet)
        assert museum["parts"]["season"] > peak["parts"]["season"]
        assert falls["parts"]["season"] == recommend.ATTRACTION_WEIGHTS["season"]

    def test_confidence_needs_evidence_not_just_a_score(self):
        strong = recommend.score_attraction(_p("Amer Fort", "fort", "curated", curated_rank=0),
                                            ["Culture & Heritage"])
        thin = recommend.score_attraction(_p("Odd Little Spot", "attraction"), [])
        assert strong["confidence"] == "high"
        assert thin["confidence"] == "low"

    def test_must_see_requires_evidence(self):
        pool = [_p("Amer Fort", "fort", "curated", curated_rank=0),
                _p("Unknown Fort", "fort", "openstreetmap")]
        ranked = {p["name"]: p for p in places.rank_places(pool, ["Culture & Heritage"], min_keep=0)}
        assert ranked["Amer Fort"]["must_see"] is True
        assert ranked["Unknown Fort"]["must_see"] is False

    def test_rerank_keeps_source_votes(self):
        votes = {places._normalize_name("Some Viewpoint"): 3}
        first = places.rank_places([_p("Some Viewpoint", "viewpoint")], [], votes)
        again = places.rerank_places(first, [], {"spend_style": "normal"})
        assert again[0]["recommendation"]["evidence"]["sources"] == 3


# ------------------------------------------------------------------ foursquare

def test_foursquare_search_asks_for_quality_fields_and_keeps_them(monkeypatch):
    monkeypatch.setattr(config, "FOURSQUARE_ENABLED", True)
    monkeypatch.setattr(config, "FOURSQUARE_API_KEY", "k")
    seen = {}

    async def fake_get_json(url, params=None, headers=None, **kw):
        seen["params"] = params
        return {"results": [{"name": "Lovely Falls", "categories": [{"name": "Waterfall"}],
                             "geocodes": {"main": {"latitude": 30.3, "longitude": 78.0}},
                             "rating": 8.8, "popularity": 0.93, "price": 1,
                             "stats": {"total_ratings": 412}}]}

    monkeypatch.setattr(webclient, "get_json", fake_get_json)
    out = asyncio.run(places._foursquare_search(30.3, 78.0, "tourist attraction", 10000, 10))
    assert "rating" in seen["params"]["fields"] and "popularity" in seen["params"]["fields"]
    assert out[0]["fsq_rating"] == 8.8 and out[0]["fsq_popularity"] == 0.93
    assert out[0]["fsq_ratings_count"] == 412


def test_foursquare_quality_survives_the_merge():
    osm = [_p("Lovely Falls", "waterfall", lat=30.3, lon=78.0)]
    fsq = [_p("Lovely Falls", "waterfall", "foursquare", lat=30.3001, lon=78.0001,
              fsq_rating=8.8, fsq_popularity=0.9)]
    merged = places._merge_sources(osm, fsq, limit=10)
    assert len(merged) == 1 and "fsq_rating" not in merged[0]
    places._attach_quality_signals(merged, fsq)
    assert merged[0]["fsq_rating"] == 8.8


# ----------------------------------------------------------------------- stays

class TestStayRanking:
    def pool(self):
        return [_stay("Backpackers Hostel", "hostel", 1, 700, tagged=False),
                _stay("Hotel Doon Palace", "hotel", 3, 2800, lat=30.318, lon=78.034,
                      sources=2, website="w"),
                _stay("Far Away Resort", "resort", 5, 9000, lat=30.45, lon=78.2, website="w"),
                _stay("Grand Central Hotel", "hotel", 4, 4300, lat=30.317, lon=78.033,
                      sources=2, website="w")]

    def test_cheapest_is_not_automatically_best(self):
        ranked = recommend.rank_stays(self.pool(), 5000, "balanced", (30.318, 78.034))
        assert ranked[0]["name"] != "Backpackers Hostel"
        assert ranked[0]["why"]

    def test_unaffordable_stay_is_never_the_pick(self):
        ranked = recommend.rank_stays(self.pool(), 3000, "comfort", (30.318, 78.034))
        assert ranked[0]["price_per_night_inr"] <= 3000
        far = next(s for s in ranked if s["name"] == "Far Away Resort")
        assert far["score"] <= 45 and far["recommendation"]["affordable"] is False

    def test_location_matters(self):
        near = _stay("Near Hotel", "hotel", 3, 2800, lat=30.318, lon=78.034)
        far = _stay("Far Hotel", "hotel", 3, 2800, lat=30.40, lon=78.20)
        ranked = recommend.rank_stays([far, near], 5000, "balanced", (30.318, 78.034))
        assert ranked[0]["name"] == "Near Hotel"

    def test_visitor_rating_beats_an_assumed_star_count(self):
        loved = _stay("Loved Inn", "hotel", 3, 2800, rating=9.0)
        meh = _stay("Meh Inn", "hotel", 3, 2800, rating=4.0)
        ranked = recommend.rank_stays([meh, loved], 5000, "balanced", None)
        assert ranked[0]["name"] == "Loved Inn"

    def test_travel_style_changes_the_winner(self):
        pool = self.pool()
        anchor = (30.318, 78.034)
        cheap = recommend.rank_stays(pool, 9500, "budget", anchor)[0]
        rich = recommend.rank_stays(pool, 9500, "comfort", anchor)[0]
        assert cheap["price_per_night_inr"] < rich["price_per_night_inr"]

    def test_input_list_is_not_mutated(self):
        pool = self.pool()
        recommend.rank_stays(pool, 5000, "balanced", None)
        assert all("recommendation" not in s for s in pool)

    def test_merge_counts_agreeing_sources(self):
        a = _stay("Hotel Pink City", "hotel", 3, 2800)
        b = dict(_stay("hotel pink-city", "hotel", 3, 2800), website="x.com", address="MI Road")
        merged = stays_tool._merge_stays([a], [b], limit=10)
        assert len(merged) == 1
        assert merged[0]["source_count"] == 2 and merged[0]["website"] == "x.com"

    def test_foursquare_rating_enriches_real_stays_only(self):
        mine = [_stay("Hotel Doon Palace", "hotel", 3, 2800)]
        rated = [{"name": "Hotel Doon Palace", "lat": 30.3166, "lon": 78.0323,
                  "rating": 8.2, "price_tier": 2, "ratings_count": 90},
                 {"name": "Invented Hotel", "lat": 30.3, "lon": 78.0, "rating": 9.9}]
        stays_tool._enrich_with_ratings(mine, rated, "Dehradun")
        assert mine[0]["rating"] == 8.2 and len(mine) == 1
        assert mine[0]["price_basis"]

    def test_find_stays_returns_ranked_list_with_reasons(self, monkeypatch):
        async def fake_live(*a, **k):
            return self.pool()
        monkeypatch.setattr(stays_tool, "_live_stays", fake_live)
        out = asyncio.run(stays_tool.find_stays(30.318, 78.034, "Dehradun", 2))
        assert all("why" in s for s in out)
        assert [s["score"] for s in out] == sorted((s["score"] for s in out), reverse=True)


# ------------------------------------------------------------------ the solver

TRAIN = {"carrier": "Train", "mode": "train", "total_inr": 900, "duration": "6h"}
FLIGHT = {"carrier": "Flight", "mode": "flight", "total_inr": 4200, "duration": "1h",
          "stops": "non-stop"}


class TestBudgetUse:
    def pool(self):
        return [_stay("Mid Guest House", "guest house", 2, 1400, tagged=False),
                _stay("Hotel Doon Palace", "hotel", 3, 2800, sources=2, website="w"),
                _stay("Grand Central Hotel", "hotel", 4, 4300, sources=2, website="w"),
                _stay("Luxe Heritage", "resort", 5, 7600, sources=2, website="w")]

    def solve(self, budget, style, **kw):
        return replan.solve(days=3, nights=2, travelers=2, budget_inr=budget,
                            transport_options=[TRAIN, FLIGHT], stay_options=self.pool(),
                            travel_style=style, anchor=(30.318, 78.034), **kw)

    def test_zero_stay_budget_picks_the_cheapest_not_the_first_listed(self):
        # Regression: a ceiling of 0 used to be treated as "no limit".
        pool = [_stay("Posh Hotel", "hotel", 5, 6000), _stay("Basic Room", "guest house", 2, 900)]
        assert replan._pick_stay(pool, 0, "comfort")["name"] == "Basic Room"

    def test_generous_budget_spends_more_for_comfort_than_for_budget_travel(self):
        cheap = self.solve(120000, "budget")
        rich = self.solve(120000, "comfort")
        assert cheap["stay"]["price_per_night_inr"] < rich["stay"]["price_per_night_inr"]
        assert cheap["budget"]["total_inr"] < rich["budget"]["total_inr"]

    def test_spare_money_is_spent_but_never_beyond_the_budget(self):
        result = self.solve(60000, "balanced")
        assert result["fits"] and result["budget"]["total_inr"] <= 60000
        assert result["upgrade_rounds"] >= 1
        assert result["attempts"][-1]["upgrade"] is True

    def test_budget_traveller_keeps_the_saving(self):
        result = self.solve(120000, "budget")
        assert result["upgrade_rounds"] == 0

    def test_upgrade_rounds_do_not_count_as_solving_rounds_or_savings(self):
        result = self.solve(60000, "balanced")
        assert result["rounds"] == 1
        assert replan.savings_summary(result["attempts"]) == ""

    def test_tight_budget_still_downgrades_and_fits(self):
        result = self.solve(14000, "comfort")
        assert result["fits"] and result["budget"]["total_inr"] <= 14000

    def test_alternatives_show_picks_and_an_honest_stretch(self):
        result = self.solve(26000, "balanced")
        view = replan.stay_alternatives(
            self.pool(), result["stay"], budget_inr=26000, transport_total=8400, days=3,
            nights=2, travelers=2, style=result["budget"]["style"], travel_style="balanced",
            anchor=(30.318, 78.034))
        assert view["picks"] and view["picks"][0]["price_per_night_inr"] <= view["ceiling_per_night_inr"]
        if view["stretch"]:
            assert view["stretch"]["stay"]["price_per_night_inr"] > view["ceiling_per_night_inr"]
            assert view["stretch"]["extra_total_inr"] > 0


# -------------------------------------------------------------------- the plan

def test_quality_places_are_chosen_before_geography():
    great = [dict(_p(f"Great {i}", "fort", lat=30.30 + i * 0.01, lon=78.00), score=85)
             for i in range(4)]
    weak = [dict(_p(f"Weak {i}", "marketplace", lat=30.30 + i * 0.01, lon=78.10), score=20)
            for i in range(10)]
    keep, reserve = shortlist_by_quality(weak + great, num_days=1)
    assert all(p["name"].startswith("Great") for p in keep)
    assert len(reserve) == 10


def test_unscored_places_are_left_alone_by_the_shortlist():
    pool = [_p("A", "fort"), _p("B", "fort")]
    keep, reserve = shortlist_by_quality(pool, 2)
    assert keep == pool and reserve == []


def test_day_plan_uses_the_strongest_places_and_lists_the_rest_as_ideas():
    strong = [dict(_p(f"Strong {i}", "fort", lat=30.30 + i * 0.002, lon=78.00), score=90 - i)
              for i in range(4)]
    weak = [dict(_p(f"Weak {i}", "marketplace", lat=30.30, lon=78.05 + i * 0.001), score=15)
            for i in range(6)]
    days = build_day_plan(weak + strong, [], ["2026-10-10"], 30.30, 78.00)
    names = [s["name"] for s in days[0]["stops"]]
    assert names and all(n.startswith("Strong") for n in names)


def test_summary_counts_confidence():
    days = [{"stops": [{"score": 80, "confidence": "high", "must_see": True, "name": "A"},
                       {"score": 30, "confidence": "low", "name": "B"}]}]
    summary = recommend.summarize_days(days)
    assert summary["stops"] == 2 and summary["high_confidence"] == 1
    assert summary["weak_stops"] == ["B"] and summary["must_see"] == 1


def test_critic_flags_a_day_of_weak_places():
    from agent import critic
    plan = {"days": [{"day_number": 1, "date": "2026-10-10", "stops": [
        {"name": "A", "score": 20, "confidence": "low"},
        {"name": "B", "score": 22, "confidence": "low"}]}]}
    issues = critic.rule_checks(plan, "", "Dehradun")
    assert any("lower-confidence" in i["issue"] for i in issues)


def test_full_plan_carries_scores_reasons_and_stay_alternatives(monkeypatch):
    """plan_trip end to end, offline: every pick must explain itself."""
    from datetime import date, timedelta

    from agent import loop
    from schemas import TripRequest
    from tools import geo, transport, weather

    async def geocode(city):
        return {"lat": 30.3165, "lon": 78.0322, "name": city}

    async def forecast(*a, **k):
        return {"source": "test", "days": [], "rainy_days": []}

    async def options(*a, **k):
        return [dict(TRAIN, source="test"), dict(FLIGHT, source="test")]

    async def find_stays(lat, lon, city, nights, *a, **k):
        return [_stay("Mid Guest House", "guest house", 2, 1400, tagged=False),
                _stay("Hotel Doon Palace", "hotel", 3, 2800, sources=2, website="w"),
                _stay("Grand Central Hotel", "hotel", 4, 4300, sources=2, website="w"),
                _stay("Luxe Heritage", "resort", 5, 7600, sources=2, website="w")]

    async def no_network(*a, **k):
        return None

    monkeypatch.setattr(geo, "geocode", geocode)
    monkeypatch.setattr(weather, "get_forecast", forecast)
    monkeypatch.setattr(transport, "all_transport_options", options)
    monkeypatch.setattr(stays_tool, "find_stays", find_stays)
    monkeypatch.setattr(webclient, "overpass", no_network)
    monkeypatch.setattr(webclient, "get_json", no_network)
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    monkeypatch.setattr(config, "STRICT_MODE", False)

    start = date.today() + timedelta(days=20)
    req = TripRequest(source_city="Delhi", destination_city="Dehradun", start_date=start,
                      end_date=start + timedelta(days=2), travelers=2, budget_inr=60000,
                      interests=["Nature", "Adventure"], travel_style="balanced")
    plan = asyncio.run(loop.plan_trip("t-reco", req))

    assert plan["ok"] and plan["fits"]
    assert plan["stay"]["why"] and plan["stay"]["score"] >= 0
    assert plan["stay_options"] and all("why" in s for s in plan["stay_options"])
    assert plan["recommendation_summary"]["stops"] > 0
    stops = [s for d in plan["days"] for s in d["stops"]]
    assert all("why" in s and 0 <= s["score"] <= 100 and s["confidence"] in
               {"high", "medium", "low"} for s in stops)
    assert not any("sabzi" in s["name"].lower() for s in stops)
    assert plan["budget"]["total_inr"] <= 60000
