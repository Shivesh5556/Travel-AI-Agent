"""
Real places, pulled from several independent free sources rather than one.

This is the file that replaces the old project's biggest problem. The old
version asked an LLM to invent hotel names and prices. Here every attraction
and restaurant is a real point on the map with real coordinates, which is
also what makes the day-by-day route optimisation possible.

Three live sources need no key and are always on:

  1. OpenStreetMap (Overpass API) - the primary source. Precise coordinates,
     tagged by type, free and keyless.
  2. Wikipedia geosearch - encyclopaedia entries near the point. Good at
     catching well-known landmarks Overpass has under a different tag.
  3. Wikivoyage - Wikipedia's sister project, written specifically for
     travellers. Its {{see}}/{{do}}/{{eat}}/{{drink}} templates are a
     hand-curated list of things worth doing and where to eat, written by
     someone who actually went - a genuinely different opinion from the
     other two, not just another geosearch.

Three more are optional real-data upgrades, switched on the moment a key
is set in .env, and otherwise inert (no key -> no network call). Checked
against each provider's own pricing page (Sep 2026):

  4. Geoapify Places API - CONFIRMED free, no credit card, 3,000
     requests/day. A second, independent places/food source, useful as a
     cross-check on a smaller Indian town.
  5. Ticketmaster Discovery API - CONFIRMED free, no credit card, 5,000
     calls/day. The one source here that knows about live events
     (concerts, festivals, parties) on a specific date rather than a
     permanent place.
  6. Foursquare Places API - NOT confirmed free-without-card. Foursquare's
     own pricing page describes "sandbox, then $200/month credit, then
     pay-as-you-go", not an unconditional free tier like the two above.
     The code still works if you already have a key; it just is not
     something to recommend as a guaranteed-free signup.

We ask every enabled source at once and merge what comes back (deduplicated
by name), so a plan is not hostage to one API having a bad day, and a
destination gets the combined judgement of several sources instead of one.

Overpass query language looks strange the first time, so here is the shape
of it:

    [out:json][timeout:25];
    (
      node["tourism"="museum"](around:15000, 26.91, 75.78);
    );
    out center 40;

Read it as: find nodes tagged tourism=museum within 15km of that lat/lon,
give me at most 40 of them as JSON.
"""

import asyncio
import difflib
import math
import re

import config
import recommend
from tools import webclient
from tools.geo import haversine_km

# Nothing further than this from the city centre belongs on a day plan.
MAX_DAY_TRIP_KM = 60

# ---------------------------------------------------------------------------
# What to ask OpenStreetMap for.
#
# v24: the old query asked for e.g. ALL amenity=place_of_worship and ALL
# leisure=park inside 20 km. In an Indian city that is hundreds of
# neighbourhood temples and colony parks, and Overpass returns them in
# database-id order (not importance order), so the first N were effectively
# random - and the code then cut the list to N *before* ranking. That is a
# big part of why plans felt random.
#
# Now there are two groups:
#   SIGHT tags      - a sight by definition (forts, museums, viewpoints,
#                     tourism=attraction). Always asked for.
#   NOTABLE_ONLY    - common things (temples, parks, markets, bars) that we
#                     only want when OSM itself marks them as notable: they
#                     carry a wikidata / heritage / tourism tag.
# ---------------------------------------------------------------------------

SIGHT_TAGS = [("tourism", "attraction"), ("tourism", "museum"), ("tourism", "viewpoint"),
              ("tourism", "gallery"), ("tourism", "zoo"),
              ("historic", "fort"), ("historic", "castle"), ("historic", "monument"),
              ("historic", "ruins"), ("historic", "archaeological_site"),
              ("historic", "palace"), ("historic", "tomb")]

# Which OSM tags to search for, depending on what the user says they like.
# An OSM "tag" is just a key=value label that mappers put on a place.
INTEREST_TAGS = {
    "Nature": [("natural", "peak"), ("leisure", "park"), ("natural", "waterfall"),
               ("tourism", "viewpoint"), ("leisure", "nature_reserve"), ("natural", "water"),
               ("leisure", "garden")],
    "Culture & Heritage": [("historic", "fort"), ("historic", "monument"), ("historic", "castle"),
                           ("tourism", "museum"), ("historic", "ruins"),
                           ("amenity", "place_of_worship")],
    "Spiritual": [("amenity", "place_of_worship")],
    "Adventure": [("tourism", "viewpoint"), ("natural", "peak"), ("sport", "climbing"),
                  ("natural", "cave_entrance")],
    "Shopping": [("shop", "mall"), ("amenity", "marketplace")],
    "Relaxation": [("leisure", "park"), ("natural", "beach"), ("leisure", "garden")],
    "Nightlife": [("amenity", "bar"), ("amenity", "pub"), ("amenity", "nightclub")],
    "Photography": [("tourism", "viewpoint"), ("historic", "monument"), ("natural", "waterfall")],
    # Food is served by the lunch/dinner picks (find_restaurants), not by
    # turning restaurants into sightseeing stops.
    "Food": [],
}

# Too common to take unfiltered - only when OSM marks them notable.
NOTABLE_ONLY = {("amenity", "place_of_worship"), ("leisure", "park"), ("leisure", "garden"),
                ("amenity", "marketplace"), ("natural", "water"), ("natural", "peak"),
                ("shop", "mall")}
NOTABLE_FILTERS = ("wikidata", "heritage", "tourism")

# The same interests, in Geoapify's category names. Before this, the Geoapify
# call always asked for "tourism.sights,entertainment" whatever the traveller
# picked, so Nightlife / Shopping / Relaxation never produced a single stop
# and the review pass (rightly) complained that they were ignored.
INTEREST_GEOAPIFY = {
    "Nature": ["leisure.park", "natural"],
    "Culture & Heritage": ["tourism.sights", "entertainment.culture"],
    "Spiritual": ["religion.place_of_worship"],
    "Adventure": ["tourism.sights", "natural"],
    "Shopping": ["commercial.shopping_mall", "commercial.marketplace"],
    "Relaxation": ["leisure.spa", "leisure.park"],
    "Nightlife": ["catering.bar", "catering.pub", "adult.nightclub"],
    "Photography": ["tourism.sights"],
    "Food": [],
}


def _geoapify_categories(interests: list[str]) -> str:
    cats = ["tourism.sights", "entertainment"]
    for interest in interests:
        cats.extend(INTEREST_GEOAPIFY.get(interest, []))
    return ",".join(dict.fromkeys(cats))


# If the user picks nothing, show the classic tourist stuff.
DEFAULT_TAGS = [("tourism", "attraction"), ("historic", "monument"),
                ("tourism", "museum"), ("historic", "fort"), ("tourism", "viewpoint")]

# Roughly how long people spend at each kind of place. Used by the day planner
# so we do not stuff eight three-hour sites into one afternoon.
VISIT_HOURS = {
    "fort": 2.5, "castle": 2.5, "palace": 2.0, "museum": 2.0, "monument": 1.0, "ruins": 1.5,
    "archaeological_site": 1.5, "place_of_worship": 1.0, "place of worship": 1.0,
    "viewpoint": 0.75, "park": 1.5, "garden": 1.0, "beach": 2.0, "waterfall": 1.5,
    "lake": 1.0, "water": 1.0, "zoo": 2.0, "gallery": 1.5, "peak": 3.0, "marketplace": 1.5,
    "mall": 2.0, "nature_reserve": 3.0, "nature reserve": 3.0, "attraction": 1.5,
    "event": 2.5,
}


def _build_query(lat: float, lon: float, tags: list[tuple[str, str]],
                 radius_m: int, limit: int, notable_only: set | None = None) -> str:
    """
    Build ONE Overpass query from a list of (key, value) tags.

    Tags are grouped by key into one regex statement each, because the free
    Overpass servers time out (504) on 30 separate spatial searches:

        nwr["historic"~"^(fort|monument|castle|ruins)$"](around:20000,26.9,75.8);

    Tags in `notable_only` get one statement per notability filter instead:

        nwr["amenity"~"^(place_of_worship)$"]["wikidata"](around:...);
        nwr["amenity"~"^(place_of_worship)$"]["heritage"](around:...);

    so the neighbourhood shrine without any of those tags never comes back.
    "nwr" means node-way-relation.
    """
    notable_only = notable_only or set()
    plain: dict[str, list[str]] = {}
    filtered: dict[str, list[str]] = {}
    for key, value in tags:
        target = filtered if (key, value) in notable_only else plain
        target.setdefault(key, []).append(value)

    around = f"(around:{radius_m},{lat},{lon})"
    lines = []
    for key, values in plain.items():
        pattern = "|".join(sorted(set(values)))
        lines.append(f'nwr["{key}"~"^({pattern})$"]{around};')
    for key, values in filtered.items():
        pattern = "|".join(sorted(set(values)))
        for flag in NOTABLE_FILTERS:
            if flag == key:
                continue
            lines.append(f'nwr["{key}"~"^({pattern})$"]["{flag}"]{around};')

    body = "\n  ".join(lines)
    # A shorter server-side timeout means we fail fast and try the next mirror
    # instead of the client sitting there for 25 seconds.
    return f"[out:json][timeout:15];\n(\n  {body}\n);\nout center {limit};"


# Which tag decides what a place IS. "historic" first: Amer Fort carries both
# tourism=attraction and historic=castle, and v23 read the tourism tag first,
# so it became a generic "attraction" (6/10) instead of a fort (10/10).
KIND_TAG_ORDER = ("historic", "natural", "tourism", "leisure", "amenity", "shop")

_WEEKDAYS = ["mo", "tu", "we", "th", "fr", "sa", "su"]
_OH_DAY_RANGE = re.compile(r"\b(mo|tu|we|th|fr|sa|su)\s*(?:-\s*(mo|tu|we|th|fr|sa|su))?\b", re.I)


def closed_days_from_opening_hours(text: str | None) -> list[int]:
    """
    Weekdays (0 = Monday) a place is closed on, read from an OSM opening_hours
    string. Deliberately simple: understands "Tu-Su 09:00-17:00" (closed
    Monday), "Mo-Th,Sa-Su 06:00-19:00" (closed Friday) and "Fr off". Anything
    it cannot read confidently returns [] - better no claim than a wrong one.
    """
    if not text or not isinstance(text, str):
        return []
    lowered = text.lower()
    if "24/7" in lowered:
        return []
    open_days: set[int] = set()
    closed: set[int] = set()
    for rule in re.split(r";", lowered):
        rule = rule.strip()
        if not rule or rule.startswith(("ph", "jan", "feb", "mar", "apr", "may", "jun", "jul",
                                        "aug", "sep", "oct", "nov", "dec")):
            continue
        is_off = rule.endswith("off") or rule.endswith("closed")
        days: set[int] = set()
        for match in _OH_DAY_RANGE.finditer(rule.split(" ")[0]):
            a = _WEEKDAYS.index(match.group(1).lower())
            b = _WEEKDAYS.index(match.group(2).lower()) if match.group(2) else a
            i = a
            while True:
                days.add(i)
                if i == b:
                    break
                i = (i + 1) % 7
        if not days:
            continue
        if is_off:
            closed |= days
        else:
            open_days |= days
    if open_days and len(open_days) < 7:
        closed |= set(range(7)) - open_days
    closed -= open_days
    return sorted(closed)


