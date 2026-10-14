"""
Turning a pile of attractions into a sensible day-by-day plan.

This file contains NO AI at all, and that is the point. Deciding which places
to visit on which day is a geometry (and calendar) problem, not a language
problem. An LLM guessing at it will happily send you across the city and back
twice, or to the Taj Mahal on a Friday.

The algorithms, all written out by hand so you can follow them:

  1. k-means clustering
     Group the attractions so each day is one part of the city.

  2. capacity rebalancing  (v24)
     k-means does not care how much is in a group. One day could get seven
     hours of sights and the next a single stop. Overfull days hand their
     cheapest-to-move stop to a neighbouring day that has room.

  3. day assignment  (v24)
     Which group goes on which DATE is a small assignment problem: a group
     containing a place that is closed that weekday, a rainy date for an
     outdoor group, or a big group on a short arrival day all cost points.
     For trips up to 7 days every permutation is tried (7! = 5,040); longer
     trips use a greedy pass.

  4. nearest neighbour ordering, then time-of-day
     Inside one day, start at the hotel and always go to the closest place
     not yet seen (greedy TSP). Then sunrise points and morning-only sights
     move to the front, sunset views / aartis / the Wagah ceremony to the end.
"""

import itertools
import math
import random
from datetime import date as _date

from tools.geo import haversine_km

# Realistic upper limit on sightseeing in one day, in hours.
MAX_HOURS_PER_DAY = 7.0

# How many stops per day we plan around when choosing the shortlist.
STOPS_PER_DAY_TARGET = 4

# A day with fewer visit-hours than this looks empty; one with more than the
# capacity is overstuffed.
DAY_VISIT_CAPACITY = MAX_HOURS_PER_DAY - 1.0      # leave room for travel
COMFORTABLE_DAY_HOURS = 5.5

# Every full day should have roughly this much to see (visit hours), and never
# less than MIN_DAY_HOURS when there is anything left to add. No more than
# DEFAULT_MAX_STOPS stops a day unless the traveller asks for a packed trip.
TARGET_DAY_HOURS = 4.5
MIN_DAY_HOURS = 3.0
DEFAULT_MAX_STOPS = 5

# Day trips. A day whose places sit more than DAY_TRIP_KM (straight line) from
# the stay is an outing (Dehradun -> Mussoorie, Agra -> Fatehpur Sikri). The
# drive there and back is planned explicitly: real roads are ~1.35x the
# straight line and, with hills and traffic, average ~25 km/h.
DAY_TRIP_KM = 10.0
ROAD_FACTOR = 1.35
ROAD_KMH = 32.0          # out-of-town roads on the plains
# Hill roads wind: Dehradun -> Mussoorie is ~15 km as the crow flies but ~33 km
# and ~1h20m by road. Used for the hill and north-east regions.
HILL_ROAD_FACTOR = 2.1
HILL_ROAD_KMH = 23.0


# Levelling and topping-up only move a stop to a day whose places are within
# this distance, and never between an out-of-town outing and a city day - an
# even day is no good if it zig-zags across the region.
MAX_MOVE_KM = 12.0


# ...but a place this close to the day's own stops always fits that day: Jal
# Mahal is 3.6 km from Amer Fort on the same road, even though Amer is just
# past the "out of town" line and Jal Mahal is just inside it.
NEXT_DOOR_KM = 4.0


def _same_kind_of_day(place: dict, cluster: list[dict], base) -> bool:
    if not base or not cluster:
        return True
    place_out = haversine_km(base[0], base[1], place["lat"], place["lon"]) > DAY_TRIP_KM
    day_out = trip_distance(cluster, base) > DAY_TRIP_KM
    if place_out == day_out:
        return True
    centre = _centre(cluster)
    return centre is not None and \
        haversine_km(place["lat"], place["lon"], centre[0], centre[1]) <= NEXT_DOOR_KM

# A climb of more than this from the stay means a winding hill road.
HILL_CLIMB_M = 400


def road_params(base: tuple[float, float] | None, places: list[dict] | None = None,
                base_elevation: float | None = None) -> tuple[float, float]:
    """
    (detour factor, average speed km/h) for driving from the stay to `places`.

    With elevations (Open-Meteo, attached by the agent) the answer is
    physical: climbing more than HILL_CLIMB_M means a hill road. Without them,
    fall back to the region and treat only longer outings in hill regions as
    hill drives.
    """
    places = places or []
    heights = [p.get("elevation_m") for p in places if p.get("elevation_m") is not None]
    if base_elevation is not None and heights:
        hilly = max(heights) - base_elevation > HILL_CLIMB_M
    else:
        try:
            from tools.weather import region_for
            region = region_for(*base) if base else "plains"
        except Exception:
            region = "plains"
        far = trip_distance(places, base) if places and base else 0.0
        hilly = region in ("hills", "northeast", "ladakh") and far > 13.0
    return (HILL_ROAD_FACTOR, HILL_ROAD_KMH) if hilly else (ROAD_FACTOR, ROAD_KMH)

DAY_START_HOUR = 9.0
DAY_END_HOUR = 19.5          # sunset points and aartis run into the evening

# A lunch spot further than this from the day's stops is "far": the agent
# then searches for restaurants near that day specifically.
LUNCH_NEAR_KM = 2.5

# Which place kinds count as indoor. Used for the rainy-day swap.
INDOOR_KINDS = {"museum", "mall", "marketplace", "place of worship", "castle", "gallery",
                "restaurant", "palace"}

WEEKDAY_NAMES = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays",
                 "Saturdays", "Sundays"]


# ---------------------------------------------------------------------------
# Algorithm 1: k-means
# ---------------------------------------------------------------------------

