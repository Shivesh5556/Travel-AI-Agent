"""
Turning a city name into coordinates, plus the distance maths.

Real data source: Nominatim (OpenStreetMap's geocoder). Free, no API key,
just be polite about how often you call it.

We also keep a small hard-coded table of Indian cities. That is not cheating:
it means the project still demos on college wifi that blocks everything, and
it saves a network call for the 40 cities people actually search for.
"""

import math

import config
from tools import webclient

# lat, lon, and the nearest airport's IATA code (needed for flight search).
KNOWN_CITIES = {
    "delhi": (28.6139, 77.2090, "DEL"),
    "new delhi": (28.6139, 77.2090, "DEL"),
    "mumbai": (19.0760, 72.8777, "BOM"),
    "bengaluru": (12.9716, 77.5946, "BLR"),
    "bangalore": (12.9716, 77.5946, "BLR"),
    "chennai": (13.0827, 80.2707, "MAA"),
    "kolkata": (22.5726, 88.3639, "CCU"),
    "hyderabad": (17.3850, 78.4867, "HYD"),
    "pune": (18.5204, 73.8567, "PNQ"),
    "ahmedabad": (23.0225, 72.5714, "AMD"),
    "jaipur": (26.9124, 75.7873, "JAI"),
    "udaipur": (24.5854, 73.7125, "UDR"),
    "jodhpur": (26.2389, 73.0243, "JDH"),
    "jaisalmer": (26.9157, 70.9083, "JSA"),
    "goa": (15.2993, 74.1240, "GOI"),
    "panaji": (15.4909, 73.8278, "GOI"),
    "kochi": (9.9312, 76.2673, "COK"),
    "munnar": (10.0889, 77.0595, "COK"),
    "alleppey": (9.4981, 76.3388, "COK"),
    "varkala": (8.7379, 76.7163, "TRV"),
    "thiruvananthapuram": (8.5241, 76.9366, "TRV"),
    "dehradun": (30.3165, 78.0322, "DED"),
    "mussoorie": (30.4598, 78.0644, "DED"),
    "rishikesh": (30.0869, 78.2676, "DED"),
    "haridwar": (29.9457, 78.1642, "DED"),
    "nainital": (29.3803, 79.4636, "PGH"),
    "manali": (32.2432, 77.1892, "KUU"),
    "shimla": (31.1048, 77.1734, "SLV"),
    "dharamshala": (32.2190, 76.3234, "DHM"),
    "kasol": (32.0100, 77.3150, "KUU"),
    "spiti": (32.2464, 78.0177, "KUU"),
    "leh": (34.1526, 77.5771, "IXL"),
    "srinagar": (34.0837, 74.7973, "SXR"),
    "amritsar": (31.6340, 74.8723, "ATQ"),
    "chandigarh": (30.7333, 76.7794, "IXC"),
    "agra": (27.1767, 78.0081, "AGR"),
    "varanasi": (25.3176, 82.9739, "VNS"),
    "lucknow": (26.8467, 80.9462, "LKO"),
    "darjeeling": (27.0360, 88.2627, "IXB"),
    "gangtok": (27.3389, 88.6065, "PYG"),
    "shillong": (25.5788, 91.8933, "SHL"),
    "guwahati": (26.1445, 91.7362, "GAU"),
    "puri": (19.8135, 85.8312, "BBI"),
    "bhubaneswar": (20.2961, 85.8245, "BBI"),
    "hampi": (15.3350, 76.4600, "HBX"),
    "coorg": (12.3375, 75.8069, "BLR"),
    "ooty": (11.4102, 76.6950, "CJB"),
    "pondicherry": (11.9416, 79.8083, "MAA"),
    "andaman": (11.6234, 92.7265, "IXZ"),
    "port blair": (11.6234, 92.7265, "IXZ"),
}