def _clean_results(raw: dict, limit: int, is_restaurant: bool = False) -> list[dict]:
    """Overpass gives us a lot of junk. Keep only places that have a real,
    visitable name - see _fails_visitor_check for what that filters out.

    v24: also keeps the notability tags (wikidata, wikipedia, heritage,
    tourism=attraction) the ranking needs, and opening_hours."""
    places = []
    seen_names = set()

    for element in raw.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name:en") or tags.get("name")

        if not name or name.lower() in seen_names:
            continue
        if _fails_visitor_check(name, is_restaurant=is_restaurant):
            continue
        seen_names.add(name.lower())

        # A node has lat/lon directly. A way has a "center" instead.
        if "lat" in element:
            lat, lon = element["lat"], element["lon"]
        elif "center" in element:
            lat, lon = element["center"]["lat"], element["center"]["lon"]
        else:
            continue

        raw_kind = "attraction"
        for key in KIND_TAG_ORDER:
            value = tags.get(key)
            if not value or value in ("yes", "attraction", "information", "hotel", "guest_house"):
                continue
            raw_kind = value
            break
        kind = recommend.canonical_kind(raw_kind)

        place = {
            "name": name,
            "kind": kind,
            "lat": lat,
            "lon": lon,
            "visit_hours": VISIT_HOURS.get(raw_kind, VISIT_HOURS.get(kind, 1.5)),
            "fee": tags.get("fee", "unknown"),
            "wikipedia": tags.get("wikipedia", ""),
            "wikidata": tags.get("wikidata", ""),
            "heritage": bool(tags.get("heritage")),
            "osm_attraction": tags.get("tourism") in ("attraction", "museum", "viewpoint"),
            "source": "openstreetmap",
        }
        closed = closed_days_from_opening_hours(tags.get("opening_hours"))
        if closed:
            place["closed_days"] = closed
        if is_restaurant:
            place["cuisine"] = tags.get("cuisine", "")
            diet = (tags.get("diet:vegetarian") or "").lower()
            place["vegetarian"] = diet in ("yes", "only") or "pure veg" in name.lower() \
                or bool(re.search(r"\bveg\b", name.lower()))
        places.append(place)

        if len(places) >= limit:
            break

    return places


# ---------------------------------------------------------------------------
# Curated backstop. Overpass mirrors and Wikipedia geosearch can both come
# back thin for a given city on a given day - that is real, not a bug, and
# no amount of retrying fixes it. Rather than leave four days empty, top up
# with a short hand list of well-known, real, named attractions for the
# destinations people actually plan here. These are genuine places (source
# is labelled "curated", not "offline-sample") - just not fetched live.
# Coordinates are approximate (city-landmark precision, not survey-grade),
# which is fine for clustering days by area.
#
# v24: an optional 5th item carries what a map cannot tell you:
#   closed     weekdays it is shut (0 = Monday). The Taj Mahal is closed on
#              Fridays - a planner that sends you there on a Friday is wrong.
#   best       "morning" / "evening" (sunrise points, aartis, sunset views,
#              the Wagah ceremony) - the day planner orders around it.
#   hours      a better visit length than the per-kind default.
# ---------------------------------------------------------------------------
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)