def kmeans(points: list[dict], k: int, iterations: int = 25, seed: int = 42) -> list[list[dict]]:
    """
    Split points into k geographic clusters.

    How k-means works, in four steps:
      1. Pick k starting centres.
      2. Give every point to its nearest centre.
      3. Move each centre to the average position of its points.
      4. Repeat 2 and 3 until nothing changes.

    We fix the random seed so the same trip always produces the same plan.
    A demo that gives a different answer every refresh looks broken.
    """
    if k <= 1 or len(points) <= k:
        # Not enough points to cluster - one point per group.
        return [[p] for p in points] if len(points) <= k else [points]

    rng = random.Random(seed)

    # Step 1: k-means++ style start. Pick the first centre at random, then
    # keep picking the point that is furthest from the centres we have.
    # (v23 built the first centre from one point's latitude and ANOTHER
    # point's longitude - a location that does not exist.)
    first = points[rng.randrange(len(points))]
    centres = [(first["lat"], first["lon"])]
    while len(centres) < k:
        furthest_point = None
        furthest_distance = -1.0
        for p in points:
            nearest = min(haversine_km(p["lat"], p["lon"], c[0], c[1]) for c in centres)
            if nearest > furthest_distance:
                furthest_distance = nearest
                furthest_point = p
        centres.append((furthest_point["lat"], furthest_point["lon"]))

    clusters: list[list[dict]] = [[] for _ in range(k)]

    for _ in range(iterations):
        # Step 2: assign every point to the closest centre
        clusters = [[] for _ in range(k)]
        for p in points:
            distances = [haversine_km(p["lat"], p["lon"], c[0], c[1]) for c in centres]
            best = distances.index(min(distances))
            clusters[best].append(p)

        # Step 3: move the centres
        new_centres = []
        for i, cluster in enumerate(clusters):
            if not cluster:
                new_centres.append(centres[i])   # empty cluster, leave it alone
                continue
            avg_lat = sum(p["lat"] for p in cluster) / len(cluster)
            avg_lon = sum(p["lon"] for p in cluster) / len(cluster)
            new_centres.append((avg_lat, avg_lon))

        # Step 4: stop early if the centres stopped moving
        if new_centres == centres:
            break
        centres = new_centres

    # Lloyd's algorithm can converge with a cluster left empty. Steal the
    # point furthest from its own cluster's centre for each empty one, so
    # every day gets something whenever there are enough points.
    guard = 0
    while any(not c for c in clusters) and guard < k * 3:
        guard += 1
        empty_index = next(i for i, c in enumerate(clusters) if not c)
        donor_index = max(range(len(clusters)), key=lambda j: len(clusters[j]))
        if len(clusters[donor_index]) <= 1:
            break  # fewer points than clusters - nothing left to give
        donor = clusters[donor_index]
        d_lat = sum(p["lat"] for p in donor) / len(donor)
        d_lon = sum(p["lon"] for p in donor) / len(donor)
        donor.sort(key=lambda p: haversine_km(p["lat"], p["lon"], d_lat, d_lon))
        clusters[empty_index].append(donor.pop())

    return [c for c in clusters if c]


# ---------------------------------------------------------------------------
# Algorithm 2: capacity rebalancing
# ---------------------------------------------------------------------------

def _hours(cluster: list[dict]) -> float:
    return sum(p.get("visit_hours", 1.5) for p in cluster)


def _centre(cluster: list[dict]) -> tuple[float, float] | None:
    if not cluster:
        return None
    return (sum(p["lat"] for p in cluster) / len(cluster),
            sum(p["lon"] for p in cluster) / len(cluster))


def rebalance(clusters: list[list[dict]], capacity: float = DAY_VISIT_CAPACITY,
              max_moves: int = 60) -> list[list[dict]]:
    """
    Move stops out of overfull days into the neighbouring day that can take
    them most cheaply (smallest extra distance). Stops that fit nowhere stay
    put - the day trimmer turns them into "if you have time" ideas.
    """
    clusters = [list(c) for c in clusters]
    for _ in range(max_moves):
        over = [i for i, c in enumerate(clusters) if _hours(c) > capacity and len(c) > 1]
        if not over:
            break
        i = max(over, key=lambda j: _hours(clusters[j]))
        ci = _centre(clusters[i])
        best = None
        for p in clusters[i]:
            for j, other in enumerate(clusters):
                if j == i or _hours(other) + p.get("visit_hours", 1.5) > capacity:
                    continue
                cj = _centre(other)
                if cj is None:
                    cost = 5.0      # an empty day is a fine home for a spare stop
                else:
                    cost = (haversine_km(p["lat"], p["lon"], cj[0], cj[1])
                            - haversine_km(p["lat"], p["lon"], ci[0], ci[1]))
                # prefer moving weaker stops, keep the headline sights together
                cost += p.get("score", 50) / 50.0
                if best is None or cost < best[0]:
                    best = (cost, p, j)
        if best is None:
            break
        _, p, j = best
        clusters[i].remove(p)
        clusters[j].append(p)
    return clusters


# ---------------------------------------------------------------------------
# Algorithm 3: nearest neighbour + time of day
# ---------------------------------------------------------------------------

def order_by_nearest_neighbour(points: list[dict], start_lat: float, start_lon: float) -> list[dict]:
    """
    Put one day's stops in visiting order, starting from the hotel.

    Greedy: from where you are, go to the closest place you have not visited,
    repeat. Each stop gets a "leg_km" showing how far you travelled to reach it.
    """
    remaining = list(points)
    ordered = []
    current_lat, current_lon = start_lat, start_lon

    while remaining:
        distances = [haversine_km(current_lat, current_lon, p["lat"], p["lon"]) for p in remaining]
        nearest_index = distances.index(min(distances))

        stop = dict(remaining.pop(nearest_index))
        stop["leg_km"] = round(distances[nearest_index], 2)
        ordered.append(stop)

        current_lat, current_lon = stop["lat"], stop["lon"]

    return ordered


def _remeasure(stops: list[dict], start_lat: float, start_lon: float) -> list[dict]:
    lat, lon = start_lat, start_lon
    out = []
    for s in stops:
        s = dict(s)
        s["leg_km"] = round(haversine_km(lat, lon, s["lat"], s["lon"]), 2)
        lat, lon = s["lat"], s["lon"]
        out.append(s)
    return out