async def geocode(city: str) -> dict:
    """
    Find a city. Returns:
        {"name", "lat", "lon", "iata", "source"}

    'source' tells you whether the answer came from the real API, our built-in
    table, or a rough guess. We show that in the UI - never hide where a
    number came from.
    """
    key = city.strip().lower()

    # 1. our own table first (instant, no network)
    if key in KNOWN_CITIES:
        lat, lon, iata = KNOWN_CITIES[key]
        return {"name": city.title(), "lat": lat, "lon": lon, "iata": iata, "source": "builtin"}

    # 2. ask OpenStreetMap
    data = await webclient.get_json(
        config.NOMINATIM_URL,
        params={"q": city, "format": "json", "limit": 1, "countrycodes": "in"},
        label="nominatim_geocode",
        ttl=60 * 60 * 24 * 30,   # city coordinates do not move, cache for a month
    )

    if data:
        hit = data[0]
        lat, lon = float(hit["lat"]), float(hit["lon"])
        return {
            "name": hit.get("display_name", city).split(",")[0],
            "lat": lat,
            "lon": lon,
            "iata": nearest_known_airport(lat, lon),
            "source": "nominatim",
        }

    # 2b. Nominatim failed (403 / busy) - try Geoapify before giving up
    alt = await _geoapify_autocomplete(city, 1)
    if alt:
        hit = alt[0]
        return {"name": hit["name"], "lat": hit["lat"], "lon": hit["lon"],
                "iata": hit["iata"] or nearest_known_airport(hit["lat"], hit["lon"]),
                "source": "geoapify"}

    # 3. total fallback so we never crash - centre of India
    return {"name": city.title(), "lat": 22.9734, "lon": 78.6569, "iata": "DEL", "source": "unknown"}


async def search_candidates(query: str, limit: int = 7) -> list[dict]:
    """
    Several possible matches for a place name the user is still typing.

    geocode() above commits to ONE answer - fine once you already know which
    "Jaipur" you mean. While the user is typing, though, a name alone is
    often ambiguous: "Goa" is a whole state in India but also a town in
    Nigeria, and India alone has more than one Bilaspur, Pratapgarh and
    Hamirpur. This is the search-as-you-type version: it returns several
    candidates, each with enough context (state/country, and an airport code
    where we know one) for a human to pick the right one - the same idea as
    the "Goa (South)" vs "Goa (North)" vs "Genoa, Italy" picker on a flight
    booking site.
    """
    query = query.strip()
    if len(query) < 2:
        return []

    seen_points: set[tuple[float, float]] = set()
    candidates: list[dict] = []

    # 1. our own table first - instant, no network, and it already carries
    # an airport code, which makes a nice disambiguating badge.
    needle = query.lower()
    for key, (lat, lon, iata) in KNOWN_CITIES.items():
        if needle not in key:
            continue
        point = (round(lat, 2), round(lon, 2))
        if point in seen_points:
            continue
        seen_points.add(point)
        candidates.append({
            "name": key.title(),
            "subtitle": "India",
            "country": "India",
            "state": "",
            "lat": lat,
            "lon": lon,
            "iata": iata,
            "source": "builtin",
        })

    # 2. Nominatim, deliberately NOT restricted to one country - restricting
    # it would hide exactly the collisions ("Goa, India" vs "Genoa, Italy")
    # this feature exists to catch.
    data = await webclient.get_json(
        config.NOMINATIM_URL,
        params={
            "q": query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": max(limit, 8),
            "accept-language": "en",
        },
        label="nominatim_search",
        ttl=60 * 60 * 24,
    )

    # Only keep results that are actually a place to plan a trip around, not
    # a street, shop or landmark that happens to match the text.
    place_types = {"city", "town", "village", "municipality", "hamlet",
                   "suburb", "administrative", "state", "county",
                   "state_district", "island"}

    # Nominatim is a free public server and 403s/rate-limits easily. When it
    # gives us nothing, ask Geoapify's autocomplete (same free key as places).
    if not data:
        for cand in await _geoapify_autocomplete(query, limit):
            point = (round(cand["lat"], 2), round(cand["lon"], 2))
            if point in seen_points:
                continue
            seen_points.add(point)
            candidates.append(cand)
        return candidates[:limit]

    for hit in (data or []):
        if hit.get("class") not in ("place", "boundary"):
            continue
        if hit.get("type") not in place_types:
            continue

        try:
            lat, lon = float(hit["lat"]), float(hit["lon"])
        except (KeyError, ValueError):
            continue

        point = (round(lat, 2), round(lon, 2))
        if point in seen_points:
            continue
        seen_points.add(point)

        address = hit.get("address", {}) or {}
        name = (address.get("city") or address.get("town") or address.get("village")
                or address.get("state") or hit.get("display_name", query).split(",")[0])
        state = address.get("state", "")
        country = address.get("country", "")
        subtitle = ", ".join(part for part in (state, country) if part) or country

        candidates.append({
            "name": name,
            "subtitle": subtitle,
            "country": country,
            "state": state,
            "lat": lat,
            "lon": lon,
            "iata": nearest_known_airport(lat, lon) if country == "India" else "",
            "source": "nominatim",
        })

        if len(candidates) >= limit:
            break

    return candidates[:limit]