CURATED_ATTRACTIONS: dict[str, list[tuple]] = {
    "dehradun": [
        ("Robber's Cave (Guchhupani)", "ruins", 30.4012, 78.1147),
        ("Sahastradhara", "waterfall", 30.3808, 78.1236),
        ("Forest Research Institute", "museum", 30.3410, 77.9994),
        ("Mindrolling Monastery", "place_of_worship", 30.2444, 78.0844),
        ("Tapkeshwar Temple", "place_of_worship", 30.3542, 78.0089),
        ("Malsi Deer Park", "park", 30.3811, 78.0611),
        ("Clock Tower (Ghanta Ghar)", "monument", 30.3226, 78.0359),
        ("Lachhiwala", "park", 30.2436, 78.1131),
        ("Paltan Bazaar", "marketplace", 30.3235, 78.0420),
        ("Santla Devi Temple", "place_of_worship", 30.3585, 78.0340),
        # Mussoorie is the classic Dehradun day trip (about 35 km up the hill).
        ("Mussoorie Mall Road", "marketplace", 30.4586, 78.0736, {"best": "evening"}),
        ("Kempty Falls", "waterfall", 30.4926, 77.9871),
        ("Gun Hill, Mussoorie", "viewpoint", 30.4560, 78.0664),
        ("Company Garden, Mussoorie", "garden", 30.4640, 78.0790),
    ],
    "mussoorie": [
        ("Kempty Falls", "waterfall", 30.4926, 77.9871),
        ("Gun Hill", "viewpoint", 30.4560, 78.0664),
        ("Mall Road, Mussoorie", "marketplace", 30.4586, 78.0736, {"best": "evening"}),
        ("Camel's Back Road", "viewpoint", 30.4580, 78.0790, {"best": "evening"}),
        ("Lal Tibba", "viewpoint", 30.4735, 78.1077, {"best": "morning"}),
        ("Company Garden, Mussoorie", "garden", 30.4640, 78.0790),
    ],
    "rishikesh": [
        ("Laxman Jhula", "monument", 30.1219, 78.3289),
        ("Ram Jhula", "monument", 30.1097, 78.3196),
        ("Triveni Ghat", "attraction", 30.0965, 78.2915, {"best": "evening"}),
        ("Neelkanth Mahadev Temple", "place_of_worship", 30.1002, 78.4126, {"best": "morning"}),
        ("Parmarth Niketan", "place_of_worship", 30.1223, 78.3212, {"best": "evening"}),
        ("Beatles Ashram", "ruins", 30.1305, 78.3262),
        ("Neer Garh Waterfall", "waterfall", 30.1330, 78.3380),
    ],
    "haridwar": [
        ("Har Ki Pauri", "attraction", 29.9457, 78.1642, {"best": "evening"}),
        ("Chandi Devi Temple", "place_of_worship", 29.9601, 78.1770, {"best": "morning"}),
        ("Mansa Devi Temple", "place_of_worship", 29.9583, 78.1583, {"best": "morning"}),
        ("Maya Devi Temple", "place_of_worship", 29.9459, 78.1652),
    ],
    "jaipur": [
        ("Amer Fort", "fort", 26.9855, 75.8513, {"best": "morning", "hours": 3.0}),
        ("Hawa Mahal", "monument", 26.9239, 75.8267, {"best": "morning"}),
        ("City Palace, Jaipur", "palace", 26.9258, 75.8237),
        ("Jantar Mantar, Jaipur", "monument", 26.9246, 75.8247),
        ("Nahargarh Fort", "fort", 26.9373, 75.8155, {"best": "evening"}),
        ("Jal Mahal", "monument", 26.9535, 75.8465, {"best": "evening", "hours": 0.5}),
        ("Albert Hall Museum", "museum", 26.9114, 75.8195),
        ("Jaigarh Fort", "fort", 26.9853, 75.8458),
        ("Johari Bazaar", "marketplace", 26.9206, 75.8267, {"best": "evening"}),
        ("Birla Mandir, Jaipur", "place_of_worship", 26.8926, 75.8157),
        ("Galta Ji Temple", "place_of_worship", 26.9179, 75.8604),
        ("Patrika Gate", "monument", 26.8429, 75.8085),
    ],
    "delhi": [
        ("Red Fort", "fort", 28.6562, 77.2410, {"closed": [MON]}),
        ("Qutub Minar", "monument", 28.5245, 77.1855),
        ("India Gate", "monument", 28.6129, 77.2295, {"best": "evening"}),
        ("Humayun's Tomb", "monument", 28.5933, 77.2507),
        ("Lotus Temple", "place_of_worship", 28.5535, 77.2588, {"closed": [MON]}),
        ("Jama Masjid, Delhi", "place_of_worship", 28.6507, 77.2334),
        ("Akshardham Temple", "place_of_worship", 28.6127, 77.2773, {"closed": [MON], "hours": 3.0}),
        ("Chandni Chowk", "marketplace", 28.6506, 77.2303),
        ("Lodhi Garden", "garden", 28.5931, 77.2197, {"best": "morning"}),
        ("Hauz Khas Village", "attraction", 28.5535, 77.1943, {"best": "evening"}),
        ("National Museum, New Delhi", "museum", 28.6119, 77.2192, {"closed": [MON]}),
        ("Connaught Place", "marketplace", 28.6315, 77.2167, {"best": "evening"}),
        ("Agrasen ki Baoli", "monument", 28.6265, 77.2251),
        ("Dilli Haat INA", "marketplace", 28.5733, 77.2085),
    ],
    "agra": [
        ("Taj Mahal", "monument", 27.1751, 78.0421, {"closed": [FRI], "best": "morning", "hours": 3.0}),
        ("Agra Fort", "fort", 27.1795, 78.0211),
        ("Fatehpur Sikri", "ruins", 27.0937, 77.6609, {"hours": 2.5}),
        ("Mehtab Bagh", "garden", 27.1783, 78.0424, {"best": "evening"}),
        ("Itimad-ud-Daulah's Tomb", "monument", 27.1926, 78.0312),
        ("Akbar's Tomb, Sikandra", "monument", 27.2206, 77.9505),
    ],
    "udaipur": [
        ("City Palace, Udaipur", "palace", 24.5764, 73.6835, {"hours": 2.5}),
        ("Lake Pichola", "lake", 24.5711, 73.6789, {"best": "evening"}),
        ("Jag Mandir", "monument", 24.5651, 73.6803),
        ("Saheliyon Ki Bari", "garden", 24.6023, 73.6875),
        ("Fateh Sagar Lake", "lake", 24.6039, 73.6788, {"best": "evening"}),
        ("Bagore Ki Haveli", "museum", 24.5800, 73.6820, {"best": "evening"}),
    ],
    "varanasi": [
        ("Kashi Vishwanath Temple", "place_of_worship", 25.3109, 83.0107),
        ("Dashashwamedh Ghat", "attraction", 25.3070, 83.0105, {"best": "evening"}),
        ("Sarnath", "ruins", 25.3811, 83.0243, {"hours": 2.5}),
        ("Assi Ghat", "attraction", 25.2850, 83.0110, {"best": "morning"}),
        ("Manikarnika Ghat", "attraction", 25.3109, 83.0137),
        ("Ramnagar Fort", "fort", 25.2694, 83.0270),
    ],
    "goa": [
        ("Baga Beach", "beach", 15.5553, 73.7519, {"best": "evening"}),
        ("Calangute Beach", "beach", 15.5439, 73.7553),
        ("Basilica of Bom Jesus", "place_of_worship", 15.5009, 73.9114),
        ("Fort Aguada", "fort", 15.4925, 73.7738),
        ("Dudhsagar Falls", "waterfall", 15.3144, 74.3144),
        ("Chapora Fort", "fort", 15.6062, 73.7360, {"best": "evening"}),
    ],
    "panaji": [
        ("Basilica of Bom Jesus", "place_of_worship", 15.5009, 73.9114),
        ("Fort Aguada", "fort", 15.4925, 73.7738),
        ("Baga Beach", "beach", 15.5553, 73.7519, {"best": "evening"}),
        ("Calangute Beach", "beach", 15.5439, 73.7553),
        ("Fontainhas", "attraction", 15.4970, 73.8310),
    ],
    "mumbai": [
        ("Gateway of India", "monument", 18.9220, 72.8347),
        ("Marine Drive", "attraction", 18.9440, 72.8235, {"best": "evening"}),
        ("Elephanta Caves", "ruins", 18.9633, 72.9315, {"closed": [MON], "hours": 4.0}),
        ("Chhatrapati Shivaji Terminus", "monument", 18.9398, 72.8355),
        ("Chhatrapati Shivaji Maharaj Vastu Sangrahalaya", "museum", 18.9269, 72.8326),
        ("Siddhivinayak Temple", "place_of_worship", 19.0170, 72.8306),
        ("Haji Ali Dargah", "place_of_worship", 18.9827, 72.8089),
        ("Juhu Beach", "beach", 19.0988, 72.8267, {"best": "evening"}),
        ("Dhobi Ghat, Mahalaxmi", "attraction", 18.9827, 72.8259, {"best": "morning"}),
        ("Colaba Causeway", "marketplace", 18.9150, 72.8258),
        ("Sanjay Gandhi National Park", "nature_reserve", 19.2147, 72.9106, {"closed": [MON]}),
    ],
    "kolkata": [
        ("Victoria Memorial", "monument", 22.5448, 88.3426, {"closed": [MON]}),
        ("Howrah Bridge", "monument", 22.5851, 88.3468),
        ("Indian Museum", "museum", 22.5579, 88.3511, {"closed": [MON]}),
        ("Dakshineswar Kali Temple", "place_of_worship", 22.6548, 88.3575, {"best": "morning"}),
        ("Belur Math", "place_of_worship", 22.6322, 88.3560),
        ("Kalighat Kali Temple", "place_of_worship", 22.5203, 88.3426),
        ("Prinsep Ghat", "attraction", 22.5553, 88.3304, {"best": "evening"}),
        ("South Park Street Cemetery", "monument", 22.5434, 88.3597),
        ("New Market, Kolkata", "marketplace", 22.5600, 88.3520),
        ("Science City, Kolkata", "museum", 22.5392, 88.3958),
    ],
    "bengaluru": [
        ("Bangalore Palace", "palace", 12.9988, 77.5921),
        ("Lalbagh Botanical Garden", "garden", 12.9507, 77.5848, {"best": "morning"}),
        ("Cubbon Park", "park", 12.9763, 77.5929, {"best": "morning"}),
        ("Tipu Sultan's Summer Palace", "palace", 12.9593, 77.5737),
        ("ISKCON Temple Bangalore", "place_of_worship", 13.0098, 77.5511),
        ("Bull Temple (Dodda Basavana Gudi)", "place_of_worship", 12.9425, 77.5681),
        ("Visvesvaraya Industrial and Technological Museum", "museum", 12.9752, 77.5963),
        ("Commercial Street", "marketplace", 12.9822, 77.6085),
        ("Nandi Hills", "viewpoint", 13.3702, 77.6835, {"best": "morning", "hours": 3.0}),
    ],
    "hyderabad": [
        ("Charminar", "monument", 17.3616, 78.4747),
        ("Golconda Fort", "fort", 17.3833, 78.4011, {"best": "evening", "hours": 3.0}),
        ("Salar Jung Museum", "museum", 17.3713, 78.4804, {"closed": [FRI], "hours": 2.5}),
        ("Chowmahalla Palace", "palace", 17.3578, 78.4717, {"closed": [FRI]}),
        ("Hussain Sagar", "lake", 17.4239, 78.4738, {"best": "evening"}),
        ("Qutb Shahi Tombs", "monument", 17.3949, 78.3961),
        ("Laad Bazaar", "marketplace", 17.3609, 78.4732, {"best": "evening"}),
        ("Birla Mandir, Hyderabad", "place_of_worship", 17.4062, 78.4691),
        ("Ramoji Film City", "attraction", 17.2543, 78.6808, {"hours": 6.0}),
    ],
    "chennai": [
        ("Marina Beach", "beach", 13.0500, 80.2824, {"best": "evening"}),
        ("Kapaleeshwarar Temple", "place_of_worship", 13.0339, 80.2697),
        ("Fort St. George", "fort", 13.0796, 80.2873, {"closed": [FRI]}),
        ("San Thome Basilica", "place_of_worship", 13.0334, 80.2777),
        ("Government Museum, Chennai", "museum", 13.0697, 80.2563, {"closed": [FRI]}),
        ("Shore Temple, Mahabalipuram", "ruins", 12.6165, 80.1993, {"hours": 3.0}),
        ("DakshinaChitra", "museum", 12.8236, 80.2427, {"closed": [TUE]}),
    ],
    "jodhpur": [
        ("Mehrangarh Fort", "fort", 26.2978, 73.0185, {"best": "morning", "hours": 3.0}),
        ("Umaid Bhawan Palace", "palace", 26.2808, 73.0471),
        ("Jaswant Thada", "monument", 26.3016, 73.0237, {"best": "evening"}),
        ("Clock Tower and Sardar Market", "marketplace", 26.2943, 73.0249, {"best": "evening"}),
        ("Mandore Gardens", "garden", 26.3513, 73.0369),
        ("Toorji Ka Jhalra", "monument", 26.2935, 73.0226),
    ],
    "darjeeling": [
        ("Tiger Hill", "viewpoint", 26.9984, 88.2786, {"best": "morning"}),
        ("Batasia Loop", "monument", 27.0136, 88.2479),
        ("Padmaja Naidu Himalayan Zoological Park", "zoo", 27.0587, 88.2552, {"closed": [THU]}),
        ("Peace Pagoda, Darjeeling", "place_of_worship", 27.0298, 88.2537),
        ("Darjeeling Himalayan Railway", "attraction", 27.0410, 88.2663, {"hours": 2.0}),
        ("Chowrasta (The Mall)", "marketplace", 27.0440, 88.2665, {"best": "evening"}),
    ],
    "leh": [
        ("Shanti Stupa", "monument", 34.1735, 77.5747, {"best": "evening"}),
        ("Leh Palace", "palace", 34.1655, 77.5860),
        ("Thiksey Monastery", "place_of_worship", 34.0559, 77.6672, {"best": "morning"}),
        ("Hall of Fame, Leh", "museum", 34.1365, 77.5577),
        ("Hemis Monastery", "place_of_worship", 33.9125, 77.7031, {"hours": 2.0}),
        ("Leh Market", "marketplace", 34.1640, 77.5850, {"best": "evening"}),
    ],
    "kochi": [
        ("Chinese Fishing Nets, Fort Kochi", "attraction", 9.9682, 76.2425, {"best": "evening"}),
        ("Mattancherry Palace", "palace", 9.9582, 76.2595, {"closed": [FRI]}),
        ("Paradesi Synagogue", "place_of_worship", 9.9573, 76.2594, {"closed": [FRI, SAT]}),
        ("St. Francis Church, Kochi", "place_of_worship", 9.9658, 76.2421),
        ("Jew Town, Mattancherry", "marketplace", 9.9575, 76.2600),
        ("Marine Drive, Kochi", "attraction", 9.9779, 76.2763, {"best": "evening"}),
    ],
    "mysuru": [
        ("Mysore Palace", "palace", 12.3052, 76.6552, {"hours": 2.5}),
        ("Chamundi Hill", "place_of_worship", 12.2724, 76.6705, {"best": "morning"}),
        ("Brindavan Gardens", "garden", 12.4216, 76.5727, {"best": "evening"}),
        ("St. Philomena's Church", "place_of_worship", 12.3209, 76.6584),
        ("Mysore Zoo", "zoo", 12.3022, 76.6649, {"closed": [TUE]}),
        ("Devaraja Market", "marketplace", 12.3105, 76.6519),
    ],
    "manali": [
        ("Solang Valley", "viewpoint", 32.3168, 77.1569, {"hours": 3.0}),
        ("Hadimba Temple", "place_of_worship", 32.2497, 77.1892),
        ("Old Manali", "marketplace", 32.2560, 77.1745, {"best": "evening"}),
        ("Rohtang Pass", "peak", 32.3722, 77.2494, {"closed": [TUE], "best": "morning"}),
        ("Jogini Falls", "waterfall", 32.2632, 77.1937),
        ("Vashisht Temple", "place_of_worship", 32.2662, 77.1876),
    ],
    "shimla": [
        ("The Ridge, Shimla", "viewpoint", 31.1035, 77.1730, {"best": "evening"}),
        ("Mall Road, Shimla", "marketplace", 31.1041, 77.1726, {"best": "evening"}),
        ("Jakhoo Temple", "place_of_worship", 31.1044, 77.1868, {"best": "morning"}),
        ("Kufri", "viewpoint", 31.0996, 77.2669),
        ("Christ Church, Shimla", "place_of_worship", 31.1040, 77.1757),
    ],
    "amritsar": [
        ("Golden Temple", "place_of_worship", 31.6200, 74.8765, {"hours": 2.5}),
        ("Wagah Border", "attraction", 31.6048, 74.5738, {"best": "evening", "hours": 3.0}),
        ("Jallianwala Bagh", "monument", 31.6203, 74.8800),
        ("Partition Museum", "museum", 31.6265, 74.8768, {"closed": [MON]}),
        ("Gobindgarh Fort", "fort", 31.6331, 74.8577, {"best": "evening"}),
    ],
}
# Common alternative spellings.
CURATED_ATTRACTIONS["new delhi"] = CURATED_ATTRACTIONS["delhi"]
CURATED_ATTRACTIONS["bangalore"] = CURATED_ATTRACTIONS["bengaluru"]
CURATED_ATTRACTIONS["mysore"] = CURATED_ATTRACTIONS["mysuru"]
CURATED_ATTRACTIONS["calcutta"] = CURATED_ATTRACTIONS["kolkata"]
CURATED_ATTRACTIONS["bombay"] = CURATED_ATTRACTIONS["mumbai"]
CURATED_ATTRACTIONS["madras"] = CURATED_ATTRACTIONS["chennai"]
CURATED_ATTRACTIONS["cochin"] = CURATED_ATTRACTIONS["kochi"]
CURATED_ATTRACTIONS["benares"] = CURATED_ATTRACTIONS["varanasi"]
CURATED_ATTRACTIONS["banaras"] = CURATED_ATTRACTIONS["varanasi"]


