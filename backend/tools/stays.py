"""
Where to stay.

Honest explanation, because this matters in a viva:

  The HOTELS are real. They come from two independent sources that are
  merged and de-duplicated: Geoapify Places (free, 3,000 requests/day, the
  same key the attractions search already uses) and OpenStreetMap. Both give
  real names, real coordinates and a real street address.

  The PRICES are estimated. There is no free API in 2026 that gives real
  Indian hotel rates - Booking.com and Expedia both need a partner contract.
  So we estimate the price with a plain, readable formula based on the type
  of property, the star rating in the OSM tags, and how expensive the city
  is. Every price we return is tagged price_is_estimate=True and the UI
  shows a warning next to it.

That is a much better answer than "an LLM guessed a number and we hid it".
"""

import asyncio
import math
import re

import config
import recommend
from tools import webclient
from tools.geo import haversine_km

# How much a night roughly costs by property type, in rupees.
BASE_PRICE = {
    "hostel": 700,
    "guest_house": 1400,
    "apartment": 2200,
    "hotel": 2800,
    "resort": 6000,
}

# Some cities are simply pricier. Multiplier on the base price.
CITY_MULTIPLIER = {
    "mumbai": 1.8, "delhi": 1.5, "new delhi": 1.5, "bengaluru": 1.5, "bangalore": 1.5,
    "goa": 1.6, "panaji": 1.6, "leh": 1.4, "udaipur": 1.4, "gangtok": 1.1,
    "jaipur": 1.2, "manali": 1.2, "shimla": 1.2, "munnar": 1.2, "andaman": 1.7,
    "varanasi": 0.9, "agra": 1.0, "hampi": 0.8, "pushkar": 0.8, "rishikesh": 0.9,
}

# Each star ABOVE what is normal for that property type adds this much.
STAR_BONUS = 900

# What star rating you would expect from each type by default. A hostel is a
# 1-star place by nature, so being tagged 1 star should not add anything -
# that quality is already priced into BASE_PRICE. Getting this wrong is how
# you end up quoting Rs 1,900 a night for a backpacker hostel.
BASELINE_STARS = {
    "hostel": 1,
    "guest_house": 2,
    "apartment": 2,
    "hotel": 3,
    "resort": 4,
}


def estimate_price(kind: str, stars: int, city: str) -> int:
    """
    Our price model. Deliberately simple so anyone can read it and argue
    with it - that is better than a black box.
    """
    base = BASE_PRICE.get(kind, 2000)

    extra_stars = max(0, stars - BASELINE_STARS.get(kind, 3))
    base += extra_stars * STAR_BONUS

    base *= CITY_MULTIPLIER.get(city.strip().lower(), 1.0)
    return int(round(base / 50) * 50)   # round to the nearest 50 rupees


def _build_query(lat: float, lon: float, radius_m: int, limit: int) -> str:
    """
    One statement instead of ten. See the long note in places.py for why -
    short version: too many separate spatial searches in one request makes
    the free Overpass servers time out.
    """
    kinds = "hotel|guest_house|hostel|apartment|resort"
    return (f'[out:json][timeout:12];\n'
            f'nwr["tourism"~"^({kinds})$"](around:{radius_m},{lat},{lon});\n'
            f'out center {limit};')


# Geoapify's own names for the kinds of property we care about.
GEOAPIFY_STAY_CATEGORIES = ("accommodation.hotel,accommodation.guest_house,"
                            "accommodation.hostel,accommodation.motel,"
                            "accommodation.apartment,accommodation.chalet")

# Geoapify category -> the property kinds our price model understands.
_GEOAPIFY_KIND = {
    "hotel": "hotel",
    "guest_house": "guest_house",
    "hostel": "hostel",
    "motel": "guest_house",
    "apartment": "apartment",
    "chalet": "guest_house",
}

DEFAULT_STARS = {"hostel": 1, "guest_house": 2, "apartment": 2, "hotel": 3, "resort": 4}