def order_for_the_day(points: list[dict], start_lat: float, start_lon: float) -> list[dict]:
    """
    Nearest-neighbour order, then respect time of day: a sunrise point or a
    "go early" fort first, a sunset view / evening aarti / the Wagah ceremony
    last. Legs are re-measured after any move so the km stay honest.
    """
    morning = [p for p in points if p.get("best_time") == "morning"]
    evening = [p for p in points if p.get("best_time") == "evening"]
    middle = [p for p in points if p.get("best_time") not in ("morning", "evening")]

    ordered = order_by_nearest_neighbour(morning, start_lat, start_lon) if morning else []
    last = ordered[-1] if ordered else {"lat": start_lat, "lon": start_lon}
    ordered += order_by_nearest_neighbour(middle, last["lat"], last["lon"]) if middle else []
    last = ordered[-1] if ordered else {"lat": start_lat, "lon": start_lon}
    if evening:
        # the most "evening" one (a sunset point) goes very last
        ordered += order_by_nearest_neighbour(evening, last["lat"], last["lon"])
    if not morning and not evening:
        return ordered
    return _remeasure(ordered, start_lat, start_lon)


# ---------------------------------------------------------------------------
# Arrival and departure days
# ---------------------------------------------------------------------------

def parse_duration_hours(duration: str | None) -> float | None:
    """'13h 20m' -> 13.33. None if unreadable."""
    if not duration:
        return None
    try:
        text = str(duration).lower().replace(" ", "")
        if "h" in text:
            h, _, rest = text.partition("h")
            m = rest.replace("m", "") or "0"
            return int(h) + int(m) / 60.0
        if text.endswith("m"):
            return int(text[:-1]) / 60.0
    except ValueError:
        return None
    return None


def _clock(text: str | None) -> float | None:
    try:
        h, m = str(text).split(":")[:2]
        return int(h) + int(m) / 60.0
    except (ValueError, AttributeError):
        return None


def fmt_clock(hours: float) -> str:
    hours = hours % 24
    h, m = int(hours), int(round((hours - int(hours)) * 60))
    if m == 60:
        h, m = h + 1, 0
    suffix = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {suffix}"


def day_windows(num_days: int, transport: dict | None) -> list[dict]:
    """
    How much of each day is actually free for sightseeing, given the journey.

    v23 planned Day 1 as a full 9-to-5 day even after a 15-hour bus. Now:
      - short trips by road/rail (<= 9h): leave at 6 AM, sightseeing starts an
        hour after arrival
      - long trips by road/rail: an overnight journey, leaving around 8 PM the
        evening before, arriving the next morning (how most Indians do it)
      - flights: depart at the offer's time (or 8 AM), +1.5h to get into town
      - the last day ends in time to make the journey home
    Every number here is an estimate and the notes say so.
    """
    windows = [{"start_hour": DAY_START_HOUR, "available_hours": MAX_HOURS_PER_DAY,
                "travel_note": ""} for _ in range(num_days)]
    if not transport or num_days <= 0:
        return windows
    hours = parse_duration_hours(transport.get("duration"))
    if hours is None:
        return windows
    mode = transport.get("mode", "train")
    label = transport.get("carrier") or mode

    # ---- arrival (day 1) ----
    if mode == "flight":
        dep = _clock(transport.get("depart")) or 8.0
        arrive = dep + hours + 1.5
        how = f"Fly in ({label})"
    elif hours <= 9:
        arrive = 6.0 + hours
        how = f"Leave around 6 AM by {label}"
    else:
        arrive = 20.0 + hours - 24.0          # overnight, left the evening before
        how = f"Overnight {mode}: leave around 8 PM the evening before"
    first = windows[0]
    if arrive >= 24:
        first["start_hour"], first["available_hours"] = DAY_END_HOUR, 0.0
        first["travel_note"] = (f"{how}; the journey takes about {transport.get('duration')}, "
                                f"so Day 1 is a travel day (estimate).")
    else:
        start = max(DAY_START_HOUR, arrive + 1.0)
        first["start_hour"] = round(start, 2)
        first["available_hours"] = round(max(0.0, min(MAX_HOURS_PER_DAY, DAY_END_HOUR - start)), 1)
        if start > DAY_START_HOUR or mode != "flight":
            first["travel_note"] = (f"{how}, arriving around {fmt_clock(arrive)} "
                                    f"(estimate). Sightseeing is planned from {fmt_clock(start)}.")

    # ---- departure (last day) ----
    if mode == "flight":
        leave_by = 22.0 - hours - 2.5          # be at the airport early
    elif hours <= 9:
        leave_by = 22.0 - hours
    else:
        leave_by = 20.0                         # overnight back
    last = windows[-1]
    end = min(DAY_END_HOUR, leave_by)
    avail = max(0.0, min(last["available_hours"], end - last["start_hour"]))
    if avail < last["available_hours"] - 0.25:
        last["available_hours"] = round(avail, 1)
        note = f"Head back by {fmt_clock(leave_by)} for the {transport.get('duration')} journey home."
        last["travel_note"] = (last["travel_note"] + " " + note).strip()
    elif hours > 9 and mode != "flight":
        note = f"Overnight {mode} home around {fmt_clock(leave_by)}."
        last["travel_note"] = (last["travel_note"] + " " + note).strip()
    return windows


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

def indoor_score(cluster: list[dict]) -> float:
    """What fraction of this group of places is indoors? 0.0 to 1.0."""
    if not cluster:
        return 0.0
    indoor = sum(1 for p in cluster if p.get("kind", "") in INDOOR_KINDS)
    return indoor / len(cluster)


def _cluster_quality(cluster: list[dict]) -> float:
    """Average worth-visiting score of a group of places (0 when unscored)."""
    if not cluster:
        return 0.0
    return sum(p.get("score", 0) for p in cluster) / len(cluster)


def _weekday(iso: str) -> int | None:
    try:
        return _date.fromisoformat(iso).weekday()
    except (TypeError, ValueError):
        return None


def _closed_on(place: dict, iso: str) -> bool:
    wd = _weekday(iso)
    return wd is not None and wd in (place.get("closed_days") or ())


def _pick_best_for_day(cluster: list[dict], budget: float = DAY_VISIT_CAPACITY,
                       max_stops: int | None = None) -> tuple[list[dict], list[dict]]:
    """
    Fill one day with the highest-scoring places that fit in the time
    available. Returns (chosen, left_out). Places with no score keep their
    original order, so behaviour is unchanged when nothing has been ranked.
    """
    chosen, left_out, used = [], [], 0.0
    for place in sorted(cluster, key=lambda p: p.get("score", 0), reverse=True):
        hours = place.get("visit_hours", 1.5)
        fits_time = (not chosen and hours <= budget + 1.0) or used + hours <= budget
        fits_count = max_stops is None or len(chosen) < max_stops
        if fits_time and fits_count and budget > 0:
            chosen.append(place)
            used += hours
        else:
            left_out.append(place)
    return chosen, left_out