def _normalize_name(name: str) -> str:
    """Strip spacing/punctuation so 'Amer Fort' and 'Amer  Fort,' match."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _has_coords(p: dict) -> bool:
    return p.get("lat") is not None and p.get("lon") is not None and not p.get("approx_location")


# Evidence worth keeping when two sources describe the same place.
_CARRY_OVER = ("wikidata", "wikipedia", "heritage", "sitelinks", "has_image", "osm_attraction",
               "closed_days", "best_time", "blurb")
# (Foursquare ratings are copied separately by _attach_quality_signals.)


def _absorb_duplicate(keeper: dict, other: dict) -> None:
    for key in _CARRY_OVER:
        if not keeper.get(key) and other.get(key):
            keeper[key] = other[key]


def _merge_sources(*lists: list[dict], limit: int) -> list[dict]:
    """
    Combine results from several sources into one list, keeping the first
    time each real-world place shows up and dropping the rest once we have
    enough.

    v24: "the same place" is no longer only an exact name match. "Hawa Mahal",
    "Hawa Mahal, Jaipur" and "Palace of Winds (Hawa Mahal)" within a few
    hundred metres are one place (see _same_place) - v23 kept all three as
    separate stops. The surviving entry also inherits useful evidence
    (wikidata id, heritage tag, rating) from the duplicates.
    """
    seen: dict[str, dict] = {}
    merged: list[dict] = []
    for source_list in lists:
        for place in source_list:
            key = _normalize_name(place.get("name", ""))
            if not key:
                continue
            if key in seen:
                _absorb_duplicate(seen[key], place)
                continue
            twin = None
            if _has_coords(place):
                twin = next((m for m in merged if _has_coords(m) and _same_place(m, place)), None)
            if twin is not None:
                _absorb_duplicate(twin, place)
                seen[key] = twin
                continue
            if len(merged) >= limit:
                continue
            seen[key] = place
            merged.append(place)
    return merged[:limit]


# ---------------------------------------------------------------------------
# Tourist-worthiness ranking.
#
# Why this exists: the pool of "real places" used to be taken in whatever
# order the sources answered, so a Dehradun day could open with "Sabzi Mandi"
# (a wholesale vegetable market) and "RAM LEELA BAZAR" while Robber's Cave and
# Sahastradhara sat unused. Everything that is real is not worth a day.
#
# The scoring itself lives in recommend.py (pure, unit-tested, 0-100 with a
# breakdown, confidence and a "why"). This file only gathers candidates and
# attaches the evidence the engine needs.
# ---------------------------------------------------------------------------
UTILITY_RE = recommend.HARD_NEGATIVE_RE
FAMOUS_MARKET_HINTS = recommend.FAMOUS_MARKET_HINTS
KIND_BASE = recommend.KIND_BASE
INTEREST_KINDS = recommend.INTEREST_KINDS

# A score at or above this earns the "Must-see" badge in the UI - but only when
# something independent vouches for the place (see rank_places).
MUST_SEE_SCORE = 68
# Below this a place is only used if the pool would otherwise be too thin.
MIN_GOOD_SCORE = 35


DOCUMENTED_FLOOR = 22


def _documented(p: dict) -> bool:
    ev = (p.get("recommendation") or {}).get("evidence") or {}
    return bool(ev.get("curated") or ev.get("wikipedia") or ev.get("wikidata")
                or ev.get("heritage") or (ev.get("sources") or 1) >= 2)


def _is_utility_place(name: str) -> bool:
    return recommend.is_hard_negative(name) or (
        recommend.is_soft_negative(name) and not recommend.names_a_sight(name)
        and not recommend._is_famous_market(name))


def _source_votes(*lists: list[dict]) -> dict[str, int]:
    """
    How many independent source lists name each place.

    v24: a place counts as "named" by a source when that source has the same
    name OR a near-identical name within 1.5 km ("Amer Fort" / "Amber Fort",
    "Hawa Mahal" / "Hawa Mahal, Jaipur"). v23 only counted exact names, so
    real agreement between sources was mostly invisible to the ranking.
    """
    groups: list[dict] = []        # [{"rep": place, "keys": set(), "sources": set()}]
    by_key: dict[str, dict] = {}
    for index, source_list in enumerate(lists):
        for place in source_list:
            key = _normalize_name(place.get("name", ""))
            if not key:
                continue
            group = by_key.get(key)
            if group is None and _has_coords(place):
                group = next((g for g in groups if _has_coords(g["rep"])
                              and _same_place(g["rep"], place)), None)
            if group is None:
                group = {"rep": place, "keys": set(), "sources": set()}
                groups.append(group)
            group["keys"].add(key)
            group["sources"].add(index)
            by_key[key] = group
    votes: dict[str, int] = {}
    for group in groups:
        for key in group["keys"]:
            votes[key] = len(group["sources"])
    return votes


def score_place(place: dict, interests: list[str], votes: dict[str, int] | None = None,
                ctx: dict | None = None) -> int:
    """0-100 recommendation score; -100 means 'never recommend this'."""
    result = recommend.score_attraction(place, interests, votes, ctx)
    return -100 if result is None else result["score"]


def rank_places(places: list[dict], interests: list[str],
                votes: dict[str, int] | None = None,
                min_keep: int = 0, ctx: dict | None = None) -> list[dict]:
    """
    Score, drop the clearly-not-a-destination, and sort best first. Every
    survivor carries `score`, `must_see`, `confidence`, `why` and the full
    `recommendation` breakdown so the interface can explain itself.

    Restaurants and cafes are never sightseeing stops (they become lunch
    picks), even for a Food traveller.

    min_keep: if fewer than this many places clear MIN_GOOD_SCORE, the best of
    the rest are kept so a small town does not end up with empty days. Places
    that fail the negative filters never come back, even then.
    """
    scored = []
    for place in places:
        if recommend.canonical_kind(place.get("kind")) in recommend.MEAL_KINDS:
            continue
        rec = recommend.score_attraction(place, interests, votes, ctx)
        if rec is None:
            continue
        item = dict(place)
        item["kind"] = rec["kind"]
        item["score"] = rec["score"]
        item["source_votes"] = rec["evidence"]["sources"]
        item["recommendation"] = rec
        item["confidence"] = rec["confidence"]
        item["why"] = rec["why"]
        ev = rec["evidence"]
        # "Must-see" is a claim, so it needs evidence as well as a high score.
        item["must_see"] = rec["score"] >= MUST_SEE_SCORE and bool(
            ev["curated"] or ev["wikipedia"] or ev["heritage"] or ev["sources"] >= 2
            or ev["rating"] is not None or (ev.get("sitelinks") or 0) >= 5)
        scored.append(item)

    scored.sort(key=lambda p: p["score"], reverse=True)
    # A well-documented landmark that simply is not what this traveller asked
    # for (Marine Drive for a "Culture + Food" trip) stays in the list, low
    # down, as a back-up / "if you have time" idea instead of vanishing.
    good = [p for p in scored if p["score"] >= MIN_GOOD_SCORE
            or (p["score"] >= DOCUMENTED_FLOOR and _documented(p))]
    if len(good) >= min_keep:
        return good
    return scored[: max(min_keep, len(good))]


def rerank_places(places: list[dict], interests: list[str], ctx: dict | None = None,
                  min_keep: int = 0) -> list[dict]:
    """
    Score an already-ranked list again with fresh context (where the traveller
    sleeps, what the budget solver decided, the forecast). Source votes are
    remembered on each place, so nothing is lost between the two passes.
    """
    votes = {}
    for p in places:
        votes[_normalize_name(p.get("name", ""))] = p.get("source_votes", 1)
    return rank_places(places, interests, votes, min_keep=min_keep, ctx=ctx)


_BRACKETS = re.compile(r"\(([^)]*)\)")


def _name_variants(name: str) -> set[str]:
    """'Palace of Winds (Hawa Mahal), Jaipur' -> {'palaceofwinds', 'hawamahal', ...}"""
    variants = {_normalize_name(name)}
    for inner in _BRACKETS.findall(name or ""):
        variants.add(_normalize_name(inner))
    outer = _BRACKETS.sub(" ", name or "")
    variants.add(_normalize_name(outer))
    variants.add(_normalize_name(outer.split(",")[0]))
    return {v for v in variants if len(v) >= 4}


def _same_place(a: dict, b: dict) -> bool:
    """Same real-world place under two names: within 1.5 km AND one name
    contains the other, or the names are near-identical ('Amer Fort' vs
    'Amber Fort')."""
    if a.get("lat") is None or b.get("lat") is None:
        return False
    if haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) >= 1.5:
        return False
    va, vb = _name_variants(a.get("name", "")), _name_variants(b.get("name", ""))
    if not va or not vb:
        return False
    for x in va:
        for y in vb:
            if x == y or x in y or y in x:
                return True
            if min(len(x), len(y)) >= 6 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.88:
                return True
    return False


def _curated_entry(entry: tuple) -> tuple[str, str, float, float, dict]:
    name, kind, lat, lon = entry[:4]
    info = entry[4] if len(entry) > 4 else {}
    return name, kind, lat, lon, info


def curated_info(city: str) -> list[dict]:
    """Every hand-picked entry for a city as a plain dict (used by the eval script)."""
    return [{"name": n, "kind": k, "lat": la, "lon": lo, **i}
            for n, k, la, lo, i in map(_curated_entry, CURATED_ATTRACTIONS.get(city.strip().lower(), []))]


def _curated_attractions(city: str, exclude_names: set[str] | None = None,
                         existing: list[dict] | None = None) -> list[dict]:
    """Real, named landmarks for a known city. Never fetched live, so it
    works even when both Overpass and Wikipedia are having a bad day.

    `existing` is the live pool: a curated entry is skipped when the same
    place is already in it (even under a slightly different name)."""
    exclude_names = exclude_names or set()
    existing = existing or []
    entries = CURATED_ATTRACTIONS.get(city.strip().lower(), [])
    out = []
    # The hand-picked lists run from the headline sight downwards, so a
    # place's position is itself a quality signal ("curated_rank", 0 = the
    # sight the city is known for).
    for rank, entry in enumerate(entries):
        name, kind, lat, lon, info = _curated_entry(entry)
        if name.lower() in exclude_names:
            continue
        candidate = {"name": name, "lat": lat, "lon": lon}
        matches = [p for p in existing if _same_place(candidate, p)]
        extra = {}
        if info.get("closed"):
            extra["closed_days"] = list(info["closed"])
        if info.get("best"):
            extra["best_time"] = info["best"]
        if matches:
            # The live source already has it. Keep the live entry (more
            # precise coordinates) but remember a hand-picked list also
            # vouches for it, so it ranks like the landmark it is.
            for match in matches:
                match["curated_match"] = True
                match["curated_rank"] = min(rank, match.get("curated_rank", rank))
                for key, value in extra.items():
                    match.setdefault(key, value)
                if info.get("hours"):
                    match["visit_hours"] = info["hours"]
            continue
        out.append({
            "name": name,
            "kind": kind.replace("_", " "),
            "lat": lat,
            "lon": lon,
            "visit_hours": info.get("hours") or VISIT_HOURS.get(kind, 1.5),
            "fee": "unknown",
            "wikipedia": name.replace(" ", "_"),
            "source": "curated",
            "curated_rank": rank,
            **extra,
        })
    return out


# ---------------------------------------------------------------------------
# Popularity from Wikidata - free, keyless, and works for every Indian city.
#
# Every OSM object with a `wikidata` tag (and every Wikipedia page) has a
# Wikidata item. The number of Wikipedia language editions that cover it
# ("sitelinks") is a remarkably good popularity signal: the Taj Mahal has
# 150+, Amer Fort ~40, a neighbourhood temple 0-2. One batched API call per
# 50 places, cached for a month.
# ---------------------------------------------------------------------------

_NOT_ENCYCLOPAEDIA = {"commonswiki", "specieswiki", "metawiki", "wikidatawiki", "sourceswiki",
                      "mediawikiwiki", "outreachwiki", "wikimaniawiki", "incubatorwiki"}


def _count_sitelinks(entity: dict) -> int:
    links = entity.get("sitelinks") or {}
    return sum(1 for site in links if site.endswith("wiki") and site not in _NOT_ENCYCLOPAEDIA)


async def enrich_with_wikidata(places: list[dict]) -> None:
    """Attach `sitelinks` to every place that has a Wikidata id. In-place, never raises."""
    ids = sorted({p["wikidata"] for p in places
                  if isinstance(p.get("wikidata"), str) and re.fullmatch(r"Q\d+", p["wikidata"])})
    if not ids:
        return
    counts: dict[str, int] = {}
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        data = await webclient.get_json(
            config.WIKIDATA_API_URL,
            params={"action": "wbgetentities", "ids": "|".join(batch), "props": "sitelinks",
                    "format": "json"},
            label="wikidata_sitelinks",
            ttl=60 * 60 * 24 * 30,
        )
        if not data:
            continue
        for qid, entity in (data.get("entities") or {}).items():
            if isinstance(entity, dict) and "missing" not in entity:
                counts[qid] = _count_sitelinks(entity)
    for p in places:
        if p.get("wikidata") in counts:
            p["sitelinks"] = counts[p["wikidata"]]


# ---------------------------------------------------------------------------
# Optional real-data upgrades: Foursquare and Geoapify.
#
# OSM + Wikipedia + Wikivoyage above need no key and are always on. These
# two are extra, independent opinions that get merged in the moment a key
# is set in .env (FOURSQUARE_API_KEY / GEOAPIFY_API_KEY) - with no key each
# function below returns [] immediately, no network call, so nothing about
# the app's behaviour changes if you leave them blank.
# ---------------------------------------------------------------------------

# Field names differ between the new Places API and the retired v3 one
# (fsq_id -> fsq_place_id, geocodes -> latitude/longitude). Asking the new API
# for old names is an error, so send the set that matches the host.
FSQ_FIELDS_NEW = "fsq_place_id,name,latitude,longitude,categories,location,rating,popularity,price,stats"
FSQ_FIELDS_V3 = "fsq_id,name,geocodes,categories,location,rating,popularity,price,stats"


def fsq_fields() -> str:
    return FSQ_FIELDS_NEW if "places-api.foursquare.com" in config.FOURSQUARE_URL else FSQ_FIELDS_V3


FSQ_FIELDS = FSQ_FIELDS_NEW   # kept for older imports


def _fsq_headers() -> dict:
    """Foursquare moved to a new host that wants a Bearer token and a version header."""
    headers = {"Accept": "application/json"}
    if "places-api.foursquare.com" in config.FOURSQUARE_URL:
        headers["Authorization"] = f"Bearer {config.FOURSQUARE_API_KEY}"
        headers["X-Places-Api-Version"] = getattr(config, "FOURSQUARE_API_VERSION", "2025-06-17")
    else:
        headers["Authorization"] = config.FOURSQUARE_API_KEY
    return headers


def _fsq_coords(place: dict) -> tuple[float | None, float | None]:
    main = (place.get("geocodes") or {}).get("main") or {}
    lat = main.get("latitude", place.get("latitude"))
    lon = main.get("longitude", place.get("longitude"))
    return lat, lon


async def _foursquare_search(lat: float, lon: float, query: str,
                             radius_m: int, limit: int, is_restaurant: bool = False) -> list[dict]:
    """
    Foursquare Places - free-text search. Unlike the other sources it carries
    QUALITY signals (rating 0-10, popularity 0-1, price tier 1-4), and we ask
    for them explicitly: a source that only adds more place NAMES does nothing
    for recommendation quality.
    """
    if not config.FOURSQUARE_ENABLED:
        return []

    data = await webclient.get_json(
        config.FOURSQUARE_URL,
        params={"ll": f"{lat},{lon}", "query": query,
                "radius": min(radius_m, 100000), "limit": min(limit, 50),
                "sort": "RELEVANCE", "fields": fsq_fields()},
        headers=_fsq_headers(),
        label="foursquare_search",
        ttl=60 * 60 * 24,
    )
    if not data:
        return []

    out = []
    for place in data.get("results", []):
        name = place.get("name")
        p_lat, p_lon = _fsq_coords(place)
        if not name or p_lat is None or p_lon is None:
            continue
        if _fails_visitor_check(name, is_restaurant=is_restaurant):
            continue
        categories = place.get("categories") or []
        kind = recommend.canonical_kind(categories[0]["name"] if categories else "attraction")
        stats = place.get("stats") or {}
        out.append({
            "name": name,
            "kind": kind,
            "lat": p_lat,
            "lon": p_lon,
            "visit_hours": VISIT_HOURS.get(kind, 1.0),
            "fee": "unknown",
            "wikipedia": "",
            "blurb": (place.get("location") or {}).get("formatted_address", ""),
            "source": "foursquare",
            "fsq_rating": place.get("rating"),
            "fsq_popularity": place.get("popularity"),
            "fsq_price": place.get("price"),
            "fsq_ratings_count": stats.get("total_ratings"),
        })
    return out


# Interest -> a Foursquare search phrase, so quality data covers what THIS
# traveller wants and not just generic sights.
FSQ_INTEREST_QUERY = {
    "Food": "famous local food", "Nightlife": "bar lounge", "Shopping": "popular market",
    "Nature": "nature park viewpoint", "Adventure": "adventure trek", "Relaxation": "spa garden",
    "Culture & Heritage": "heritage monument museum", "Photography": "scenic viewpoint",
    "Spiritual": "famous temple",
}


async def _foursquare_for_interests(lat: float, lon: float, interests: list[str],
                                    radius_m: int, limit: int) -> list[dict]:
    """The generic sights query plus up to two interest-specific ones."""
    if not config.FOURSQUARE_ENABLED:
        return []
    queries = ["tourist attraction"] + [FSQ_INTEREST_QUERY[i] for i in interests[:2]
                                        if i in FSQ_INTEREST_QUERY]
    results = await asyncio.gather(*[_foursquare_search(lat, lon, q, radius_m, limit)
                                     for q in queries])
    return [p for batch in results for p in batch]


def _attach_quality_signals(merged: list[dict], from_fsq: list[dict]) -> None:
    """
    Merging keeps the first source's entry for a place, which would throw away
    the Foursquare rating. Copy those signals onto whichever entry survived.
    """
    for fsq in from_fsq:
        if fsq.get("fsq_rating") is None and fsq.get("fsq_popularity") is None:
            continue
        for place in merged:
            if place is fsq or not _same_place(place, fsq):
                continue
            for key in ("fsq_rating", "fsq_popularity", "fsq_price", "fsq_ratings_count"):
                if place.get(key) is None and fsq.get(key) is not None:
                    place[key] = fsq[key]
            break


async def _geoapify_search(lat: float, lon: float, categories: str,
                           radius_m: int, limit: int, is_restaurant: bool = False) -> list[dict]:
    """Geoapify Places API - category search, backed by OSM plus Geoapify's own POI layer."""
    if not config.GEOAPIFY_ENABLED:
        return []

    data = await webclient.get_json(
        config.GEOAPIFY_PLACES_URL,
        params={"categories": categories, "filter": f"circle:{lon},{lat},{radius_m}",
                "bias": f"proximity:{lon},{lat}", "limit": min(limit, 50),
                "apiKey": config.GEOAPIFY_API_KEY},
        label="geoapify_places",
        ttl=60 * 60 * 24,
    )
    if not data:
        return []

    out = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        name = props.get("name")
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        p_lon, p_lat = coords[0], coords[1]
        if not name or p_lat is None or p_lon is None:
            continue
        if _fails_visitor_check(name, is_restaurant=is_restaurant):
            continue
        cats = props.get("categories") or []
        # Geoapify lists several categories, generic first ("tourism.sights",
        # "tourism.sights.castle"). Use the most specific one we recognise.
        kind = "attraction"
        for cat in cats:
            guess = recommend.canonical_kind(cat.split(".")[-1])
            if guess != "attraction":
                kind = guess
                break
        out.append({
            "name": name,
            "kind": kind,
            "lat": p_lat,
            "lon": p_lon,
            "visit_hours": VISIT_HOURS.get(kind, 1.0),
            "fee": "unknown",
            "wikipedia": "",
            "blurb": props.get("formatted", ""),
            "source": "geoapify",
        })
    return out