async def _geoapify_autocomplete(query: str, limit: int) -> list[dict]:
    """City suggestions from Geoapify. [] with no key and no network call."""
    if not config.GEOAPIFY_ENABLED:
        return []
    data = await webclient.get_json(
        config.GEOAPIFY_AUTOCOMPLETE_URL,
        params={"text": query, "type": "city", "limit": max(limit, 8),
                "lang": "en", "format": "json", "apiKey": config.GEOAPIFY_API_KEY},
        label="geoapify_autocomplete",
        ttl=60 * 60 * 24,
    )
    out = []
    for hit in (data or {}).get("results", []):
        lat, lon = hit.get("lat"), hit.get("lon")
        if lat is None or lon is None:
            continue
        name = hit.get("city") or hit.get("name") or hit.get("state") or query
        state, country = hit.get("state", ""), hit.get("country", "")
        out.append({
            "name": name,
            "subtitle": ", ".join(x for x in (state, country) if x) or country,
            "country": country,
            "state": state,
            "lat": float(lat),
            "lon": float(lon),
            "iata": nearest_known_airport(float(lat), float(lon)) if country == "India" else "",
            "source": "geoapify",
        })
    return out


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Straight-line distance between two points on Earth, in km.

    This is the standard haversine formula. We use it a lot: to decide if a
    flight even makes sense, and to group attractions into days that are
    actually near each other.
    """
    R = 6371.0  # radius of the Earth in km

    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)

    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)

    return round(2 * R * math.asin(math.sqrt(a)), 2)


def nearest_known_airport(lat: float, lon: float) -> str:
    """Pick the closest airport from our table. Good enough for flight search."""
    best_code = "DEL"
    best_distance = float("inf")
    for _, (c_lat, c_lon, iata) in KNOWN_CITIES.items():
        d = haversine_km(lat, lon, c_lat, c_lon)
        if d < best_distance:
            best_distance = d
            best_code = iata
    return best_code


ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"


async def elevations(points: list[tuple[float, float]]) -> list[float | None]:
    """
    Height above sea level for up to 100 points per call (Open-Meteo, free, no
    key). Used to tell a hill drive (Dehradun 640 m -> Mussoorie 2,000 m) from
    a flat one, because the road distance and speed are very different.
    Returns None for every point if the API is unreachable.
    """
    from tools import webclient
    out: list[float | None] = [None] * len(points)
    for start in range(0, len(points), 100):
        chunk = points[start:start + 100]
        data = await webclient.get_json(
            ELEVATION_URL,
            params={"latitude": ",".join(f"{p[0]:.4f}" for p in chunk),
                    "longitude": ",".join(f"{p[1]:.4f}" for p in chunk)},
            label="open_meteo_elevation", ttl=60 * 60 * 24 * 90)
        values = (data or {}).get("elevation") or []
        for i, value in enumerate(values[:len(chunk)]):
            out[start + i] = float(value) if value is not None else None
    return out