def trim_to_day_length(stops: list[dict], max_hours: float = MAX_HOURS_PER_DAY) -> tuple[list[dict], list[dict]]:
    """
    Keep adding stops until the day is full (sightseeing plus travel time
    between stops). Everything else becomes an "if you have time" suggestion
    rather than being silently thrown away.
    """
    kept, extra = [], []
    hours_used = 0.0

    for stop in stops:
        visit = stop.get("visit_hours", 1.5)
        # assume 20 km/h average in an Indian city, traffic included
        travel = stop.get("leg_km", 0) / 20.0

        if hours_used + visit + travel <= max_hours or (not kept and max_hours >= 1.0):
            kept.append(stop)
            hours_used += visit + travel
        else:
            extra.append(stop)

    return kept, extra


def shortlist_by_quality(attractions: list[dict], num_days: int) -> tuple[list[dict], list[dict]]:
    """
    Quality first, geography second.

    The best places are chosen first (by the recommendation score), and only
    those are clustered and routed. The rest become "if you have time" ideas.
    Unscored places keep their order.
    """
    if not attractions or not any("score" in p for p in attractions):
        return list(attractions), []
    ranked = sorted(attractions, key=lambda p: p.get("score", 0), reverse=True)
    keep = max(num_days * STOPS_PER_DAY_TARGET, num_days * 2)
    return ranked[:keep], ranked[keep:]


_BASE_ELEVATION: dict[tuple, float | None] = {}


def set_base_elevation(base: tuple[float, float], elevation: float | None) -> None:
    """The stay's height, so road_params can compare it with the places."""
    _BASE_ELEVATION[(round(base[0], 4), round(base[1], 4))] = elevation


def _base_elev(base):
    return _BASE_ELEVATION.get((round(base[0], 4), round(base[1], 4))) if base else None


def road_km(straight_km: float, base: tuple[float, float] | None = None,
            places: list[dict] | None = None) -> float:
    factor, _ = road_params(base, places, _base_elev(base))
    return straight_km * factor


def drive_hours(straight_km: float, base: tuple[float, float] | None = None,
                places: list[dict] | None = None) -> float:
    """One-way driving time for a straight-line distance."""
    factor, speed = road_params(base, places, _base_elev(base))
    return straight_km * factor / speed


def trip_distance(cluster: list[dict], base: tuple[float, float] | None) -> float:
    """Straight-line km from the stay to the middle of a day's places."""
    centre = _centre(cluster)
    if not base or not centre:
        return 0.0
    return haversine_km(base[0], base[1], centre[0], centre[1])


def _assignment_cost(cluster: list[dict], date_iso: str, capacity: float, is_rainy: bool,
                     any_rain: bool, position: int, base: tuple[float, float] | None = None,
                     short_day: bool = False) -> float:
    cost = 0.0
    # A long drive does not belong on an arrival or departure day.
    far = trip_distance(cluster, base)
    if short_day and far > DAY_TRIP_KM:
        cost += 40.0 + 4.0 * drive_hours(far, base, cluster)
    for p in cluster:
        if _closed_on(p, date_iso):
            cost += 100.0 + p.get("score", 50)
    over = _hours(cluster) - capacity
    if over > 0:
        cost += over * 12.0
    if any_rain:
        indoor = indoor_score(cluster)
        cost += (1.0 - indoor) * 8.0 if is_rainy else indoor * 2.0
    # Day 1 should be the strongest day when nothing else matters.
    cost += position * _cluster_quality(cluster) / 400.0
    return cost


def assign_to_dates(clusters: list[list[dict]], dates: list[str], capacities: list[float],
                    rainy_dates: list[str], base: tuple[float, float] | None = None,
                    short_days: set[int] | None = None) -> list[list[dict]]:
    """Choose which group of places goes on which date (see module docstring)."""
    n = len(dates)
    short_days = short_days or set()
    clusters = list(clusters) + [[] for _ in range(n - len(clusters))]
    clusters = clusters[:n]
    any_rain = bool(rainy_dates)
    cost = [[_assignment_cost(c, dates[d], capacities[d], dates[d] in rainy_dates, any_rain, d,
                              base, d in short_days)
             for d in range(n)] for c in clusters]

    if n <= 7:
        best_perm, best_cost = None, math.inf
        for perm in itertools.permutations(range(n)):      # perm[cluster] = date index
            total = sum(cost[c][perm[c]] for c in range(n))
            if total < best_cost - 1e-9:
                best_perm, best_cost = perm, total
        assigned = [[] for _ in range(n)]
        for c, d in enumerate(best_perm):
            assigned[d] = clusters[c]
        return assigned

    # Longer trips: strongest groups choose first.
    order = sorted(range(n), key=lambda c: -_cluster_quality(clusters[c]))
    free = set(range(n))
    assigned = [[] for _ in range(n)]
    for c in order:
        d = min(free, key=lambda j: (cost[c][j], j))
        assigned[d] = clusters[c]
        free.remove(d)
    return assigned


def _fix_closed_days(assigned: list[list[dict]], dates: list[str], capacities: list[float]) -> list[str]:
    """A stop still sitting on its closed day moves to the nearest open day with room.
    Returns human-readable notes about what moved / could not be placed."""
    notes = []
    for d, cluster in enumerate(assigned):
        for p in [p for p in cluster if _closed_on(p, dates[d])]:
            closed_name = WEEKDAY_NAMES[_weekday(dates[d])]
            options = []
            for j, other in enumerate(assigned):
                if j == d or _closed_on(p, dates[j]) or capacities[j] < 1.0:
                    continue
                if _hours(other) + p.get("visit_hours", 1.5) > max(capacities[j] - 0.5, 1.0):
                    continue
                c = _centre(other)
                dist = 0.0 if c is None else haversine_km(p["lat"], p["lon"], c[0], c[1])
                options.append((dist, j))
            cluster.remove(p)
            if options:
                _, j = min(options)
                assigned[j].append(p)
                notes.append(f"{p['name']} is closed on {closed_name}, so it is on Day {j + 1} instead.")
            else:
                p["closed_note"] = f"Closed on {closed_name}"
                notes.append(f"{p['name']} is closed on {closed_name} and no other day had room.")
    return notes