async def _ticketmaster_events(lat: float, lon: float, limit: int,
                               start_date: str | None = None,
                               end_date: str | None = None) -> list[dict]:
    """
    Live events (concerts, festivals, parties) near the destination -
    Ticketmaster is the one source in this project that knows about things
    happening on a specific date, rather than a permanent place. Optional
    (TICKETMASTER_API_KEY in .env) - returns [] with no key and no network
    call. Coverage is strongest in the US/UK/EU/AU/NZ and thin for India, so
    an empty list here means "nothing listed", not "nothing happening".

    v24: when the trip dates are known, only events ON those dates are asked
    for. v23 asked for "the next events, soonest first", so a December trip
    could get a concert from October as a stop.
    """
    if not config.TICKETMASTER_ENABLED:
        return []

    params = {"apikey": config.TICKETMASTER_API_KEY, "latlong": f"{lat},{lon}",
              "radius": "50", "unit": "km", "size": min(limit, 20), "sort": "date,asc"}
    if start_date:
        params["startDateTime"] = f"{start_date}T00:00:00Z"
    if end_date:
        params["endDateTime"] = f"{end_date}T23:59:59Z"

    data = await webclient.get_json(
        config.TICKETMASTER_URL,
        params=params,
        label="ticketmaster_events",
        ttl=60 * 60 * 6,  # events change fast - hours, not the weeks everything else gets
    )
    if not data:
        return []

    out = []
    for event in (data.get("_embedded") or {}).get("events", []):
        venue = ((event.get("_embedded") or {}).get("venues") or [{}])[0]
        v_loc = venue.get("location") or {}
        start = (event.get("dates") or {}).get("start") or {}
        local_date = start.get("localDate", "")
        # Belt and braces: drop anything the API returned outside the trip.
        if local_date and start_date and local_date < start_date:
            continue
        if local_date and end_date and local_date > end_date:
            continue
        try:
            p_lat = float(v_loc.get("latitude", lat))
            p_lon = float(v_loc.get("longitude", lon))
        except (TypeError, ValueError):
            p_lat, p_lon = lat, lon

        when = " ".join(x for x in (local_date, start.get("localTime", "")) if x)
        name = event.get("name", "")
        item = {
            "name": f"{name} ({when})" if when else name,
            "kind": "event",
            "lat": p_lat,
            "lon": p_lon,
            "visit_hours": 2.5,
            "fee": "unknown",
            "wikipedia": "",
            "blurb": venue.get("name", ""),
            "source": "ticketmaster",
            "best_time": "evening",
        }
        if local_date:
            item["only_on"] = local_date
        out.append(item)
    return out


