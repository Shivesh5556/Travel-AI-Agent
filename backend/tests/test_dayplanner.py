"""
Tests for the clustering and route ordering.

The nice thing about testing a geometry problem is that you can build inputs
where you already know the right answer: put three points in one corner and
three in another, and clustering had better not mix them.
"""

import dayplanner
from tools.geo import haversine_km


def place(name, lat, lon, kind="attraction", hours=1.5):
    return {"name": name, "lat": lat, "lon": lon, "kind": kind, "visit_hours": hours}


# Two obvious groups: one cluster near (26.90, 75.80), one near (27.10, 76.00)
NORTH = [place("N1", 26.900, 75.800), place("N2", 26.905, 75.805), place("N3", 26.898, 75.795)]
SOUTH = [place("S1", 27.100, 76.000), place("S2", 27.105, 76.005), place("S3", 27.098, 75.995)]


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(26.9, 75.8, 26.9, 75.8) == 0.0

    def test_known_distance_is_about_right(self):
        # Delhi to Mumbai is roughly 1150 km in a straight line
        distance = haversine_km(28.6139, 77.2090, 19.0760, 72.8777)
        assert 1100 < distance < 1200

    def test_it_is_symmetric(self):
        a = haversine_km(26.9, 75.8, 27.1, 76.0)
        b = haversine_km(27.1, 76.0, 26.9, 75.8)
        assert a == b


class TestKmeans:
    def test_it_separates_two_obvious_groups(self):
        clusters = dayplanner.kmeans(NORTH + SOUTH, k=2)
        assert len(clusters) == 2

        # each cluster should be all-north or all-south, never mixed
        for cluster in clusters:
            names = {p["name"][0] for p in cluster}
            assert len(names) == 1, f"cluster mixed north and south: {cluster}"

    def test_it_is_deterministic(self):
        first = dayplanner.kmeans(NORTH + SOUTH, k=2)
        second = dayplanner.kmeans(NORTH + SOUTH, k=2)
        assert [[p["name"] for p in c] for c in first] == [[p["name"] for p in c] for c in second]

    def test_it_keeps_every_point(self):
        points = NORTH + SOUTH
        clusters = dayplanner.kmeans(points, k=3)
        total = sum(len(c) for c in clusters)
        assert total == len(points)

    def test_more_days_than_places_does_not_crash(self):
        clusters = dayplanner.kmeans(NORTH, k=10)
        assert sum(len(c) for c in clusters) == len(NORTH)


class TestNearestNeighbour:
    def test_it_starts_with_the_closest_place(self):
        ordered = dayplanner.order_by_nearest_neighbour(NORTH + SOUTH, 26.900, 75.800)
        assert ordered[0]["name"] == "N1"

    def test_it_visits_everything_once(self):
        ordered = dayplanner.order_by_nearest_neighbour(NORTH + SOUTH, 26.9, 75.8)
        names = [p["name"] for p in ordered]
        assert sorted(names) == sorted([p["name"] for p in NORTH + SOUTH])
        assert len(names) == len(set(names))

    def test_it_beats_a_bad_order(self):
        """The whole reason this function exists: it should be shorter."""
        def route_length(points, start_lat, start_lon):
            total = 0.0
            lat, lon = start_lat, start_lon
            for p in points:
                total += haversine_km(lat, lon, p["lat"], p["lon"])
                lat, lon = p["lat"], p["lon"]
            return total

        # deliberately terrible order: north, south, north, south...
        bad = [NORTH[0], SOUTH[0], NORTH[1], SOUTH[1], NORTH[2], SOUTH[2]]
        good = dayplanner.order_by_nearest_neighbour(bad, 26.9, 75.8)

        assert route_length(good, 26.9, 75.8) < route_length(bad, 26.9, 75.8)

    def test_every_stop_records_its_leg(self):
        ordered = dayplanner.order_by_nearest_neighbour(NORTH, 26.9, 75.8)
        assert all("leg_km" in stop for stop in ordered)


class TestDayPlan:
    def test_one_day_per_date(self):
        days = dayplanner.build_day_plan(
            NORTH + SOUTH, [], ["2026-09-14", "2026-09-15"], 26.9, 75.8)
        assert len(days) == 2
        assert [d["day_number"] for d in days] == [1, 2]

    def test_no_attraction_appears_twice(self):
        days = dayplanner.build_day_plan(
            NORTH + SOUTH, [], ["2026-09-14", "2026-09-15"], 26.9, 75.8)
        seen = [s["name"] for d in days for s in d["stops"]] + \
               [n for d in days for n in d["extra_ideas"]]
        assert len(seen) == len(set(seen))

    def test_no_places_still_returns_the_right_days(self):
        days = dayplanner.build_day_plan([], [], ["2026-09-14", "2026-09-15"], 26.9, 75.8)
        assert len(days) == 2
        assert all(d["stops"] == [] for d in days)

    def test_indoor_days_go_on_rainy_dates(self):
        indoor = [place("Museum A", 26.900, 75.800, "museum"),
                  place("Museum B", 26.902, 75.802, "museum")]
        outdoor = [place("Peak A", 27.100, 76.000, "peak"),
                   place("Peak B", 27.102, 76.002, "peak")]

        days = dayplanner.build_day_plan(
            indoor + outdoor, [], ["2026-09-14", "2026-09-15"],
            26.9, 75.8, rainy_dates=["2026-09-15"])

        rainy_day = next(d for d in days if d["date"] == "2026-09-15")
        stop_kinds = {s["kind"] for s in rainy_day["stops"]}
        assert "museum" in stop_kinds, "the indoor group should move to the rainy day"

    def test_a_day_does_not_get_overstuffed(self):
        many = [place(f"P{i}", 26.9 + i * 0.001, 75.8, hours=3.0) for i in range(12)]
        days = dayplanner.build_day_plan(many, [], ["2026-09-14"], 26.9, 75.8)
        assert days[0]["hours"] <= dayplanner.MAX_HOURS_PER_DAY + 3.0
        assert len(days[0]["extra_ideas"]) > 0, "leftovers should become suggestions"

    def test_lunch_is_the_nearest_restaurant(self):
        restaurants = [place("Far Cafe", 28.0, 77.0, "restaurant"),
                       place("Near Dhaba", 26.901, 75.801, "restaurant")]
        days = dayplanner.build_day_plan(NORTH, restaurants, ["2026-09-14"], 26.9, 75.8)
        assert days[0]["lunch"]["name"] == "Near Dhaba"