def _day_hours(cluster: list[dict], base: tuple[float, float] | None = None) -> float:
    """Visit time plus the drive out to the area and back (20 km/h, x1.3 for
    real roads). A Nandi Hills day is 3h at the hill PLUS ~4h on the road."""
    hours = _hours(cluster)
    centre = _centre(cluster)
    if base and centre:
        hours += 2 * drive_hours(haversine_km(base[0], base[1], centre[0], centre[1]), base, cluster)
    return hours


def _load(cluster: list[dict], capacity: float, base: tuple[float, float] | None = None) -> float:
    return _day_hours(cluster, base) / max(capacity, 0.5)


def even_out(assigned: list[list[dict]], dates: list[str], capacities: list[float],
             active: list[int], max_stops: int = 5, max_moves: int = 80,
             base: tuple[float, float] | None = None) -> None:
    """
    Level the days. Repeatedly move one stop from the fullest day (relative to
    how much time that day has) to the emptiest one, choosing the stop that
    sits closest to the emptier day's places. Stops only move to a day they
    are open on, and only when the move actually narrows the gap.
    In place.
    """
    if len(active) < 2:
        return
    for _ in range(max_moves):
        loads = {i: _load(assigned[i], capacities[i], base) for i in active}
        # a one-stop day (a day trip) cannot give anything away - level the rest
        donors = [i for i in active if len(assigned[i]) > 1]
        if not donors:
            break
        hi = max(donors, key=lambda i: (loads[i], len(assigned[i])))
        lo = min(active, key=lambda i: (loads[i], len(assigned[i])))
        gap_hours = _day_hours(assigned[hi], base) - _day_hours(assigned[lo], base)
        if hi == lo or loads[hi] <= loads[lo]:
            break
        if loads[hi] - loads[lo] < 0.2 and abs(len(assigned[hi]) - len(assigned[lo])) <= 1:
            break
        target = _centre(assigned[lo])
        best = None
        for p in assigned[hi]:
            h = p.get("visit_hours", 1.5)
            if p.get("only_on") or _closed_on(p, dates[lo]):
                continue
            if _hours(assigned[lo]) + h > capacities[lo] or len(assigned[lo]) >= max_stops:
                continue
            new_hi = _load([q for q in assigned[hi] if q is not p], capacities[hi], base)
            new_lo = _load(assigned[lo] + [p], capacities[lo], base)
            old_spread = loads[hi] - loads[lo]
            if max(new_hi, new_lo) - min(new_hi, new_lo) >= old_spread - 1e-6 \
                    and gap_hours < 1.0:
                continue
            if max(new_hi, new_lo) > max(loads[hi], loads[lo]) + 1e-6:
                continue
            dist = 0.0 if target is None else haversine_km(p["lat"], p["lon"], target[0], target[1])
            # Levelling must not wreck geography: never send a Mussoorie stop
            # to a Dehradun city day (or a city temple up the hill).
            if dist > MAX_MOVE_KM or not _same_kind_of_day(p, assigned[lo], base):
                continue
            # prefer a nearby, not-the-headline stop
            cost = dist / 5.0 + p.get("score", 50) / 100.0
            if best is None or cost < best[0]:
                best = (cost, p)
        if best is None:
            break
        assigned[hi].remove(best[1])
        assigned[lo].append(best[1])


def fill_light_days(assigned: list[list[dict]], reserve: list[dict], dates: list[str],
                    capacities: list[float], active: list[int],
                    max_stops: int = 5, base: tuple[float, float] | None = None) -> list[dict]:
    """A day still below the comfortable minimum takes the best reserve places
    that are open that day, nearest first. Returns what is left of the reserve."""
    reserve = list(reserve)
    for i in active:
        floor = min(capacities[i], MIN_DAY_HOURS)
        while _hours(assigned[i]) < floor and len(assigned[i]) < max_stops and reserve:
            centre = _centre(assigned[i])
            options = [p for p in reserve if not _closed_on(p, dates[i])
                       and _hours(assigned[i]) + p.get("visit_hours", 1.5) <= capacities[i] + 0.5
                       and (centre is None or haversine_km(p["lat"], p["lon"], centre[0],
                                                           centre[1]) <= MAX_MOVE_KM)
                       and _same_kind_of_day(p, assigned[i], base)]
            if not options:
                break
            pick = min(options, key=lambda p: (
                (0.0 if centre is None else haversine_km(p["lat"], p["lon"], centre[0], centre[1])) / 5.0
                - p.get("score", 0) / 20.0))
            reserve.remove(pick)
            assigned[i].append(pick)
    return reserve


# Days with less sightseeing time than this (a late arrival, an early
# departure, an acclimatisation day) are not forced to match the others.
LEVEL_MIN_HOURS = 4.0


