"""
v24 regression tests: one test (or more) per problem found in the v23 review.

Every test here failed on v23. If one goes red, a recommendation or planning
bug that real travellers would notice has come back.
"""

import asyncio
from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import config
import dayplanner
import preferences
import recommend
from agent import events, replan
from agent import loop as agent_loop
from tools import places, stays, weather, webclient


def _p(name, kind, source="openstreetmap", lat=26.92, lon=75.82, **extra):
    return {"name": name, "kind": kind, "lat": lat, "lon": lon, "visit_hours": 1.5,
            "source": source, **extra}


JAIPUR = (26.9124, 75.7873)


# ---------------------------------------------------------------------------
# 1. Random places used to outrank real landmarks
# ---------------------------------------------------------------------------

class TestLandmarksBeatNoise:
    def osm(self):
        """What Overpass really returns: Amer Fort carries historic + wikidata tags."""
        return {"elements": [
            {"lat": 26.9855, "lon": 75.8513, "tags": {
                "name": "Amer Fort", "tourism": "attraction", "historic": "castle",
                "wikidata": "Q1060316", "wikipedia": "en:Amer Fort"}},
            {"lat": 26.915, "lon": 75.79, "tags": {"name": "Shri Ram Mandir",
                                                   "amenity": "place_of_worship"}},
            {"lat": 26.913, "lon": 75.788, "tags": {"name": "Sharma Tea Stall", "amenity": "cafe"}},
            {"lat": 26.914, "lon": 75.789, "tags": {"name": "Domino's Pizza",
                                                    "amenity": "restaurant"}},
        ]}

    def test_historic_tag_decides_the_kind_not_tourism_attraction(self):
        cleaned = {p["name"]: p for p in places._clean_results(self.osm(), 50)}
        assert cleaned["Amer Fort"]["kind"] in {"fort", "castle"}   # 10/10, not "attraction"
        assert cleaned["Amer Fort"]["wikidata"] == "Q1060316"

    def test_amer_fort_beats_a_neighbourhood_temple_and_food_is_not_a_stop(self):
        pool = places._clean_results(self.osm(), 50)
        ranked = places.rank_places(pool, ["Culture & Heritage", "Food"], {},
                                    ctx={"anchor": JAIPUR})
        names = [p["name"] for p in ranked]
        assert names[0] == "Amer Fort"
        assert "Domino's Pizza" not in names and "Sharma Tea Stall" not in names

    def test_interest_points_scale_with_quality(self):
        temple = recommend.score_attraction(_p("Local Mandir", "place of worship"),
                                            ["Culture & Heritage"])
        famous = recommend.score_attraction(_p("Amer Fort", "fort", wikidata="Q1", sitelinks=40),
                                            ["Culture & Heritage"])
        assert famous["parts"]["interest"] > temple["parts"]["interest"] + 4
        assert famous["score"] > temple["score"] + 15

    def test_wikidata_language_editions_are_a_popularity_signal(self):
        few = recommend.score_attraction(_p("Fort A", "fort", wikidata="Q1", sitelinks=1), [])
        many = recommend.score_attraction(_p("Fort B", "fort", wikidata="Q2", sitelinks=60), [])
        assert many["score"] > few["score"] + 8
        assert any("languages" in r for r in many["reasons"])

    def test_enrich_with_wikidata_counts_encyclopaedia_sitelinks(self, monkeypatch):
        async def fake(url, params=None, **kw):
            assert params["action"] == "wbgetentities"
            return {"entities": {"Q1": {"sitelinks": {"enwiki": {}, "hiwiki": {},
                                                      "commonswiki": {}, "enwikivoyage": {}}}}}
        monkeypatch.setattr(webclient, "get_json", fake)
        pool = [_p("X", "fort", wikidata="Q1")]
        asyncio.run(places.enrich_with_wikidata(pool))
        assert pool[0]["sitelinks"] == 2

    def test_overpass_query_only_takes_notable_temples_and_parks(self):
        query = places._build_query(26.9, 75.8, [("amenity", "place_of_worship"),
                                                 ("historic", "fort")], 20000, 400,
                                    places.NOTABLE_ONLY)
        assert '["amenity"~"^(place_of_worship)$"]["wikidata"]' in query
        assert 'nwr["historic"~"^(fort)$"](around' in query
        assert 'nwr["amenity"~"^(place_of_worship)$"](around' not in query