async def find_attractions(lat: float, lon: float, interests: list[str],
                           radius_km: int = 20, limit: int = 30,
                           num_days: int = 3, city: str = "",
                           start_date: str | None = None, end_date: str | None = None,
                           preferences: dict | None = None) -> list[dict]:
    """
    Real things to see near the destination, merged from three always-on
    sources (OpenStreetMap, Wikipedia, Wikivoyage) plus Foursquare /
    Geoapify / Ticketmaster when a key is configured.

    Pipeline:
      1. ask every source at once (asyncio.gather)
      2. count how many independent sources name each place
      3. merge, collapsing the same place under different names
      4. add Wikidata popularity (language editions) - free, one call
      5. add the hand-picked landmarks for known cities
      6. drop anything absurdly far away, then rank (recommend.py)

    About 5 candidates per day are kept, never fewer than 20.
    """
    preferences = preferences or {}
    interest_list = list(interests) + [i for i in preferences.get("extra_interests", [])
                                       if i not in interests]
    tags = list(SIGHT_TAGS)
    for interest in interest_list:
        tags.extend(INTEREST_TAGS.get(interest, []))
    if not interest_list:
        tags.extend(DEFAULT_TAGS)

    # remove duplicates while keeping the order
    tags = list(dict.fromkeys(tags))

    wanted = max(limit, num_days * 5, 20)

    # A generous raw limit: the query only asks for sights / notable things,
    # and ranking happens on the whole pool, not on whatever came back first.
    query = _build_query(lat, lon, tags, radius_km * 1000, 400, NOTABLE_ONLY)

    raw, from_wiki, from_wv, from_fsq, from_geo, from_events = await asyncio.gather(
        webclient.overpass(query, label="overpass_attractions", ttl=60 * 60 * 24 * 7),
        wikipedia_nearby(lat, lon, wanted * 2, destination=city),
        wikivoyage_nearby(lat, lon, wanted * 2, wanted_kinds=("see", "do"), destination=city),
        _foursquare_for_interests(lat, lon, interest_list, radius_km * 1000, wanted),
        _geoapify_search(lat, lon, _geoapify_categories(interest_list), radius_km * 1000, wanted),
        _ticketmaster_events(lat, lon, wanted, start_date, end_date),
    )

    # If the interest-specific Geoapify request came back empty (for example a
    # category name the API rejected), retry once with the safe default pair so
    # we never lose the whole source.
    if not from_geo and config.GEOAPIFY_ENABLED and interest_list:
        from_geo = await _geoapify_search(lat, lon, "tourism.sights,entertainment",
                                          radius_km * 1000, wanted)

    osm_places = _clean_results(raw, 400) if raw else []
    # Wikivoyage listings without their own coordinates cannot be placed on a
    # route honestly (v23 put them all on the city centre).
    from_wv = [p for p in from_wv if not p.get("approx_location")]

    # Count how many independent sources name each place BEFORE merging
    # collapses them - two or three sources agreeing is the strongest sign a
    # place is a real, known attraction rather than a corner of the map.
    sources = (osm_places, from_wiki, from_wv, from_fsq, from_geo, from_events)
    votes = _source_votes(*sources)

    pool = max(wanted * 4, 120)
    places = _merge_sources(*sources, limit=pool)

    # Still thin: cast a wider Overpass net once - but only if Overpass
    # answered at all. Retrying a server that just timed out only wastes
    # another 10s, and the other sources have already had their say.
    if raw and len(places) < num_days * 3:
        wider_tags = list(dict.fromkeys(tags + DEFAULT_TAGS))
        wider = _build_query(lat, lon, wider_tags, radius_km * 2000, 400, NOTABLE_ONLY)
        raw_wide = await webclient.overpass(wider, label="overpass_attractions_wide",
                                            ttl=60 * 60 * 24 * 7)
        if raw_wide:
            wide_places = _clean_results(raw_wide, 400)
            combined = _merge_sources(wide_places, osm_places, from_wiki, from_wv, limit=pool)
            if len(combined) > len(places):
                places = combined
                votes = _source_votes(wide_places, from_wiki, from_wv, from_fsq, from_geo,
                                      from_events)

    # Carry Foursquare ratings onto the entries that won the merge.
    _attach_quality_signals(places, from_fsq)

    # Free popularity signal: how many Wikipedia language editions cover it.
    await enrich_with_wikidata(places)

    # Hand-picked landmarks are ALWAYS added for cities we know (not just
    # when live data is thin) - they are the places every traveller to that
    # city is actually asking about. A live entry for the same place wins
    # (it is marked curated_match instead), so nothing is doubled.
    if city:
        curated = _curated_attractions(city, existing=places)
        if curated:
            places = places + curated

    # Final sanity check. Wikipedia/Wikivoyage occasionally have a page
    # geotagged in the wrong place - that is how "Amoli, Almora", 200km
    # away, ended up on a Dehradun day plan. Anything absurdly far out is
    # not a day trip.
    places = [p for p in places
              if p.get("lat") is not None
              and haversine_km(lat, lon, p["lat"], p["lon"]) <= MAX_DAY_TRIP_KM]

    # Rank by how worth-visiting each place is. First pass: the anchor is the
    # city centre. The agent re-ranks again once it knows where the traveller
    # sleeps and how much the budget leaves (see agent/loop.py).
    ctx = {"anchor": (lat, lon), "city": city}
    for key in ("avoid_kinds", "avoid_words", "must_include", "extra_interests"):
        if preferences.get(key):
            ctx[key] = preferences[key]
    places = rank_places(places, interests, votes, min_keep=num_days * 2, ctx=ctx)[:wanted]

    return places if places else _offline_attractions(lat, lon)


# ---------------------------------------------------------------------------
# Restaurants: ranked, not just "whatever OSM returned first".
# ---------------------------------------------------------------------------

# International chains are fine food, but nobody travels to Jaipur for them.
CHAIN_RE = recommend.words_re((
    "domino's", "dominos", "mcdonald's", "mcdonalds", "kfc", "pizza hut", "subway",
    "burger king", "starbucks", "cafe coffee day", "ccd", "costa coffee", "barista",
    "wow momo", "faasos", "baskin robbins", "dunkin", "taco bell", "chaayos",
))
_VEG_RE = recommend.words_re(("veg", "vegetarian", "pure veg", "shudh", "bhojanalaya", "jain",
                              "udupi", "thali", "sweets", "mithai"))
_NONVEG_RE = recommend.words_re(("chicken", "mutton", "biryani", "kebab", "kebabs", "tandoori",
                                 "fish", "seafood", "meat", "non veg", "non-veg"))


def score_restaurant(r: dict, diet: str | None = None) -> float:
    """0-100: how good a lunch pick this is (distance is handled by the caller)."""
    score = 40.0
    if r.get("source") == "wikivoyage":
        score += 25          # a traveller wrote it up by hand
    if r.get("source_votes", 1) >= 2:
        score += 10
    if r.get("fsq_rating") is not None:
        score += (float(r["fsq_rating"]) - 6.0) * 6
    if r.get("wikidata") or r.get("wikipedia"):
        score += 10
    if r.get("cuisine"):
        score += 4           # someone bothered to tag what it serves
    if CHAIN_RE.search(r.get("name") or ""):
        score -= 25
    if r.get("source") == "offline-sample":
        score -= 30
    name = r.get("name") or ""
    if diet in ("vegetarian", "jain"):
        if r.get("vegetarian") or _VEG_RE.search(name):
            score += 15
        if _NONVEG_RE.search(name):
            score -= 40
    return max(0.0, min(100.0, score))


def rank_restaurants(restaurants: list[dict], diet: str | None = None) -> list[dict]:
    ranked = []
    for r in restaurants:
        item = dict(r)
        item["kind"] = "restaurant" if recommend.canonical_kind(r.get("kind")) not in ("cafe",) else "cafe"
        item["food_score"] = round(score_restaurant(r, diet))
        ranked.append(item)
    ranked.sort(key=lambda r: -r["food_score"])
    return ranked


async def find_restaurants(lat: float, lon: float, limit: int = 12,
                           radius_m: int = 8000, diet: str | None = None) -> list[dict]:
    """
    Real restaurants and cafes near a point, merged from OpenStreetMap
    (precise locations), Wikivoyage's hand-written "eat"/"drink" listings
    and Foursquare/Geoapify when keys are set - then RANKED (hand-written
    recommendation, rating, agreement, diet fit, chains last).

    The day planner calls this once for the city, and the agent calls it
    again near any day whose stops are far from all of those (a Mussoorie
    day trip should not send you back to Dehradun for lunch).
    """
    tags = [("amenity", "restaurant"), ("amenity", "cafe")]
    query = _build_query(lat, lon, tags, radius_m, limit * 6)

    raw, from_wv, from_fsq, from_geo = await asyncio.gather(
        webclient.overpass(query, label="overpass_restaurants", ttl=60 * 60 * 24 * 7),
        wikivoyage_nearby(lat, lon, limit * 2, wanted_kinds=("eat", "drink")),
        _foursquare_search(lat, lon, "restaurant cafe", radius_m, limit, is_restaurant=True),
        _geoapify_search(lat, lon, "catering.restaurant,catering.cafe", radius_m, limit,
                         is_restaurant=True),
    )

    osm_places = _clean_results(raw, limit * 6, is_restaurant=True) if raw else []

    # Same reasoning as find_attractions: a mirror can answer with zero
    # results just because a smaller town has few named restaurants inside
    # the radius on OSM. Widen once before giving up on that source.
    if not osm_places:
        wider = _build_query(lat, lon, tags, radius_m * 2, limit * 6)
        raw_wide = await webclient.overpass(wider, label="overpass_restaurants_wide",
                                            ttl=60 * 60 * 24 * 7)
        if raw_wide:
            osm_places = _clean_results(raw_wide, limit * 6, is_restaurant=True)

    # Wikivoyage "eat" listings with no coordinates are kept (they are often
    # the best tips) but flagged, and the lunch picker prefers located ones.
    votes = _source_votes(osm_places, from_wv, from_fsq, from_geo)
    merged = _merge_sources(from_wv, osm_places, from_fsq, from_geo, limit=limit * 6)
    _attach_quality_signals(merged, from_fsq)
    for r in merged:
        r["source_votes"] = votes.get(_normalize_name(r.get("name", "")), 1)
        if r.get("approx_location"):
            r["lat"], r["lon"] = lat, lon

    places = rank_restaurants(merged, diet)[: max(limit, 12) * 2]
    return places if places else _offline_restaurants(lat, lon)