def level_stop_counts(day_stops: list[list[dict]], dates: list[str], avail: list[float],
                      pool: list[dict], hotel: tuple[float, float],
                      max_stops: int = DEFAULT_MAX_STOPS, max_rounds: int = 120) -> set[int]:
    """
    Make every sightseeing day have the SAME number of activities (at most one
    apart when the total does not divide evenly). In place; returns the days
    that changed.

    Each round looks at the day with the fewest stops and the day with the
    most, and tries, in order:
      1. add the best spare place near the light day (open that date, same
         kind of day - never a city temple on the Mussoorie outing - and the
         day still fits its hours, drive included)
      2. move a stop from the busy day to the light day (same rules)
      3. drop the busy day's weakest stop (it becomes an "if you have time" idea)
    Step 3 always shrinks the gap, so this always finishes.
    """
    idx = [i for i in range(len(day_stops)) if avail[i] >= LEVEL_MIN_HOURS]
    changed: set[int] = set()
    if len(idx) < 2:
        return changed
    used = {p["name"] for stops in day_stops for p in stops}

    def fits(i: int, stops: list[dict]) -> bool:
        ordered = order_for_the_day(stops, *hotel)
        return day_metrics(ordered, *hotel)["hours"] <= avail[i] + 0.5

    def suitable(p: dict, i: int) -> bool:
        if _closed_on(p, dates[i]) or p.get("only_on") and p["only_on"] != dates[i]:
            return False
        centre = _centre(day_stops[i]) or hotel
        if haversine_km(p["lat"], p["lon"], centre[0], centre[1]) > MAX_MOVE_KM:
            return False
        return _same_kind_of_day(p, day_stops[i], hotel)

    def try_raise(lo: int, counts: dict) -> bool:
        """Give the light day one more stop: a spare place, or one moved from
        any day that can spare it without becoming the new light day."""
        if counts[lo] >= max_stops:
            return False
        spare = sorted((p for p in pool if p["name"] not in used and suitable(p, lo)),
                       key=lambda p: -p.get("score", 0))
        pick = next((p for p in spare if fits(lo, day_stops[lo] + [p])), None)
        if pick is not None:
            pool.remove(pick)
            day_stops[lo].append(pick)
            used.add(pick["name"])
            changed.add(lo)
            return True
        donors = sorted((i for i in idx if counts[i] >= counts[lo] + 2),
                        key=lambda i: -counts[i])
        for donor in donors:
            movable = sorted(day_stops[donor],
                             key=lambda p: (bool(p.get("must_see")), p.get("score", 0)))
            for p in movable:
                if suitable(p, lo) and fits(lo, day_stops[lo] + [p]):
                    day_stops[donor].remove(p)
                    day_stops[lo].append(p)
                    changed.update((donor, lo))
                    return True
        return False

    for _ in range(max_rounds):
        counts = {i: len(day_stops[i]) for i in idx}
        if max(counts.values()) - min(counts.values()) <= 1:
            break
        # 1 + 2: raise ANY of the lightest days if possible (fill before trimming)
        light = sorted((i for i in idx if counts[i] <= max(counts.values()) - 2),
                       key=lambda i: (counts[i], _hours(day_stops[i])))
        if any(try_raise(lo, counts) for lo in light):
            continue
        # 3. nothing can be added anywhere: trim the busiest day's weakest stop
        hi = max(idx, key=lambda i: (counts[i], _hours(day_stops[i])))
        weakest = min(day_stops[hi], key=lambda p: (bool(p.get("must_see")), p.get("score", 0)))
        day_stops[hi].remove(weakest)
        pool.insert(0, weakest)
        used.discard(weakest["name"])
        changed.add(hi)

    # 4. top up: the counts are now within one, but a city with 12 good places
    #    should not end up with 1-2 stops a day while places sit unused. Give the
    #    LIGHTEST day the best spare place nearby while the day stays comfortable,
    #    then the next lightest... Only a day at the current minimum is raised, so
    #    the days never drift more than one apart.
    def comfy(i: int, stops: list[dict]) -> bool:
        limit = min(avail[i], COMFORTABLE_DAY_HOURS)
        ordered = order_for_the_day(stops, *hotel)
        return day_metrics(ordered, *hotel)["hours"] <= limit + 0.5

    for _ in range(max_rounds):
        counts = {i: len(day_stops[i]) for i in idx}
        low = min(counts.values())
        if low >= max_stops:
            break
        raised = False
        for lo in sorted((i for i in idx if counts[i] == low),
                         key=lambda i: _hours(day_stops[i])):
            spare = sorted((p for p in pool if p["name"] not in used and suitable(p, lo)),
                           key=lambda p: -p.get("score", 0))
            pick = next((p for p in spare if comfy(lo, day_stops[lo] + [p])), None)
            if pick is not None:
                pool.remove(pick)
                day_stops[lo].append(pick)
                used.add(pick["name"])
                changed.add(lo)
                raised = True
                break
        if not raised:
            break

    for i in changed:
        day_stops[i][:] = order_for_the_day(day_stops[i], *hotel)
    return changed