def _norm(name: str) -> str:
    """'Hotel  Pink-City!' and 'hotel pink city' are the same hotel."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _stars_from(value, kind: str) -> int:
    try:
        stars = int(str(value).strip()[0])
        if 1 <= stars <= 5:
            return stars
    except (ValueError, IndexError):
        pass
    return DEFAULT_STARS.get(kind, 2)


def _stars_tagged(value) -> bool:
    """Did the map data actually give a star rating, or are we assuming one?"""
    try:
        return 1 <= int(str(value).strip()[0]) <= 5
    except (ValueError, IndexError):
        return False


async def _geoapify_stays(lat: float, lon: float, city: str, nights: int,
                          radius_m: int, limit: int) -> list[dict]:
    """
    Real, named properties from Geoapify Places. Returns [] immediately (no
    network call) when no GEOAPIFY_API_KEY is set, so nothing breaks without it.
    """
    if not config.GEOAPIFY_ENABLED:
        return []

    data = await webclient.get_json(
        config.GEOAPIFY_PLACES_URL,
        params={"categories": GEOAPIFY_STAY_CATEGORIES,
                "filter": f"circle:{lon},{lat},{radius_m}",
                "bias": f"proximity:{lon},{lat}",
                "limit": min(limit * 2, 60),
                "apiKey": config.GEOAPIFY_API_KEY},
        label="geoapify_stays",
        ttl=60 * 60 * 24 * 7,
    )
    if not data:
        return []

    out = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        name = (props.get("name") or "").strip()
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        p_lon, p_lat = coords[0], coords[1]
        if not name or p_lat is None or p_lon is None:
            continue

        # "accommodation.hotel" -> "hotel". A place can carry several categories.
        kind = "hotel"
        for category in props.get("categories") or []:
            tail = category.split(".")[-1]
            if tail in _GEOAPIFY_KIND:
                kind = _GEOAPIFY_KIND[tail]
                break

        raw_tags = (props.get("datasource") or {}).get("raw") or {}
        stars = _stars_from(raw_tags.get("stars"), kind)
        price = estimate_price(kind, stars, city)

        out.append({
            "name": name,
            "kind": kind.replace("_", " "),
            "area": props.get("suburb") or props.get("district") or props.get("city") or city,
            "address": props.get("formatted") or props.get("address_line2") or "",
            "website": props.get("website") or raw_tags.get("website") or "",
            "stars": stars,
            "stars_tagged": _stars_tagged(raw_tags.get("stars")),
            "source_count": 1,
            "lat": p_lat,
            "lon": p_lon,
            "price_per_night_inr": price,
            "total_inr": price * nights,
            "price_is_estimate": True,
            "source": "geoapify (real property, estimated price)",
        })
    return out


def _merge_stays(*lists: list[dict], limit: int) -> list[dict]:
    """
    Combine stay lists, first list wins on duplicates. Two entries are the
    same hotel when the cleaned-up names match and they are within 400 m -
    that keeps real chain branches in different parts of town as separate
    options while dropping the same hotel reported by both sources.
    """
    merged: list[dict] = []
    for stay_list in lists:
        for stay in stay_list:
            key = _norm(stay["name"])
            if not key:
                continue
            match = next((m for m in merged
                          if _norm(m["name"]) == key
                          and haversine_km(m["lat"], m["lon"], stay["lat"], stay["lon"]) < 0.4),
                         None)
            if match is None:
                merged.append(stay)
            else:
                # Same hotel from a second, independent source: that is
                # evidence it is real and operating. Keep the first entry
                # but count the agreement and fill anything it lacked.
                match["source_count"] = match.get("source_count", 1) + 1
                for field in ("website", "address"):
                    if not match.get(field) and stay.get(field):
                        match[field] = stay[field]
                if not match.get("stars_tagged") and stay.get("stars_tagged"):
                    match["stars"], match["stars_tagged"] = stay["stars"], True
    return merged[: limit * 2]


# Foursquare's own price tiers (1 cheapest .. 4 most expensive) -> a typical
# nightly rate in rupees before the city multiplier.
FSQ_PRICE_BAND = {1: 1200, 2: 2800, 3: 5500, 4: 10000}


async def _foursquare_stays(lat: float, lon: float, radius_m: int) -> list[dict]:
    """
    Quality metadata (rating 0-10, price tier) for hotels, when a Foursquare key
    is set. It is used to ENRICH the real properties we already found, never to
    add unverified ones - and with no key it returns [] without a network call.
    """
    if not config.FOURSQUARE_ENABLED:
        return []
    from tools import places as _places     # reuse the header/coordinate helpers
    data = await webclient.get_json(
        config.FOURSQUARE_URL,
        params={"ll": f"{lat},{lon}", "query": "hotel", "radius": min(radius_m, 100000),
                "limit": 50, "sort": "RELEVANCE", "fields": _places.fsq_fields()},
        headers=_places._fsq_headers(),
        label="foursquare_stays",
        ttl=60 * 60 * 24,
    )
    out = []
    for place in (data or {}).get("results", []):
        p_lat, p_lon = _places._fsq_coords(place)
        if not place.get("name") or p_lat is None or p_lon is None:
            continue
        out.append({"name": place["name"], "lat": p_lat, "lon": p_lon,
                    "rating": place.get("rating"), "price_tier": place.get("price"),
                    "ratings_count": (place.get("stats") or {}).get("total_ratings")})
    return out


def _enrich_with_ratings(stays: list[dict], rated: list[dict], city: str) -> None:
    """Attach a visitor rating (and a better price estimate) to matching real stays."""
    for stay in stays:
        key = _norm(stay["name"])
        for r in rated:
            same = _norm(r["name"]) == key or (len(key) > 5 and key in _norm(r["name"]))
            if not same or haversine_km(stay["lat"], stay["lon"], r["lat"], r["lon"]) > 0.4:
                continue
            if r.get("rating") is not None:
                stay["rating"] = r["rating"]
                stay["ratings_count"] = r.get("ratings_count")
            tier = r.get("price_tier")
            if tier in FSQ_PRICE_BAND and not stay.get("is_price_band"):
                band = FSQ_PRICE_BAND[tier] * CITY_MULTIPLIER.get(city.strip().lower(), 1.0)
                new_price = int(round((stay["price_per_night_inr"] + band) / 2 / 50) * 50)
                nights = stay["total_inr"] // max(1, stay["price_per_night_inr"])
                stay["price_per_night_inr"] = new_price
                stay["total_inr"] = new_price * nights
                stay["price_basis"] = "estimate adjusted by Foursquare price tier"
            stay["source_count"] = stay.get("source_count", 1) + 1
            break


async def _live_stays(lat: float, lon: float, city: str, nights: int,
                      radius_m: int, limit: int) -> list[dict]:
    """Ask Geoapify and OpenStreetMap at the same time and merge the answers."""
    raw, geo_stays, rated = await asyncio.gather(
        webclient.overpass(_build_query(lat, lon, radius_m, limit * 4),
                           label="overpass_stays", ttl=60 * 60 * 24 * 7),
        _geoapify_stays(lat, lon, city, nights, radius_m, limit),
        _foursquare_stays(lat, lon, radius_m),
    )
    osm_stays = _parse_stays(raw, city, nights, limit) if raw else []
    # Geoapify first: its entries carry a street address and website.
    merged = _merge_stays(geo_stays, osm_stays, limit=limit)
    if rated:
        _enrich_with_ratings(merged, rated, city)
    return merged


# Places to sleep that are mapped but NOT open to tourists - the v26 log
# picked "Central Government Touring Officers Guest House" in Dehradun.
NOT_FOR_TOURISTS_RE = re.compile(
    r"\b(govt|government|touring officers?|officers'?|circuit house|inspection bungalow|"
    r"dak bungalow|pwd|cpwd|ongc|army|military|air force|navy|naval|defence|defense|"
    r"cantonment|mess|transit camp|retiring rooms?|railway rest house|irrigation|"
    r"university|institute|iit|nit|aiims|college|hospital|hostel for (?:boys|girls)|"
    r"(?:boys|girls|working women'?s?) hostel|pg\b|paying guest|ashram guest house of)\b",
    re.IGNORECASE)


def open_to_tourists(stay: dict) -> bool:
    return not NOT_FOR_TOURISTS_RE.search(stay.get("name") or "")


def rooms_needed(kind: str, adults: int, children: int = 0) -> tuple[int, str]:
    """
    How many units the GROUP needs, and what a unit is.

    v23 priced one room whatever the group size - four people were costed at a
    single Rs 2,800 room, and a Rs 850 hostel "night" was really one bed.
      hostel     one bed per person
      apartment  one unit sleeps up to 4
      otherwise  a room takes 2 adults (+ a child), so
                 max(ceil(adults / 2), ceil(everyone / 3))
    """
    adults = max(1, int(adults or 1))
    children = max(0, int(children or 0))
    people = adults + children
    kind = (kind or "hotel").replace("_", " ")
    if kind == "hostel":
        return people, "bed"
    if kind == "apartment":
        return max(1, math.ceil(people / 4)), "apartment"
    return max(1, math.ceil(adults / 2), math.ceil(people / 3)), "room"


def price_for_group(stays: list[dict], adults: int, children: int = 0) -> list[dict]:
    """Attach group_price_per_night_inr / rooms / total to every stay (copies)."""
    out = []
    for stay in stays:
        item = dict(stay)
        units, unit = rooms_needed(item.get("kind", "hotel"), adults, children)
        per_unit = int(item.get("price_per_night_inr", 0))
        item["rooms"] = units
        item["room_unit"] = unit
        item["group_price_per_night_inr"] = per_unit * units
        nights = max(1, int(round(item.get("total_inr", per_unit) / per_unit))) if per_unit else 1
        item["total_inr"] = per_unit * units * nights
        plural = unit + ("s" if units > 1 else "")
        item["price_basis_note"] = f"{units} {plural} x Rs {per_unit:,} a night (estimated)"
        out.append(item)
    return out


async def find_stays(lat: float, lon: float, city: str, nights: int,
                     max_per_night: int | None = None, limit: int = 15,
                     anchor: tuple[float, float] | None = None,
                     travel_style: str = "balanced") -> list[dict]:
    """
    Real, named properties near the destination, BEST FIRST for this traveller.

    "Cheapest" is not "best": the list is ranked by recommend.rank_stays on
    budget fit, quality, location (distance to `anchor`, normally where the
    best attractions are) and evidence. Each stay carries `score`,
    `recommendation` and a plain-English `why`.

    max_per_night: if the budget solver says "nothing above Rs 1800", we filter
    here instead of showing options the user cannot afford.
    """
    stays = await _live_stays(lat, lon, city, nights, 12000, limit)

    # Few results is common in smaller cities - not every guesthouse is
    # mapped inside a tight 12 km radius. Cast a wider net once before
    # falling back to generic price bands.
    if len(stays) < 4:
        wider = await _live_stays(lat, lon, city, nights, 25000, limit)
        stays = _merge_stays(stays, wider, limit=limit)

    stays = [st for st in stays if open_to_tourists(st)]

    if not stays:
        stays = _offline_stays(lat, lon, city, nights)

    if max_per_night is not None:
        affordable = [s for s in stays if s["price_per_night_inr"] <= max_per_night]
        # If literally nothing fits, return the cheapest anyway and let the
        # budget agent report the overshoot honestly.
        stays = affordable if affordable else sorted(
            stays, key=lambda s: s["price_per_night_inr"])[:3]

    return recommend.rank_stays(stays, max_per_night, travel_style, anchor or (lat, lon))


def _parse_stays(raw: dict, city: str, nights: int, limit: int) -> list[dict]:
    stays = []
    seen = set()

    for element in raw.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        if "lat" in element:
            lat, lon = element["lat"], element["lon"]
        elif "center" in element:
            lat, lon = element["center"]["lat"], element["center"]["lon"]
        else:
            continue

        kind = tags.get("tourism", "hotel")
        stars = _stars_from(tags.get("stars"), kind)
        price = estimate_price(kind, stars, city)

        street = ", ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street"),
                                       tags.get("addr:suburb"), tags.get("addr:city")) if x)

        stays.append({
            "name": name,
            "kind": kind.replace("_", " "),
            "area": tags.get("addr:suburb") or tags.get("addr:city") or city,
            "address": street,
            "website": tags.get("website") or tags.get("contact:website") or "",
            "stars": stars,
            "stars_tagged": _stars_tagged(tags.get("stars")),
            "source_count": 1,
            "lat": lat,
            "lon": lon,
            "price_per_night_inr": price,
            "total_inr": price * nights,
            "price_is_estimate": True,       # the UI reads this and shows a warning
            "source": "openstreetmap (real property, estimated price)",
        })

        if len(stays) >= limit:
            break

    return stays


def _offline_stays(lat: float, lon: float, city: str, nights: int) -> list[dict]:
    """
    What to show when OpenStreetMap cannot be reached.

    Earlier this invented property names like "Backpackers Hostel", which then
    appeared all over a real itinerary as though it were a real place you
    could book. That is exactly the dishonesty this project exists to avoid.

    So instead of fake hotels, we return accommodation TYPES with typical
    nightly rates for that city. "Guesthouse, around Rs 1,700 a night" is true
    and useful. "Sunrise Guest House" is neither.
    """
    bands = [
        ("hostel", 1, "Hostel dorm or basic room"),
        ("guest_house", 2, "Guesthouse or homestay"),
        ("hotel", 3, "Mid-range hotel"),
        ("hotel", 4, "Upper mid-range hotel"),
        ("resort", 4, "Resort or heritage property"),
    ]

    stays = []
    for kind, stars, label in bands:
        price = estimate_price(kind, stars, city)
        stays.append({
            "name": f"{label} in {city.title()}",
            "kind": kind.replace("_", " "),
            "area": f"{city.title()} (central)",
            "stars": stars,
            "stars_tagged": False,
            "source_count": 1,
            # Put it at the city centre so the day planner still works.
            "lat": lat,
            "lon": lon,
            "price_per_night_inr": price,
            "total_inr": price * nights,
            "price_is_estimate": True,
            "is_price_band": True,      # the UI shows a different label for these
            "source": ("typical rate for this city - OpenStreetMap was "
                       "unreachable, so no specific property is listed"),
        })
    return stays