async def wikipedia_summary(place_name: str) -> str:
    """One-line description of a place. Free, no key, real encyclopaedia text."""
    slug = place_name.replace(" ", "_")
    data = await webclient.get_json(
        f"{config.WIKIPEDIA_URL}/{slug}",
        label="wikipedia_summary",
        ttl=60 * 60 * 24 * 30,
    )
    if not data:
        return ""
    return (data.get("extract") or "")[:250]


# ---------------------------------------------------------------------------
# Name / title filters.
#
# v24: every list is matched on WHOLE WORDS. v23 matched substrings, which
# dropped real landmarks: "Umaid Bhawan Palace" ("bhawan"), "Courtallam
# Falls" ("court"), "Edward Memorial Park" ("ward"), "Boardwalk" ("board").
# And a sight word in the name ("palace", "falls", "museum", "ghat") now
# outranks the softer institutional words.
# ---------------------------------------------------------------------------

# Words that mean "this is a real thing you would actually go and see".
WORTH_VISITING = (
    "temple", "mandir", "fort", "qila", "palace", "mahal", "museum", "gallery",
    "park", "garden", "bagh", "lake", "jheel", "waterfall", "falls", "cave", "caves",
    "gufa", "beach", "church", "cathedral", "mosque", "masjid", "gurudwara",
    "monastery", "gompa", "stupa", "tomb", "mausoleum", "minar", "monument",
    "memorial", "ghat", "dam", "reservoir", "sanctuary", "national park",
    "wildlife", "zoo", "botanical", "viewpoint", "point", "hill", "peak",
    "valley", "river", "spring", "ashram", "math", "dargah", "haveli",
    "chhatri", "step-well", "stepwell", "baori", "bazaar", "market", "observatory",
    "planetarium", "aquarium", "island", "cliff", "trek", "pass",
)

# Never a destination - an event, a list, an administrative unit, a place of
# study or work. Checked FIRST ("List of forts in Rajasthan" contains "fort").
ABSOLUTE_DENY = (
    "election", "elections", "constituency", "assembly", "municipal corporation",
    "lok sabha", "vidhan sabha", "census", "district", "tehsil", "block",
    "ward", "list of", "battle of", "siege of", "treaty",
    "school", "academy", "college", "university", "institute",
    "laboratory", "council", "board", "commission",
    "railway station", "metro station", "airport", "bus stand",
    "hospital", "medical college",
    "cantonment", "regiment", "battalion", "brigade", "headquarters",
    "power plant", "power station", "refinery", "factory", "township",
    "highway", "expressway", "flyover", "bypass", "national highway",
    "panchayat", "nagar nigam", "secretariat",
    "department", "ministry", "authority", "corporation",
)

# Usually not a destination - but a sight word in the title or Wikipedia's
# own description overrides ("Umaid Bhawan Palace", "Cellular Jail ...
# national memorial").
SOFT_DENY = (
    "bhawan", "bhavan", "niketan", "office", "court", "jail", "prison", "research",
    "kendra", "depot", "junction", "industrial", "medical",
)

# Of the absolute words, these can NEVER be rescued by a sight word in the
# name ("List of forts", "Mayo College Museum" is still a school campus).
UNFORGIVABLE = (
    "election", "elections", "constituency", "list of", "battle of", "siege of", "treaty",
    "school", "academy", "college", "university", "institute", "hospital",
    "medical college", "railway station", "metro station", "airport", "bus stand",
    "cantonment", "regiment", "battalion", "brigade", "headquarters", "census",
)
_UNFORGIVABLE_RE = recommend.words_re(UNFORGIVABLE)
_WORTH_RE = recommend.words_re(WORTH_VISITING)
_ABSOLUTE_RE = recommend.words_re(ABSOLUTE_DENY)
_SOFT_RE = recommend.words_re(SOFT_DENY)

# Backwards-compatible name for code that imported the old tuple.
NOT_A_DESTINATION = ABSOLUTE_DENY + SOFT_DENY


# Real landmarks whose names happen to contain a word from the deny lists.
ALWAYS_KEEP = (
    "forest research institute", "rashtrapati", "victoria memorial",
    "salar jung", "indian museum", "science city", "birla mandir",
    "national gallery", "war memorial", "cellular jail", "parmarth niketan",
    "visvesvaraya industrial", "himalayan mountaineering institute",
    "dhobi ghat", "old high court", "writers' building", "writers building",
)

# Real places, but not something you can just walk into - defense
# establishments, cantonments, training academies. Wikipedia geotags these
# as landmarks and "military academy" used to sit in ALWAYS_KEEP, which put
# the Indian Military Academy straight into a Dehradun itinerary with no
# warning that it needs prior clearance. Block them outright instead of
# relying on the critic to catch it after the plan is built.
RESTRICTED_ACCESS = (
    "military academy", "cantonment", "air force station", "naval base",
    "army base", "defence academy", "defense academy", "ordnance factory",
    "border security force", "central jail", "district jail",
    "rimc", "indian military academy", "wadia institute",
    "research institute of himalayan geology", "geological survey",
    "training centre", "training center", "regimental centre",
    "regimental center", "recruit training", "officers training academy",
    "national defence academy", "national defense academy",
)
_RESTRICTED_RE = recommend.words_re(RESTRICTED_ACCESS)

# Titles that are an entire state, union territory or country. Wikipedia
# geotags these at a city and they are not somewhere you "visit" in a day.
TOO_BROAD = {
    "india", "uttarakhand", "uttar pradesh", "rajasthan", "goa", "kerala",
    "himachal pradesh", "punjab", "haryana", "gujarat", "maharashtra",
    "karnataka", "tamil nadu", "west bengal", "odisha", "bihar", "assam",
    "sikkim", "ladakh", "jammu and kashmir", "madhya pradesh", "telangana",
    "andhra pradesh", "chhattisgarh", "jharkhand", "manipur", "meghalaya",
    "nagaland", "tripura", "mizoram", "arunachal pradesh",
}

# A bare ref code, plot number or truncated OSM tag ("14 R(S)", "P-7", "GH
# 3") sometimes ends up sitting in the one field every source calls "name".
# A real place name always contains at least one proper word - three or more
# letters in a row ("Zoo", "Taj", "Fort" all pass).


def _looks_like_junk_name(name: str) -> bool:
    """True if `name` is a garbled code/abbreviation rather than a real name."""
    name = name.strip()
    if not name:
        return True
    return not re.search(r"[^\W\d_]{3,}", name)


def _always_keep(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ALWAYS_KEEP)


def _fails_visitor_check(name: str, is_restaurant: bool = False) -> bool:
    """
    One gatekeeper, used by every source (OSM, Wikipedia, Wikivoyage,
    Geoapify, Foursquare). Returns True if this name should be dropped.
    """
    lowered = name.strip().lower()
    if not lowered:
        return True

    if _looks_like_junk_name(name):
        return True

    # restricted-access places are never a valid stop, full stop
    if _RESTRICTED_RE.search(lowered):
        return True

    # known real landmarks beat the generic deny list below
    if _always_keep(lowered):
        return False

    # The institution lists are tuned for attractions. Skip them for
    # restaurants so a cafe genuinely named "College Corner" is not dropped.
    if is_restaurant:
        return False

    sight = bool(_WORTH_RE.search(lowered))
    if _UNFORGIVABLE_RE.search(lowered):
        return True
    if _ABSOLUTE_RE.search(lowered) and not sight:
        return True
    if _SOFT_RE.search(lowered) and not sight:
        return True
    return False


def _looks_worth_visiting(title: str, destination: str = "", description: str = "") -> int:
    """
    Score a Wikipedia page title, plus its short description when we have
    one (see wikipedia_nearby).

      2 = definitely a place worth seeing
      1 = might be, we do not know
      0 = definitely not, drop it

    Order matters:
      the destination itself / restricted / a whole state  -> 0
      a known landmark                                     -> 2
      an event, list, admin unit or institution            -> 0
      a sight word in the TITLE                            -> 2
      a soft "institutional" word (unless the description
        says it is a sight)                                -> 0
      a sight word in the description                      -> 2
      otherwise                                            -> 1
    """
    lowered = title.lower().strip()
    desc = description.lower().strip()
    combined = f"{lowered} {desc}".strip()

    # a Wikipedia geosearch centred on a city returns that city's own article
    # as a "nearby place" - that's how "Day 2" ended up with a single stop
    # literally named "Dehradun". A city describing itself is not a stop.
    if destination and lowered == destination.strip().lower():
        return 0

    if _RESTRICTED_RE.search(combined):
        return 0

    # a whole state is not a day trip. Title only.
    if lowered in TOO_BROAD:
        return 0

    if _always_keep(combined):
        return 2

    title_sight = bool(_WORTH_RE.search(lowered))
    desc_sight = bool(desc and _WORTH_RE.search(desc))
    if _UNFORGIVABLE_RE.search(lowered):
        return 0
    if _ABSOLUTE_RE.search(lowered) and not title_sight:
        return 0
    # "ward in Dehradun Municipal Corporation" - but NOT "waterfall in X district"
    if desc and _ABSOLUTE_RE.search(desc) and not title_sight and not desc_sight:
        return 0
    if title_sight:
        return 2
    if _SOFT_RE.search(combined) and not _WORTH_RE.search(desc):
        return 0
    if desc and _WORTH_RE.search(desc):
        return 2
    return 1


def _kind_from_title(title: str) -> str:
    """Guess the kind, so the day planner knows what counts as indoors."""
    lowered = title.lower()
    pairs = [
        (("museum", "gallery", "planetarium", "aquarium"), "museum"),
        (("fort", "qila"), "fort"),
        (("palace", "mahal", "haveli"), "palace"),
        (("temple", "mandir", "church", "cathedral", "mosque", "masjid",
          "gurudwara", "monastery", "gompa", "dargah", "ashram", "math"), "place_of_worship"),
        (("national park", "sanctuary", "wildlife", "tiger reserve"), "nature_reserve"),
        (("zoo", "zoological"), "zoo"),
        (("park", "garden", "bagh", "botanical"), "park"),
        (("waterfall", "falls"), "waterfall"),
        (("lake", "jheel", "tal", "river", "dam", "reservoir", "sagar"), "lake"),
        (("beach",), "beach"),
        (("cave", "caves", "gufa"), "ruins"),
        (("market", "bazaar", "bazar"), "marketplace"),
        (("viewpoint", "point", "hill", "peak", "valley", "cliff"), "viewpoint"),
        (("tomb", "mausoleum", "monument", "memorial", "minar", "stupa", "gate"), "monument"),
    ]
    for words, kind in pairs:
        if recommend.words_re(words).search(lowered):
            return kind
    return "attraction"