# ---------------------------------------------------------------------------
# 2. Name filters that dropped famous places / matched inside words
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["Umaid Bhawan Palace", "Cellular Jail", "Courtallam Falls",
                                  "Edward Memorial Park", "Parmarth Niketan", "Dhobi Ghat",
                                  "Block Printing Museum"])
def test_real_landmarks_survive_the_name_filters(name):
    assert places._fails_visitor_check(name) is False
    assert places._looks_worth_visiting(name) >= 1


@pytest.mark.parametrize("name", ["Dhobi Ghat", "Bhuri Singh Museum, Mandi",
                                  "South Park Street Cemetery"])
def test_sight_names_are_not_utility_places(name):
    assert recommend.score_attraction(_p(name, "attraction", "wikipedia"), []) is not None


def test_a_trip_to_mandi_does_not_filter_out_mandi():
    ctx = {"city": "Mandi"}
    assert recommend.score_attraction(_p("Mandi Rewalsar Lake", "lake"), [], None, ctx) is not None
    assert recommend.score_attraction(_p("Sabzi Mandi", "marketplace"), [], None, ctx) is None


def test_utility_places_are_still_dropped():
    for name in ["Bank of Baroda", "City Hospital", "ISBT Bus Stand", "Sabzi Mandi",
                 "City Parking"]:
        assert recommend.score_attraction(_p(name, "attraction"), []) is None, name


def test_interest_words_match_whole_words_only():
    assert recommend._interest_fraction(_p("Bara Imambara", "monument"), ["Nightlife"])[1] == []
    assert recommend._interest_fraction(_p("Campus Corner", "attraction"), ["Adventure"])[1] == []
    assert recommend._interest_fraction(_p("Skyline Bar", "attraction"), ["Nightlife"])[1] == ["Nightlife"]


def test_lakes_are_lakes_not_waterfalls():
    assert places._kind_from_title("Fateh Sagar Lake") == "lake"
    assert places._kind_from_title("Tehri Dam") == "lake"
    assert places._kind_from_title("Jim Corbett National Park") == "nature_reserve"


def test_wikipedia_description_with_district_does_not_drop_a_waterfall():
    assert places._looks_worth_visiting("Sahastradhara",
                                        description="Waterfall in Dehradun district") == 2


# ---------------------------------------------------------------------------
# 3. Duplicates and honest evidence
# ---------------------------------------------------------------------------

def test_same_place_under_three_names_is_merged():
    merged = places._merge_sources(
        [{"name": "Hawa Mahal", "lat": 26.9239, "lon": 75.8267}],
        [{"name": "Hawa Mahal, Jaipur", "lat": 26.9240, "lon": 75.8268, "wikidata": "Q1"}],
        [{"name": "Palace of Winds (Hawa Mahal)", "lat": 26.9239, "lon": 75.8266}], limit=10)
    assert len(merged) == 1 and merged[0]["wikidata"] == "Q1"


def test_near_identical_spellings_count_as_agreeing_sources():
    votes = places._source_votes([{"name": "Amer Fort", "lat": 26.9855, "lon": 75.8513}],
                                 [{"name": "Amber Fort", "lat": 26.9856, "lon": 75.8512}])
    assert votes[places._normalize_name("Amer Fort")] == 2


def test_hand_picked_list_is_not_called_an_independent_source():
    rec = recommend.score_attraction(_p("Hawa Mahal", "monument", "curated", curated_rank=1), [])
    assert not any("independent" in r for r in rec["reasons"])


def test_wikivoyage_listing_without_coordinates_is_flagged(monkeypatch):
    async def fake(url, params=None, **kw):
        if params.get("action") == "query":
            return {"query": {"pages": {"1": {"title": "Jaipur"}}}}
        return {"parse": {"wikitext": {"*": "{{see| name=Somewhere Nice | content=x}}"}}}
    monkeypatch.setattr(places.webclient, "get_json", fake)
    out = asyncio.run(places.wikivoyage_nearby(26.9, 75.8))
    assert out[0]["approx_location"] is True


