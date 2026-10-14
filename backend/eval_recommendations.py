"""
Measure recommendation quality with numbers, not screenshots.

    python eval_recommendations.py            # offline, ~1 second, no keys
    python eval_recommendations.py --live     # real APIs (needs internet)

OFFLINE mode builds, for every city in the hand-picked list, a pool that looks
like what the live sources return: the city's real landmarks (tagged like
OpenStreetMap tags them - historic/tourism tags, a wikidata id, no "curated"
flag, so they get no help from our own list) mixed with typical noise:
neighbourhood temples, cafes, restaurants, a bank, a bus stand, colony parks.
It then checks:

  precision@k      share of the top-k picks that are real landmarks
  noise leaked     noise places that made it onto a day plan
  closed-day hits  a stop planned on a weekday it is closed (must be 0)
  stops per day    min / max across days (balanced days)

LIVE mode runs the real find_attractions for each city and reports how many
of the city's headline sights made the top of the list and how many picks
are low-confidence. Use it to compare versions in your viva:
"landmark recall went from X% to Y%".
"""

import argparse
import asyncio
import os
import random
import statistics
import sys
from datetime import date, timedelta

os.environ.setdefault("STRICT_MODE", "false")

import dayplanner  # noqa: E402
from tools import places  # noqa: E402

CITIES = ["jaipur", "delhi", "agra", "udaipur", "varanasi", "mumbai", "kolkata", "bengaluru",
          "hyderabad", "chennai", "jodhpur", "amritsar", "kochi", "mysuru", "dehradun", "goa"]
INTERESTS = ["Culture & Heritage", "Food"]

NOISE = [("Shri Ram Mandir", "place_of_worship"), ("Hanuman Mandir", "place_of_worship"),
         ("Jama Masjid (local)", "place_of_worship"), ("Sharma Tea Stall", "cafe"),
         ("Domino's Pizza", "restaurant"), ("Hotel Annapurna Restaurant", "restaurant"),
         ("Sector 4 Park", "park"), ("Colony Park", "park"), ("State Bank of India", "attraction"),
         ("City Bus Stand", "attraction"), ("Local Market", "marketplace"),
         ("Gupta General Store", "marketplace")]

OSM_TAG = {"fort": ("historic", "fort"), "palace": ("historic", "palace"),
           "monument": ("historic", "monument"), "museum": ("tourism", "museum"),
           "ruins": ("historic", "ruins"), "place_of_worship": ("amenity", "place_of_worship"),
           "garden": ("leisure", "garden"), "park": ("leisure", "park"),
           "marketplace": ("amenity", "marketplace"), "lake": ("natural", "water"),
           "beach": ("natural", "beach"), "viewpoint": ("tourism", "viewpoint"),
           "waterfall": ("natural", "waterfall"), "zoo": ("tourism", "zoo"),
           "nature_reserve": ("leisure", "nature_reserve"), "peak": ("natural", "peak"),
           "attraction": ("tourism", "attraction"), "restaurant": ("amenity", "restaurant"),
           "cafe": ("amenity", "cafe")}


def _element(name, kind, lat, lon, notable):
    key, value = OSM_TAG.get(kind, ("tourism", "attraction"))
    tags = {"name": name, key: value}
    if notable:
        tags["wikidata"] = f"Q{abs(hash(name)) % 10_000_000}"
        if key != "tourism":
            tags["tourism"] = "attraction"
    return {"lat": lat, "lon": lon, "tags": tags}


def offline_pool(city: str, rng: random.Random):
    info = places.curated_info(city)
    lat0 = statistics.mean(p["lat"] for p in info)
    lon0 = statistics.mean(p["lon"] for p in info)
    elements = [_element(p["name"], p["kind"], p["lat"], p["lon"], True) for p in info]
    noise_names = set()
    for i in range(3):                      # three copies of the noise, spread around
        for name, kind in NOISE:
            n = f"{name} {i + 1}" if i else name
            noise_names.add(n)
            elements.append(_element(n, kind, lat0 + rng.uniform(-0.04, 0.04),
                                     lon0 + rng.uniform(-0.04, 0.04), False))
    pool = places._clean_results({"elements": elements}, 1000)
    # copy opening days / best times the way find_attractions would
    for p in pool:
        for c in info:
            if c["name"] == p["name"]:
                if c.get("closed"):
                    p["closed_days"] = list(c["closed"])
                if c.get("best"):
                    p["best_time"] = c["best"]
                if c.get("hours"):
                    p["visit_hours"] = c["hours"]
    return pool, {p["name"] for p in info}, noise_names, (lat0, lon0)


def evaluate_offline(days: int = 3) -> list[tuple]:
    """(city, precision@k, noise_leaked, closed_day_hits, min_stops, max_stops) per city."""
    rng = random.Random(7)
    start = date.today() + timedelta(days=30)
    rows = []
    for city in CITIES:
        pool, landmarks, noise, centre = offline_pool(city, rng)
        ranked = places.rank_places(pool, INTERESTS, {}, min_keep=days * 2,
                                    ctx={"anchor": centre, "city": city})
        k = min(len(landmarks), days * 3)
        top = [p["name"] for p in ranked[:k]]
        precision = sum(1 for n in top if n in landmarks) / max(1, k)

        dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
        plan = dayplanner.build_day_plan(ranked, [], dates, *centre)
        stops = [s for d in plan for s in d["stops"]]
        leaked = [s["name"] for s in stops if s["name"] in noise]
        closed_hits = [s["name"] for d in plan for s in d["stops"]
                       if date.fromisoformat(d["date"]).weekday() in (s.get("closed_days") or [])]
        per_day = [len(d["stops"]) for d in plan]
        rows.append((city, precision, len(leaked), len(closed_hits), min(per_day), max(per_day)))
    return rows


def run_offline(days: int = 3) -> int:
    rows = evaluate_offline(days)
    failures = sum(1 for r in rows if r[1] < 0.8 or r[2] > 1 or r[3])
    print(f"{'city':<11}{'precision@k':>12}{'noise leaked':>14}{'closed-day':>12}{'stops/day':>12}")
    for city, precision, leaked, closed, lo, hi in rows:
        print(f"{city:<11}{precision:>11.0%}{leaked:>14}{closed:>12}{f'{lo}-{hi}':>12}")
    print(f"\nmean precision@k: {statistics.mean(r[1] for r in rows):.0%}   "
          f"noise leaked: {sum(r[2] for r in rows)}   closed-day stops: {sum(r[3] for r in rows)}")
    return failures


async def run_live(days: int = 3) -> None:
    from tools import geo
    print(f"{'city':<11}{'pool':>6}{'headline sights in top':>25}{'low-confidence':>17}")
    for city in CITIES:
        spot = await geo.geocode(city.title())
        found = await places.find_attractions(spot["lat"], spot["lon"], INTERESTS,
                                              num_days=days, city=city)
        headline = [p["name"] for p in places.curated_info(city)][:6]
        top = found[: days * 4]
        hits = sum(1 for h in headline if any(places._same_place(
            {"name": h, "lat": c["lat"], "lon": c["lon"]}, t)
            for c in places.curated_info(city) if c["name"] == h for t in top))
        low = sum(1 for p in top if p.get("confidence") == "low") / max(1, len(top))
        print(f"{city:<11}{len(found):>6}{f'{hits}/{len(headline)}':>25}{low:>16.0%}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", action="store_true", help="call the real APIs (needs internet)")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    if args.live:
        asyncio.run(run_live(args.days))
    else:
        sys.exit(1 if run_offline(args.days) else 0)