async def wikipedia_nearby(lat: float, lon: float, limit: int = 30, destination: str = "") -> list[dict]:
    """
    Real places near a point, from Wikipedia.

    Three quality signals, because raw geosearch results are rough:

      1. we ask for pageimages as well as coordinates, and drop pages with no
         picture unless the title/description clearly says "sight".
      2. we score the title AND Wikipedia's own one-line description
         (prop=description) against keep/drop word lists.
      3. we ask for the page's Wikidata id (pageprops), so the ranking can
         use how many language editions cover it (enrich_with_wikidata).

    Without this the Dehradun itinerary genuinely contained
    "2008 Dehradun Municipal Corporation election" as a 90-minute stop.
    """
    data = await webclient.get_json(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            # generator=geosearch lets us pull page details in the SAME call
            # instead of geosearching and then looking each page up again.
            "generator": "geosearch",
            "ggscoord": f"{lat}|{lon}",
            "ggsradius": 10000,          # 10km is the API maximum
            "ggslimit": 100,
            "prop": "pageimages|coordinates|description|pageprops",
            "ppprop": "wikibase_item",
            "piprop": "thumbnail",
            "pithumbsize": 200,
            "format": "json",
        },
        label="wikipedia_geosearch",
        ttl=60 * 60 * 24 * 7,
    )

    if not data:
        return []

    pages = (data.get("query", {}) or {}).get("pages", {})
    scored = []

    for page in pages.values():
        title = page.get("title", "")
        description = page.get("description", "") or ""
        score = _looks_worth_visiting(title, destination, description)

        if score == 0:
            continue

        # A page with no picture is usually an administrative or historical
        # entry rather than somewhere you would go.
        has_image = bool(page.get("thumbnail"))
        if not has_image and score < 2:
            continue

        coords = (page.get("coordinates") or [{}])[0]
        if "lat" not in coords:
            continue

        kind = _kind_from_title(title)
        if kind == "attraction" and description:
            kind = _kind_from_title(description)
        scored.append((
            score + (1 if has_image else 0),
            {
                "name": title,
                "kind": kind.replace("_", " "),
                "lat": coords["lat"],
                "lon": coords["lon"],
                "visit_hours": VISIT_HOURS.get(kind, 1.0),
                "fee": "unknown",
                "wikipedia": title.replace(" ", "_"),
                "wikidata": (page.get("pageprops") or {}).get("wikibase_item", ""),
                "has_image": has_image,
                "blurb": description,
                "source": "wikipedia",
            },
        ))

    # best first, so if we trim to `limit` we keep the good ones
    scored.sort(key=lambda row: row[0], reverse=True)
    return [place for _, place in scored[:limit]]


# ---------------------------------------------------------------------------
# THIRD real source: Wikivoyage.
#
# Wikipedia tells you a fort existed since 1727. Wikivoyage tells you when
# to go, roughly what it costs, and how to get there - because its
# {{see}}/{{do}}/{{eat}}/{{drink}} templates are literally a traveller's
# hand-written list of things worth doing, one template per recommendation.
# Same free MediaWiki API as Wikipedia, no key, different opinion.
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]")
_REF_RE = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_wikitext(text: str) -> str:
    """Turn '[[Amer Fort|the fort]]<ref>x</ref>' into 'the fort'."""
    text = _REF_RE.sub("", text)
    text = _WIKILINK_RE.sub(r"\1", text)
    text = _TAG_RE.sub("", text)
    text = text.replace("'''", "").replace("''", "")
    return " ".join(text.split())


def _find_templates(wikitext: str, names: set[str]) -> list[str]:
    """
    Pull out the raw inner text of every {{name|...}} template whose name is
    in `names`, tracking brace depth so a template that contains another
    template (or a [[wikilink]]) does not truncate the match early.
    """
    results = []
    i, n = 0, len(wikitext)
    while i < n:
        if wikitext[i:i + 2] != "{{":
            i += 1
            continue

        j = i + 2
        while j < n and wikitext[j] not in "|}\n":
            j += 1
        name = wikitext[i + 2:j].strip().lower()

        depth = 1
        pos = i + 2
        while pos < n and depth > 0:
            if wikitext[pos:pos + 2] == "{{":
                depth += 1
                pos += 2
            elif wikitext[pos:pos + 2] == "}}":
                depth -= 1
                pos += 2
            else:
                pos += 1

        if name in names:
            results.append(wikitext[i + 2:max(pos - 2, i + 2)])
        i = pos

    return results


def _split_fields(inner: str) -> list[str]:
    """
    Split a template's inner text on '|', except inside a nested [[...]] or
    {{...}} - otherwise "address=12, [[MG Road|MG Road]]" would split into
    two fields at the pipe inside the wikilink.
    """
    fields, buf = [], []
    depth = 0
    i, n = 0, len(inner)
    while i < n:
        two = inner[i:i + 2]
        if two in ("[[", "{{"):
            depth += 1
            buf.append(two)
            i += 2
            continue
        if two in ("]]", "}}"):
            depth = max(depth - 1, 0)
            buf.append(two)
            i += 2
            continue
        if inner[i] == "|" and depth == 0:
            fields.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(inner[i])
        i += 1
    fields.append("".join(buf))
    return fields


async def _wikivoyage_wikitext(title: str) -> str:
    data = await webclient.get_json(
        config.WIKIVOYAGE_URL,
        params={"action": "parse", "page": title, "prop": "wikitext",
                "format": "json", "redirects": 1},
        label="wikivoyage_wikitext",
        ttl=60 * 60 * 24 * 7,
    )
    if not data or "parse" not in data:
        return ""
    return ((data["parse"].get("wikitext") or {}).get("*", ""))


async def wikivoyage_nearby(lat: float, lon: float, limit: int = 30,
                            wanted_kinds: tuple[str, ...] = ("see", "do"),
                            destination: str = "") -> list[dict]:
    """
    Curated listings from the nearest Wikivoyage article(s).

    `wanted_kinds` picks which templates to pull: ("see", "do") for
    attractions, ("eat", "drink") for restaurants - same function, two jobs,
    because they are literally the same kind of listing template.
    """
    geosearch = await webclient.get_json(
        config.WIKIVOYAGE_URL,
        params={
            "action": "query",
            "generator": "geosearch",
            "ggscoord": f"{lat}|{lon}",
            "ggsradius": 10000,
            "ggslimit": 5,
            "format": "json",
        },
        label="wikivoyage_geosearch",
        ttl=60 * 60 * 24 * 7,
    )
    if not geosearch:
        return []

    pages = (geosearch.get("query", {}) or {}).get("pages", {})
    titles = [p["title"] for p in pages.values() if p.get("title")]
    if not titles:
        return []

    # A destination and the region page above it (e.g. "Dehradun" and
    # "Garhwal") both often exist - fetch the nearest couple, in parallel.
    wikitexts = await asyncio.gather(*(_wikivoyage_wikitext(t) for t in titles[:3]))

    out: list[dict] = []
    seen: set[str] = set()

    for wikitext in wikitexts:
        if not wikitext:
            continue
        for kind_name in wanted_kinds:
            for inner in _find_templates(wikitext, {kind_name}):
                fields = _split_fields(inner)
                kv = {}
                for field in fields[1:]:                # fields[0] is the template name
                    match = re.match(r"\s*([\w-]+)\s*=\s*(.*)", field, re.DOTALL)
                    if match:
                        kv[match.group(1).strip().lower()] = match.group(2).strip()

                name = _clean_wikitext(kv.get("name", ""))
                if not name or name.lower() in seen or name.lower() == destination.lower():
                    continue
                if _fails_visitor_check(name, is_restaurant=(kind_name in ("eat", "drink"))):
                    continue
                seen.add(name.lower())

                # v24: a listing with no coordinates of its own is flagged
                # rather than silently pinned to the city centre (which gave
                # "Only 0.0 km from the centre" reasons and wrong day groups).
                approx = False
                try:
                    p_lat = float(kv.get("lat"))
                    p_lon = float(kv.get("long") or kv.get("lon"))
                except (TypeError, ValueError):
                    p_lat, p_lon, approx = lat, lon, True

                kind = "restaurant" if kind_name in ("eat", "drink") else _kind_from_title(name)
                blurb = _clean_wikitext(kv.get("content") or kv.get("alt") or "")[:220]
                price = _clean_wikitext(kv.get("price", ""))
                if price:
                    blurb = f"{blurb} ({price})" if blurb else price

                out.append({
                    "name": name,
                    "kind": kind,
                    "lat": p_lat,
                    "lon": p_lon,
                    "visit_hours": VISIT_HOURS.get(kind, 1.0),
                    "fee": "unknown",
                    "wikipedia": "",
                    "blurb": blurb,
                    "source": "wikivoyage",
                    "approx_location": approx,
                })

                if len(out) >= limit:
                    return out

    return out


# ---------------------------------------------------------------------------
# Offline fallbacks. Used only when the internet is unreachable, and always
# labelled "offline-sample" so nobody mistakes them for real data.
# ---------------------------------------------------------------------------

def _offline_attractions(lat: float, lon: float) -> list[dict]:
    # Names are deliberately prefixed. If OpenStreetMap is unreachable we
    # would rather the plan look obviously unfinished than quietly invent
    # "City Palace Museum, Dehradun" and have somebody believe it.
    template = [
        ("[sample] local fort", "fort", 0.03, 0.02),
        ("[sample] city museum", "museum", -0.02, 0.03),
        ("[sample] riverside walk", "attraction", 0.01, -0.04),
        ("[sample] viewpoint", "viewpoint", 0.06, 0.05),
        ("[sample] main bazaar", "marketplace", -0.01, 0.01),
        ("[sample] public garden", "park", 0.04, -0.03),
        ("[sample] old temple", "place_of_worship", -0.05, -0.02),
        ("[sample] heritage site", "ruins", 0.02, 0.06),
    ]
    return [{
        "name": name,
        "kind": kind.replace("_", " "),
        "lat": round(lat + d_lat, 5),
        "lon": round(lon + d_lon, 5),
        "visit_hours": VISIT_HOURS.get(kind, 1.5),
        "fee": "unknown",
        "wikipedia": "",
        "source": "offline-sample",
    } for name, kind, d_lat, d_lon in template]


def _offline_restaurants(lat: float, lon: float) -> list[dict]:
    template = [
        ("[sample] thali place", 0.01, 0.01), ("[sample] chai cafe", -0.01, 0.02),
        ("[sample] street food lane", 0.02, -0.01), ("[sample] dhaba", -0.02, -0.02),
        ("[sample] tiffin centre", 0.03, 0.00), ("[sample] sweet shop", 0.00, 0.03),
    ]
    return [{
        "name": name,
        "kind": "restaurant",
        "lat": round(lat + d_lat, 5),
        "lon": round(lon + d_lon, 5),
        "visit_hours": 1.0,
        "fee": "unknown",
        "wikipedia": "",
        "source": "offline-sample",
    } for name, d_lat, d_lon in template]