def test_ticketmaster_asks_only_for_events_on_the_trip_dates(monkeypatch):
    monkeypatch.setattr(config, "TICKETMASTER_ENABLED", True)
    monkeypatch.setattr(config, "TICKETMASTER_API_KEY", "k")
    seen = {}

    async def fake(url, params=None, **kw):
        seen.update(params)
        return {"_embedded": {"events": [
            {"name": "Inside", "dates": {"start": {"localDate": "2026-12-02"}}},
            {"name": "Outside", "dates": {"start": {"localDate": "2026-12-20"}}}]}}
    monkeypatch.setattr(webclient, "get_json", fake)
    out = asyncio.run(places._ticketmaster_events(26.9, 75.8, 10, "2026-12-01", "2026-12-04"))
    assert seen["startDateTime"].startswith("2026-12-01")
    assert [e["name"].split(" (")[0] for e in out] == ["Inside"]


# ---------------------------------------------------------------------------
# 4. The day planner
# ---------------------------------------------------------------------------

def _agra_days(dates, windows=None):
    ranked = places.rank_places(places._curated_attractions("agra"), ["Culture & Heritage"], {},
                                ctx={"anchor": (27.1767, 78.0081)})
    return dayplanner.build_day_plan(ranked, [], dates, 27.1767, 78.0081, windows=windows)


def test_taj_mahal_is_never_planned_on_a_friday():
    for start in range(7):
        d0 = date(2026, 10, 19) + timedelta(days=start)
        dates = [(d0 + timedelta(days=i)).isoformat() for i in range(3)]
        for day in _agra_days(dates):
            names = [s["name"] for s in day["stops"]]
            if "Taj Mahal" in names:
                assert date.fromisoformat(day["date"]).weekday() != 4, day["date"]