def build_day_plan(attractions: list[dict], restaurants: list[dict], dates: list[str],
                   hotel_lat: float, hotel_lon: float, rainy_dates: list[str] | None = None,
                   rain_chances: dict[str, int] | None = None,
                   windows: list[dict] | None = None,
                   max_stops_per_day: int | None = None) -> list[dict]:
    """
    The main function. Give it places and dates, get back a real itinerary.

    windows: per-day {"start_hour", "available_hours", "travel_note"} from
             day_windows() - arrival and departure days are shorter.
    max_stops_per_day: from the traveller's notes ("can't walk far", "slow pace").

    Returns a list like:
      [{"date": "2026-09-14", "day_number": 1, "stops": [...], "lunch": {...},
        "total_km": 12.4, "hours": 6.2, "is_rainy": False, "extra_ideas": [...],
        "start_hour": 9.0, "available_hours": 7.0, "travel_note": "", "notes": [...]}]
    """
    rainy_dates = rainy_dates or []
    rain_chances = rain_chances or {}
    num_days = len(dates)
    windows = windows or [{"start_hour": DAY_START_HOUR, "available_hours": MAX_HOURS_PER_DAY,
                           "travel_note": ""} for _ in dates]
    capacities = [max(0.0, w["available_hours"] - 1.0) if w["available_hours"] >= MAX_HOURS_PER_DAY
                  else max(0.0, w["available_hours"] - 0.5) for w in windows]

    # Which single day is worst? Only that one gets called out in the text.
    # Only when it is actually likely to rain, and that day stands out - v23
    # flagged "wettest day" even when every day had the same 8% chance.
    wettest = None
    if rain_chances:
        rated = [(d, c) for d, c in rain_chances.items() if c is not None and d in dates]
        if rated:
            day, chance = max(rated, key=lambda row: row[1])
            others = [c for d, c in rated if d != day]
            if chance >= 40 and (not others or chance > min(others)):
                wettest = day

    def _empty_day(i, d):
        w = windows[i]
        return {"date": d, "day_number": i + 1, "stops": [], "lunch": None,
                "total_km": 0, "hours": 0, "is_rainy": d in rainy_dates,
                "rain_chance": rain_chances.get(d), "is_wettest": d == wettest,
                "indoor_share": 0, "extra_ideas": [], "start_hour": w["start_hour"],
                "available_hours": w["available_hours"], "travel_note": w["travel_note"],
                "travel_day": w["available_hours"] < 1.0, "notes": []}

    if not attractions:
        return [_empty_day(i, d) for i, d in enumerate(dates)]

    # Dated events can only go on their own date; drop those outside the trip.
    pinned: dict[int, list[dict]] = {}
    pool = []
    for p in attractions:
        on = p.get("only_on")
        if on:
            if on in dates:
                pinned.setdefault(dates.index(on), []).append(p)
            continue
        pool.append(p)

    max_stops = max_stops_per_day or DEFAULT_MAX_STOPS

    # Step 0: choose the places worth the time, THEN group them. Make sure the
    # shortlist holds enough hours to give EVERY day a proper programme.
    active = [i for i in range(num_days) if capacities[i] >= 1.0]
    shortlist, reserve = shortlist_by_quality(pool, max(1, len(active)))
    wanted_hours = sum(min(capacities[i], TARGET_DAY_HOURS) for i in active)
    while reserve and _hours(shortlist) < wanted_hours \
            and len(shortlist) < max(1, len(active)) * max_stops:
        shortlist.append(reserve.pop(0))

    # Step 1: group the attractions geographically - one group per day.
    clusters = kmeans(shortlist, max(1, len(active))) if shortlist else []

    # Step 2: no overstuffed days
    clusters = rebalance(clusters, capacity=max([capacities[i] for i in active] or [DAY_VISIT_CAPACITY]))

    # Step 3: which group goes on which date (closed days, rain, short days)
    if active:
        sub_dates = [dates[i] for i in active]
        sub_caps = [capacities[i] for i in active]
        # arrival / departure days (and any shortened day) should not get the
        # far-away group of places
        short = {idx for idx, i in enumerate(active)
                 if windows[i]["available_hours"] < MAX_HOURS_PER_DAY or i in (0, num_days - 1)}
        sub_assigned = assign_to_dates(clusters, sub_dates, sub_caps, rainy_dates,
                                       base=(hotel_lat, hotel_lon), short_days=short)
    else:
        sub_assigned = []
    assigned: list[list[dict]] = [[] for _ in range(num_days)]
    for idx, i in enumerate(active):
        assigned[i] = sub_assigned[idx]
    for i, events_here in pinned.items():
        assigned[i] = assigned[i] + events_here
    # anything left over (more clusters than active days) becomes ideas
    for c in clusters:
        if not any(c is a for a in assigned):
            reserve = list(c) + reserve
    closed_notes = _fix_closed_days(assigned, dates, capacities)

    # Step 3.5: every day engaging - no "packed day, then an empty day".
    # Move stops from the fullest day to the emptiest until they are level,
    # then top up any day that is still light from the reserve.
    even_out(assigned, dates, capacities, active, max_stops, base=(hotel_lat, hotel_lon))
    reserve = fill_light_days(assigned, reserve, dates, capacities, active, max_stops,
                              base=(hotel_lat, hotel_lon))

    # Step 4: order each day's stops, trim to a realistic length
    day_stops: list[list[dict]] = []
    day_extra: list[list[dict]] = []
    for i in range(num_days):
        # A day trip spends hours on the road: less time for the places.
        far = trip_distance(assigned[i], (hotel_lat, hotel_lon))
        drive_back = (drive_hours(far, (hotel_lat, hotel_lon), assigned[i])
                      if far > DAY_TRIP_KM else 0.0)
        visit_budget = max(1.0, capacities[i] - 2 * drive_back) if drive_back else capacities[i]
        chosen, left_out = _pick_best_for_day(assigned[i], visit_budget, max_stops)
        ordered = order_for_the_day(chosen, hotel_lat, hotel_lon)
        kept, extra = trim_to_day_length(
            ordered, max(1.0, windows[i]["available_hours"] - drive_back))
        day_stops.append(kept)
        day_extra.append(extra + left_out)

    # Step 4.5: no sightseeing day should end up with literally nothing on it.
    # Borrow the closest-to-hotel leftover stop that is open that day.
    for i, kept in enumerate(day_stops):
        if kept or capacities[i] < 1.0:
            continue
        candidates = [(j, p) for j in range(num_days) for p in day_extra[j]
                      if not _closed_on(p, dates[i])]
        candidates += [(-1, p) for p in reserve if not _closed_on(p, dates[i])]
        if not candidates:
            continue
        j, borrowed = min(candidates, key=lambda jp: (-jp[1].get("score", 0) / 25.0
                                                      + haversine_km(hotel_lat, hotel_lon,
                                                                     jp[1]["lat"], jp[1]["lon"]) / 10.0))
        if j >= 0:
            day_extra[j].remove(borrowed)
        else:
            reserve = [p for p in reserve if p is not borrowed]
        chosen, _ = _pick_best_for_day([borrowed], capacities[i], max_stops)
        day_stops[i] = _remeasure(chosen or [borrowed], hotel_lat, hotel_lon)

    # Step 4.6: the same number of activities every day (the traveller's rule:
    # no "five stops today, one tomorrow").
    spare = [p for extra in day_extra for p in extra] + list(reserve)
    level_stop_counts(day_stops, dates, [w["available_hours"] for w in windows], spare,
                      (hotel_lat, hotel_lon), max_stops)
    planned = {p["name"] for stops in day_stops for p in stops}
    day_extra = [[] for _ in range(num_days)]
    reserve = [p for p in spare if p["name"] not in planned]

    # Step 5: lunch, totals and the day records
    days = []
    used_lunch: set[str] = set()
    for i, date_string in enumerate(dates):
        kept, extra = day_stops[i], day_extra[i]
        w = windows[i]

        lunch = pick_lunch(restaurants, kept, used_lunch) if kept else None
        if lunch:
            used_lunch.add(lunch["name"])

        metrics_ = day_metrics(kept, hotel_lat, hotel_lon)
        total_km, hours = metrics_["total_km"], metrics_["hours"]
        notes = [n for n in closed_notes if any(n.startswith(s["name"]) for s in kept)]
        travel_note = w["travel_note"]
        if metrics_["day_trip"]:
            trip = metrics_["day_trip"]
            trip_note = (f"Day trip: {trip['km']:.0f} km by road to {trip['to']} "
                         f"(about {fmt_duration(trip['hours'])} each way) - set off early "
                         f"and keep the evening for the drive back.")
            travel_note = (travel_note + " " + trip_note).strip()

        days.append({
            "date": date_string,
            "day_number": i + 1,
            "stops": kept,
            "lunch": lunch,
            "total_km": total_km,
            "hours": hours,
            "is_rainy": date_string in rainy_dates,
            "rain_chance": rain_chances.get(date_string),
            "is_wettest": date_string == wettest,
            "indoor_share": round(indoor_score(kept) * 100),
            "extra_ideas": [e["name"] for e in extra],
            "start_hour": w["start_hour"],
            "available_hours": w["available_hours"],
            "travel_note": travel_note,
            "travel_day": w["available_hours"] < 1.0,
            "notes": notes,
            "return_km": metrics_["return_km"],
            "day_trip": metrics_["day_trip"],
        })

    # Shortlist leftovers are still useful "if you have time" ideas, best first,
    # shown on the day whose stops they sit closest to (and that they are open on).
    for place in reserve[:num_days * 2]:
        options = [j for j in range(num_days) if days[j]["stops"]
                   and not _closed_on(place, dates[j])]
        if not options:
            continue
        near = min(options, key=lambda j: min(
            haversine_km(place["lat"], place["lon"], s["lat"], s["lon"])
            for s in days[j]["stops"]))
        if place["name"] not in days[near]["extra_ideas"]:
            days[near]["extra_ideas"].append(place["name"])

    return days


def fmt_duration(hours: float) -> str:
    h = int(hours)
    m = int(round((hours - h) * 60 / 5.0) * 5)
    if m == 60:
        h, m = h + 1, 0
    return f"{h}h {m:02d}m" if h else f"{m} min"


def _area_name(stop: dict) -> str:
    name = stop["name"]
    return name.split(",")[-1].strip() if "," in name else name


def day_metrics(stops: list[dict], hotel_lat: float, hotel_lon: float) -> dict:
    """
    Distance and time for one day, INCLUDING the way back to the stay.

    v26 left out the return leg, and on a day trip it timed the climb to
    Mussoorie like a 20 km/h city hop - the review pass rightly complained.
    Day trips now use road distances (with a bigger detour factor in the
    hills) for every leg, and name the farthest place as the destination.
    """
    if not stops:
        return {"total_km": 0, "hours": 0, "return_km": 0, "day_trip": None}
    base = (hotel_lat, hotel_lon)
    last = stops[-1]
    back_straight = haversine_km(last["lat"], last["lon"], hotel_lat, hotel_lon)
    far = trip_distance(stops, base)
    visits = sum(s.get("visit_hours", 1.5) for s in stops)
    if far > DAY_TRIP_KM:
        factor, speed = road_params(base, stops, _base_elev(base))
        legs = [s.get("leg_km", 0) for s in stops] + [back_straight]
        road = [leg * (factor if leg > 3 else 1.3) for leg in legs]
        hours = visits + sum(r / (speed if leg > 3 else 15.0) for r, leg in zip(road, legs))
        farthest = max(stops, key=lambda s: haversine_km(hotel_lat, hotel_lon, s["lat"], s["lon"]))
        one_way_straight = haversine_km(hotel_lat, hotel_lon, farthest["lat"], farthest["lon"])
        trip = {"to": _area_name(farthest), "km": round(one_way_straight * factor, 1),
                "hours": round(one_way_straight * factor / speed, 2),
                "factor": factor, "speed_kmh": speed}
        return {"total_km": round(sum(road), 1), "hours": round(hours, 1),
                "return_km": round(road[-1], 1), "day_trip": trip}
    legs = sum(s.get("leg_km", 0) for s in stops)
    total = legs + back_straight
    return {"total_km": round(total, 2), "hours": round(visits + total / 20.0, 1),
            "return_km": round(back_straight, 2), "day_trip": None}


def refresh_day(day: dict, hotel_lat: float, hotel_lon: float,
                restaurants: list[dict] | None = None, used_lunch: set[str] | None = None) -> dict:
    """Re-order a day after the agent changed its stops, and recompute everything."""
    stops = order_for_the_day(day.get("stops") or [], hotel_lat, hotel_lon)
    day["stops"] = stops
    m = day_metrics(stops, hotel_lat, hotel_lon)
    day.update(total_km=m["total_km"], hours=m["hours"], return_km=m["return_km"],
               day_trip=m["day_trip"], indoor_share=round(indoor_score(stops) * 100))
    base_note = (day.get("travel_note") or "").split("Day trip:")[0].strip()
    if m["day_trip"]:
        trip = m["day_trip"]
        base_note = (base_note + f" Day trip: {trip['km']:.0f} km by road to {trip['to']} "
                     f"(about {fmt_duration(trip['hours'])} each way) - set off early and keep "
                     f"the evening for the drive back.").strip()
    day["travel_note"] = base_note
    if restaurants is not None and stops:
        day["lunch"] = pick_lunch(restaurants, stops, used_lunch or set())
    return day


def pick_lunch(restaurants: list[dict], stops: list[dict],
               used: set[str] | None = None) -> dict | None:
    """
    Choose a lunch spot for a day.

    v23 took the single nearest restaurant to the middle stop - often the same
    place every day, and on a Mussoorie day trip a restaurant 14 km away in
    Dehradun. Now: among places within LUNCH_NEAR_KM of the middle of the day,
    the best-rated one not already used; otherwise the nearest (flagged
    `is_far` so the agent can look for something closer).
    """
    if not restaurants or not stops:
        return None
    used = used or set()
    middle = stops[len(stops) // 2]

    def dist(r):
        return haversine_km(middle["lat"], middle["lon"], r["lat"], r["lon"])

    located = [r for r in restaurants if not r.get("approx_location")
               and r.get("lat") is not None]
    pool = located or restaurants
    near = [r for r in pool if dist(r) <= LUNCH_NEAR_KM]
    fresh = [r for r in near if r.get("name") not in used] or near
    if fresh:
        best = max(fresh, key=lambda r: (r.get("food_score", 50) - dist(r) * 6))
    else:
        best = min(pool, key=dist)

    result = dict(best)
    km = dist(best)
    result["walk_km"] = round(km, 2)
    result["distance_km"] = round(km, 2)
    result["is_far"] = km > LUNCH_NEAR_KM
    return result


# Kept for backwards compatibility (older code and tests import it).
def _nearest_restaurant(restaurants: list[dict], stops: list[dict]) -> dict | None:
    return pick_lunch(restaurants, stops)


def total_distance(days: list[dict]) -> float:
    """How much ground the whole trip covers. Nice stat for the UI."""
    return round(sum(d["total_km"] for d in days), 1)