def test_no_day_is_overstuffed_and_good_places_are_not_left_on_one_stop_days():
    pool = [dict(_p(f"Sight {i}", "fort", lat=26.90 + (i % 3) * 0.004, lon=75.80 + (i // 3) * 0.004),
                 score=80 - i, visit_hours=2.0) for i in range(9)]
    days = dayplanner.build_day_plan(pool, [], ["2026-11-02", "2026-11-03", "2026-11-04"],
                                     26.90, 75.80)
    for d in days:
        assert sum(s["visit_hours"] for s in d["stops"]) <= dayplanner.DAY_VISIT_CAPACITY + 0.01
        assert len(d["stops"]) >= 2


def test_kmeans_first_centre_is_a_real_point():
    pts = [{"lat": 10 + i, "lon": 70 + i * 3} for i in range(6)]
    clusters = dayplanner.kmeans(pts, 2)
    assert sum(len(c) for c in clusters) == 6


def test_evening_sights_go_last_and_morning_sights_first():
    pool = [_p("Sunset Point", "viewpoint", best_time="evening", lat=26.90, lon=75.80),
            _p("Old Fort", "fort", lat=26.95, lon=75.85),
            _p("Sunrise Temple", "place of worship", best_time="morning", lat=27.0, lon=75.9)]
    ordered = dayplanner.order_for_the_day(pool, 26.90, 75.80)
    assert ordered[0]["name"] == "Sunrise Temple" and ordered[-1]["name"] == "Sunset Point"


def test_arrival_day_starts_after_a_long_journey():
    bus = {"mode": "bus", "carrier": "Bus", "duration": "15h 00m"}
    short = {"mode": "train", "carrier": "Train", "duration": "4h 30m"}
    w_bus = dayplanner.day_windows(3, bus)
    w_short = dayplanner.day_windows(3, short)
    assert w_bus[0]["start_hour"] > 9.0 and w_bus[0]["travel_note"]
    assert w_short[0]["start_hour"] == pytest.approx(11.5)
    very_long = dayplanner.day_windows(3, {"mode": "train", "carrier": "T", "duration": "30h 00m"})
    assert very_long[0]["available_hours"] == 0.0


def test_lunch_is_near_and_does_not_repeat():
    stops = [_p("A", "fort", lat=26.90, lon=75.80)]
    rests = [{"name": "Good Thali", "lat": 26.901, "lon": 75.801, "food_score": 80},
             {"name": "Ok Cafe", "lat": 26.902, "lon": 75.801, "food_score": 50},
             {"name": "Far Place", "lat": 27.2, "lon": 76.1, "food_score": 99}]
    first = dayplanner.pick_lunch(rests, stops)
    second = dayplanner.pick_lunch(rests, stops, used={first["name"]})
    assert first["name"] == "Good Thali" and second["name"] == "Ok Cafe"
    far = dayplanner.pick_lunch([rests[2]], stops)
    assert far["is_far"] is True


def test_restaurant_ranking_prefers_written_up_places_and_honours_diet():
    ranked = places.rank_restaurants([
        {"name": "Domino's Pizza", "source": "openstreetmap"},
        {"name": "LMB", "source": "wikivoyage"},
        {"name": "Chicken Corner", "source": "openstreetmap"},
        {"name": "Shree Pure Veg Bhojanalaya", "source": "openstreetmap"}], diet="vegetarian")
    names = [r["name"] for r in ranked]
    assert names[0] == "LMB" and names[-1] == "Chicken Corner"


def test_wettest_day_is_not_flagged_when_every_day_is_dry():
    days = dayplanner.build_day_plan([_p("A", "fort")], [], ["2026-11-02", "2026-11-03"],
                                     26.9, 75.8, rain_chances={"2026-11-02": 8, "2026-11-03": 8})
    assert not any(d["is_wettest"] for d in days)


@pytest.mark.parametrize("text,closed", [("Tu-Su 09:00-17:00", [0]), ("Fr off", [4]),
                                         ("Mo-Th,Sa-Su 06:00-19:00", [4]), ("24/7", []),
                                         ("sunrise-sunset", [])])
def test_opening_hours_closed_days(text, closed):
    assert places.closed_days_from_opening_hours(text) == closed


# ---------------------------------------------------------------------------
# 5. Notes, group pricing, weather
# ---------------------------------------------------------------------------

def test_notes_become_rules():
    p = preferences.parse_rules("No early flights, we are vegetarian, one of us cannot walk far. "
                                "Must visit Nahargarh Fort")
    assert p["diet"] == "vegetarian" and p["max_stops_per_day"] == 3
    assert p["no_flights_before"] == 8.0 and "peak" in p["avoid_kinds"]
    assert p["must_include"] == ["Nahargarh Fort"] and p["understood"]


def test_non_veg_is_not_read_as_veg():
    assert preferences.parse_rules("we eat non veg, love biryani")["diet"] is None


def test_avoided_kinds_are_not_recommended_but_named_places_are_forced_in():
    ctx = {"avoid_kinds": ["place of worship"], "must_include": ["Galta Ji"]}
    assert recommend.score_attraction(_p("Birla Mandir", "place of worship", "wikipedia"),
                                      [], None, ctx) is None
    forced = recommend.score_attraction(_p("Galta Ji Temple", "place of worship"), [], None, ctx)
    assert forced is not None and forced["score"] >= 75


def test_transport_preferences_filter_options():
    opts = [{"mode": "flight", "depart": "06:10", "total_inr": 5000},
            {"mode": "flight", "depart": "13:45", "total_inr": 6000},
            {"mode": "train", "total_inr": 900}]
    kept, note = preferences.filter_transport(opts, {"no_flights_before": 8.0, "avoid_modes": []})
    assert [o.get("depart") for o in kept if o["mode"] == "flight"] == ["13:45"] and not note
    kept, _ = preferences.filter_transport(opts, {"avoid_modes": ["flight"]})
    assert all(o["mode"] != "flight" for o in kept)


def test_llm_preferences_are_clipped_to_known_values():
    clean = preferences._clean_llm({"diet": "keto", "avoid_kinds": ["place of worship", "aliens"],
                                    "max_stops_per_day": 40, "avoid_modes": ["teleport", "bus"]})
    assert clean["diet"] is None and clean["avoid_kinds"] == ["place of worship"]
    assert clean["max_stops_per_day"] is None and clean["avoid_modes"] == ["bus"]


def test_a_group_pays_for_enough_rooms_and_hostel_beds():
    priced = stays.price_for_group([
        {"name": "Hotel", "kind": "hotel", "price_per_night_inr": 2800, "total_inr": 5600},
        {"name": "Hostel", "kind": "hostel", "price_per_night_inr": 850, "total_inr": 1700}],
        adults=4, children=0)
    hotel, hostel = priced
    assert hotel["rooms"] == 2 and hotel["group_price_per_night_inr"] == 5600
    assert hostel["rooms"] == 4 and hostel["group_price_per_night_inr"] == 3400
    assert hotel["total_inr"] == 2800 * 2 * 2


def test_solver_budgets_the_group_price():
    pool = stays.price_for_group([{"name": "Hotel", "kind": "hotel", "price_per_night_inr": 3000,
                                   "total_inr": 6000, "lat": 26.9, "lon": 75.8, "stars": 3}], 4)
    result = replan.solve(days=3, nights=2, travelers=4, budget_inr=200000,
                          transport_options=[{"carrier": "T", "mode": "train", "total_inr": 2000}],
                          stay_options=pool)
    assert result["attempts"][0]["stay_inr"] == 3000 * 2 * 2


def test_seasonal_weather_knows_india_is_not_one_climate():
    july = date(2027, 7, 10)
    assert weather._seasonal_estimate(july, july, 34.16, 77.58)["days"][0]["is_rainy"] is False
    assert weather._seasonal_estimate(july, july, 15.5, 73.8)["days"][0]["is_rainy"] is True
    nov = date(2026, 11, 20)
    assert weather._seasonal_estimate(nov, nov, 13.08, 80.27)["days"][0]["is_rainy"] is True


# ---------------------------------------------------------------------------
# 6. The LLM's prose is checked against the plan
# ---------------------------------------------------------------------------

def test_prose_with_a_price_or_missing_stops_is_rejected():
    day = {"stops": [{"name": "Amer Fort"}, {"name": "Jal Mahal"}]}
    assert agent_loop._paragraph_is_faithful("Start at Amer Fort and end at Jal Mahal.", day)
    assert not agent_loop._paragraph_is_faithful("Amer Fort costs Rs 500 to enter.", day)
    assert not agent_loop._paragraph_is_faithful("A lovely day at the beach.", day)


def test_llm_cannot_change_the_trip_facts_in_tool_calls():
    from schemas import TripRequest
    start = date.today() + timedelta(days=5)
    req = TripRequest(source_city="Delhi", destination_city="Jaipur", start_date=start,
                      end_date=start + timedelta(days=4), budget_inr=40000,
                      interests=["Nature"])
    args = agent_loop._lock_args("find_attractions", {"city": "Goa", "interests": ["Food"]}, req)
    assert args == {"city": "Jaipur", "interests": ["Nature"], "num_days": 5}


# ---------------------------------------------------------------------------
# 7. Events and the API
# ---------------------------------------------------------------------------

def test_every_listener_gets_the_end_marker():
    async def run():
        events.open_stream("fan")
        a, b = events.subscribe("fan"), events.subscribe("fan")
        await events.emit("fan", "x", "hello")
        await events.close_stream("fan")
        late = events.subscribe("fan")
        return [q.get_nowait()["step"] for q in (a, a, b, b)] + [late.get_nowait()["step"]]
    assert asyncio.run(run()) == ["x", "__end__", "x", "__end__", "__end__"]


@pytest_asyncio.fixture
async def client():
    import main
    from database import init_db
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as c:
        yield c


def _trip(**over):
    start = date.today() + timedelta(days=12)
    body = {"source_city": "Delhi", "destination_city": "Agra", "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=2)).isoformat(), "adults": 2, "children": 1,
            "budget_inr": 40000, "interests": ["Culture & Heritage"], "notes": "we are vegetarian"}
    body.update(over)
    return body


async def _wait_done(client, trip_id):
    for _ in range(100):
        data = (await client.get(f"/api/trips/{trip_id}")).json()
        if data["status"] != "running":
            return data
        await asyncio.sleep(0.2)
    raise AssertionError("trip did not finish")


@pytest.mark.asyncio
async def test_finished_trip_stream_ends_immediately_every_time(client):
    created = (await client.post("/api/trips", json=_trip(),
                                 headers={"X-Client-Id": "owner-aaaa-1111"})).json()
    await _wait_done(client, created["id"])
    for _ in range(2):
        body = (await asyncio.wait_for(client.get(f"/api/trips/{created['id']}/stream"), 5)).text
        assert "__end__" in body


@pytest.mark.asyncio
async def test_trip_is_view_once_and_only_its_browser_can_delete_it(client):
    headers = {"X-Client-Id": "owner-bbbb-2222"}
    created = (await client.post("/api/trips", json=_trip(), headers=headers)).json()
    trip = await _wait_done(client, created["id"])
    assert trip["travelers"] == 3
    assert trip["plan"]["preferences"]["diet"] == "vegetarian"
    # transport is shown as the RETURN fare for the whole group
    transport = trip["plan"]["transport"]
    assert transport["return_total_inr"] == transport["total_inr"] * 2
    assert transport["travelers"] == 3
    denied = await client.delete(f"/api/trips/{created['id']}",
                                 headers={"X-Client-Id": "someone-else-99"})
    assert denied.status_code == 403
    # the browser forgets the trip once it has been viewed
    assert (await client.delete(f"/api/trips/{created['id']}", headers=headers)).status_code == 200
    assert (await client.get(f"/api/trips/{created['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_old_trips_are_purged(client):
    import monitor
    created = (await client.post("/api/trips", json=_trip(),
                                 headers={"X-Client-Id": "purge-test-4444"})).json()
    await _wait_done(client, created["id"])
    removed = await monitor.purge_old_trips(max_age_hours=-1)   # everything is "old"
    assert removed >= 1
    assert (await client.get(f"/api/trips/{created['id']}")).status_code == 404


def test_days_are_level_not_packed_then_empty():
    # six sights bunched in the old city, three spread out: v24.0 gave
    # one day 5-6 stops and another a single stop
    old_city = [dict(_p(f"Old City {i}", "monument", lat=26.920 + i * 0.002, lon=75.820),
                     score=85 - i, visit_hours=1.0) for i in range(6)]
    spread = [dict(_p(f"Outer {i}", "fort", lat=26.90 + i * 0.05, lon=75.90), score=70 - i,
                   visit_hours=1.5) for i in range(3)]
    days = dayplanner.build_day_plan(old_city + spread, [], ["2026-11-02", "2026-11-03",
                                                             "2026-11-04"], 26.92, 75.82)
    stops = [len(d["stops"]) for d in days]
    hours = [d["hours"] for d in days]
    assert min(stops) >= 2 and max(stops) <= dayplanner.DEFAULT_MAX_STOPS
    assert max(hours) - min(hours) <= 2.5


def test_no_day_gets_more_than_the_default_stop_cap():
    many = [dict(_p(f"Spot {i}", "viewpoint", lat=26.92 + i * 0.001, lon=75.82), score=80,
                 visit_hours=0.75) for i in range(20)]
    days = dayplanner.build_day_plan(many, [], ["2026-11-02", "2026-11-03"], 26.92, 75.82)
    assert all(len(d["stops"]) <= dayplanner.DEFAULT_MAX_STOPS for d in days)
    assert all(len(d["stops"]) >= 3 for d in days)


@pytest.mark.asyncio
async def test_past_start_date_is_rejected(client):
    past = date.today() - timedelta(days=10)
    body = _trip(start_date=past.isoformat(), end_date=(past + timedelta(days=2)).isoformat())
    assert (await client.post("/api/trips", json=body)).status_code == 422


@pytest.mark.asyncio
async def test_planning_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_TRIPS_PER_HOUR", 2)
    headers = {"X-Client-Id": "rate-limit-test-3333"}
    codes = [(await client.post("/api/trips", json=_trip(), headers=headers)).status_code
             for _ in range(3)]
    assert codes == [202, 202, 429]


# ---------------------------------------------------------------------------
# 8. The offline evaluation (python eval_recommendations.py) stays healthy
# ---------------------------------------------------------------------------

def test_offline_eval_precision_and_no_closed_day_stops():
    import eval_recommendations
    rows = eval_recommendations.evaluate_offline(3)
    mean_precision = sum(r[1] for r in rows) / len(rows)
    assert mean_precision >= 0.95
    assert sum(r[2] for r in rows) <= 2          # noise only as last-resort filler
    assert sum(r[3] for r in rows) == 0          # never on a closed day
