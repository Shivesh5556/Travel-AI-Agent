"""
The recommendation engine - what decides which places are WORTH the traveller's
time and money.

Ghumi Ghumi's promise is not "an itinerary". It is "the best trip that fits
YOUR budget". So every candidate (an attraction or a place to stay) gets one
structured 0-100 score built from named parts, and every part is kept so the
interface can say WHY something was picked.

    Places -> structured scoring -> top candidates -> route -> LLM writes it up

The LLM never ranks anything. It only describes what this file already chose.

This module is pure Python: no network, no LLM, no randomness. Same input, same
answer, and every number can be unit-tested (tests/test_recommend.py,
tests/test_quality.py).

ATTRACTION SCORE (100 points)
    interest match        20   does it match what THIS traveller said they like
    popularity            22   wikidata language editions, ratings, notability
    tourist significance  18   is it a known landmark, not just "a thing on a map"
    source agreement      10   how many INDEPENDENT sources name it
    type quality          10   a fort or waterfall beats a corner shop
    route fit             10   how close to where they sleep / the city centre
    budget fit             5   does its typical entry cost suit their spending level
    season fit             5   indoor on a rainy trip, waterfalls in the monsoon

v24 - why the maths changed
---------------------------
v23 added the interest points (25 of them) to everything else. Any place of the
right TYPE got all 25, so for a "Culture + Food" traveller a neighbourhood
temple or a Domino's scored ~52 while Amer Fort (tagged tourism=attraction in
OpenStreetMap) scored 43. Two changes fix that:

  1. Interest points are earned in proportion to quality. A perfect match on a
     place nobody vouches for earns about half the interest points; a perfect
     match on a famous landmark earns nearly all of them.
  2. The quality + interest "core" is multiplied by how relevant the place is
     (0.6 for "not what you asked for" up to 1.0 for a match), so a famous
     waterfall does not beat a famous bazaar for someone who said Shopping.

Route, budget and season are added on top unscaled, so a place can never win on
"500 m closer" alone (route is still only 10).
"""

import math
import re

from tools.geo import haversine_km

ATTRACTION_WEIGHTS = {
    "interest": 20, "popularity": 22, "significance": 18, "agreement": 10,
    "type_quality": 10, "route": 10, "budget_fit": 5, "season": 5,
}
QUALITY_KEYS = ("popularity", "significance", "agreement", "type_quality")
CORE_KEYS = QUALITY_KEYS + ("interest",)

# What matters most depends on how the traveller wants to spend. Each row sums
# to 100. A budget traveller's score leans on price; a comfort traveller's on
# quality; location matters to everyone.
STAY_WEIGHTS_BY_STYLE = {
    "budget":   {"budget": 42, "quality": 18, "location": 22, "evidence": 8, "style": 10},
    "balanced": {"budget": 30, "quality": 30, "location": 25, "evidence": 8, "style": 7},
    "comfort":  {"budget": 18, "quality": 42, "location": 25, "evidence": 8, "style": 7},
}
STAY_WEIGHTS = STAY_WEIGHTS_BY_STYLE["balanced"]   # kept for the docstring/tests

# The nightly price each style is happy to land on, capped in absolute rupees so
# a large budget does not turn a "budget" trip into a resort stay.
STYLE_PRICE_CAP = {"budget": 1800, "balanced": 4500, "comfort": None}


def words_re(words) -> re.Pattern:
    """Whole-word, case-insensitive matcher. 'bar' must not match 'Bara Imambara'."""
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# What a place IS
# ---------------------------------------------------------------------------

# How interesting each kind of place is on its own, 0-10.
KIND_BASE = {
    "fort": 10, "castle": 10, "palace": 10, "waterfall": 9, "monument": 8,
    "museum": 8, "ruins": 8, "beach": 8, "lake": 7, "peak": 7, "viewpoint": 7,
    "nature reserve": 7, "zoo": 6, "park": 6, "garden": 6, "attraction": 6,
    "event": 8, "sights": 6, "place of worship": 4, "marketplace": 2,
    "mall": 3, "bar": 4, "pub": 4, "nightclub": 4, "cafe": 3, "restaurant": 3,
    "spa": 5,
}

# Which kinds satisfy which interest.
INTEREST_KINDS = {
    "Nature": {"park", "garden", "waterfall", "peak", "viewpoint", "nature reserve", "beach", "lake"},
    "Culture & Heritage": {"fort", "castle", "palace", "monument", "museum", "ruins", "place of worship"},
    "Spiritual": {"place of worship"},
    "Adventure": {"peak", "viewpoint", "waterfall", "nature reserve", "ruins"},
    "Shopping": {"marketplace", "mall"},
    "Relaxation": {"park", "garden", "spa", "beach", "waterfall", "lake"},
    "Nightlife": {"bar", "pub", "nightclub", "event"},
    "Photography": {"viewpoint", "monument", "waterfall", "fort", "palace", "ruins", "lake"},
    "Food": {"restaurant", "cafe"},
}

# Words in a NAME that hint at an interest even when the kind is generic.
# Matched as WHOLE words (v23 used substrings: "Bara Imambara" was Nightlife
# because it contains "bar", and "City Parking" was Nature because of "park").
INTEREST_WORDS = {
    "Nature": ("park", "garden", "falls", "waterfall", "lake", "forest", "valley", "sanctuary",
               "reserve", "hill", "hills", "tal", "taal", "jheel"),
    "Culture & Heritage": ("fort", "qila", "palace", "mahal", "temple", "mandir", "museum", "haveli",
                           "gate", "darwaza", "tomb", "maqbara", "ghat", "monastery", "cathedral",
                           "baori", "stepwell", "minar", "memorial"),
    "Spiritual": ("temple", "mandir", "ghat", "aarti", "ashram", "monastery", "gurudwara",
                  "dargah", "masjid", "mosque", "church", "cathedral", "math", "dham", "gompa"),
    "Adventure": ("trek", "trail", "rafting", "camp", "camping", "cave", "caves", "peak", "falls",
                  "ropeway", "paragliding", "zipline", "bungee"),
    "Shopping": ("bazaar", "bazar", "market", "mall", "haat", "emporium"),
    "Relaxation": ("spa", "garden", "lake", "ghat", "beach"),
    "Nightlife": ("bar", "pub", "club", "lounge", "brewery"),
    "Photography": ("point", "view", "viewpoint", "sunrise", "sunset", "gate", "fort"),
    "Food": ("cafe", "restaurant", "dhaba", "kitchen", "food", "chaat", "sweets", "bhojanalaya"),
}
_INTEREST_WORD_RE = {k: words_re(v) for k, v in INTEREST_WORDS.items()}

# Shopping streets people genuinely come for.
FAMOUS_MARKET_HINTS = (
    "paltan", "chandni chowk", "johari", "bapu bazaar", "tripolia", "sarojini",
    "janpath", "connaught", "lajpat", "dilli haat", "mall road", "laad bazaar",
    "sadar bazaar", "colaba causeway", "crawford", "chor bazaar", "new market",
    "commercial street", "hazratganj", "aminabad", "lal bazaar", "main bazaar",
    "khan market", "hill palace", "old manali", "tibetan market", "mapusa",
    "anjuna flea", "saturday night market", "ima keithel", "police bazaar",
    "rajpur road", "charminar", "sardar market", "hauz khas", "mg road",
)

# Kinds that are only a destination when something else vouches for them.
GENERIC_KINDS = {"place of worship", "marketplace", "mall", "cafe", "restaurant",
                 "bar", "pub", "nightclub", "spa"}

# Common enough that being one proves nothing; they need evidence (see the
# evidence gate in score_attraction).
EVIDENCE_REQUIRED_KINDS = GENERIC_KINDS | {"park", "garden"}
NO_EVIDENCE_CAP = 20.0   # below places.DOCUMENTED_FLOOR: anything documented ranks first

# Places to EAT are meal stops, never sightseeing stops - even for a Food
# traveller. Food interest is honoured by ranking the lunch spots instead.
MEAL_KINDS = {"restaurant", "cafe", "fast food", "food court"}


# ---------------------------------------------------------------------------
# Kind normalisation. Every source names things differently ("Hindu Temple",
# "place_of_worship", "Scenic Lookout", "historic.castle"). Scoring, interest
# matching and the rainy-day logic all need ONE vocabulary.
# ---------------------------------------------------------------------------

_KIND_RULES = [
    (("restaurant", "food", "dhaba", "diner", "eatery", "bistro", "pizza", "fast food",
      "bakery", "sweet"), "restaurant"),
    (("cafe", "café", "coffee", "tea room", "tea house"), "cafe"),
    (("nightclub", "night club", "disco"), "nightclub"),
    (("pub", "brewery"), "pub"),
    (("bar", "lounge"), "bar"),
    (("museum", "gallery", "planetarium", "aquarium", "science centre", "science center"), "museum"),
    (("temple", "mosque", "masjid", "church", "cathedral", "gurudwara", "gurdwara", "shrine",
      "monastery", "gompa", "dargah", "place of worship", "religion", "synagogue", "stupa"),
     "place of worship"),
    (("fort", "fortress", "castle", "citadel", "qila"), "fort"),
    (("palace", "mahal", "haveli"), "palace"),
    (("ruins", "archaeological", "archaeological site", "cave", "caves"), "ruins"),
    (("waterfall", "falls"), "waterfall"),
    (("national park", "wildlife", "sanctuary", "nature reserve", "nature_reserve",
      "tiger reserve", "protected area"), "nature reserve"),
    (("zoo",), "zoo"),
    (("lake", "river", "reservoir", "dam", "water", "pond"), "lake"),
    (("beach",), "beach"),
    (("peak", "mountain", "summit", "volcano", "hill"), "peak"),
    (("viewpoint", "scenic lookout", "lookout", "view point"), "viewpoint"),
    (("botanical", "garden"), "garden"),
    (("park",), "park"),
    (("mall", "shopping centre", "shopping center", "shopping mall", "department store"), "mall"),
    (("market", "bazaar", "marketplace", "flea"), "marketplace"),
    (("monument", "memorial", "tomb", "mausoleum", "minar", "historic", "heritage",
      "landmark", "gate", "tower", "statue"), "monument"),
    (("spa", "wellness"), "spa"),
    (("event", "festival", "concert"), "event"),
]
_KIND_RULE_RE = [(words_re(ws), kind) for ws, kind in _KIND_RULES]
KNOWN_KINDS = set(KIND_BASE)


def canonical_kind(raw: str | None) -> str:
    """Map any source's category name onto our small fixed vocabulary."""
    text = str(raw or "attraction").replace("_", " ").replace(".", " ").strip().lower()
    if text in KNOWN_KINDS:
        return text
    for pattern, kind in _KIND_RULE_RE:
        if pattern.search(text):
            return kind
    return "attraction"


def _kind(place: dict) -> str:
    return canonical_kind(place.get("kind"))


# ---------------------------------------------------------------------------
# What a place is NOT  (negative filters)
# ---------------------------------------------------------------------------

# Never a destination, whatever anyone says about it (only a hand-picked entry
# can override). Whole-word matches, so "Mandir" (temple) is never mistaken
# for "mandi" (wholesale market).
HARD_NEGATIVE_RE = re.compile(
    r"\b(sabzi mandi|subzi mandi|sabji mandi|anaj mandi|grain mandi|krishi mandi|"
    r"fruit mandi|mandi samiti|wholesale|kirana|bus stand|bus stop|bus station|"
    r"bus terminal|bus depot|isbt|taxi stand|auto stand|railway station|railway colony|"
    r"railway junction|metro station|petrol|petrol pump|cng|fuel station|filling station|"
    r"garage|workshop|godown|warehouse|factory|industrial|industrial area|sewage|"
    r"substation|police station|police chowki|thana|tyre|tyres|spare parts|scrap|"
    r"hospital|clinic|nursing home|pharmacy|medical store|chemist|dispensary|diagnostic|"
    r"diagnostics|pathology|coaching|tuition|secretariat|collectorate|tehsil|nagar nigam|"
    r"electricity|power house|water works|water tank|atm|bank|insurance|office|"
    r"head office|branch office)\b", re.IGNORECASE)

# Dropped unless something vouches for the place (hand-picked, Wikipedia,
# Wikidata, heritage tag, two sources) OR its own name says it is a sight
# ("Dhobi Ghat", "Bhuri Singh Museum, Mandi", "South Park Street Cemetery").
DROP_UNLESS_NOTABLE_RE = re.compile(
    r"\b(mandi|vegetables?|fruit market|meat market|fish market|cattle|pashu|depot|mill|"
    r"crematorium|shamshan|cemetery|kabristan|graveyard|dhobi|dairy|junction|"
    r"school|college|university|vidyalaya|vidyapeeth|academy|institute of|"
    r"court|colony|society|apartments?|residency|residential|sector \d+|"
    r"phase \d+|enclave|extension|housing|showroom|stadium|crossing|chauraha|chowk|"
    r"flyover|hostel block|quarters|parking)\b", re.IGNORECASE)

# Words that, in the place's own NAME, show it is something people go to see.
SIGHT_WORD_RE = words_re((
    "museum", "palace", "fort", "qila", "mahal", "temple", "mandir", "ghat", "falls",
    "waterfall", "lake", "garden", "bagh", "memorial", "monument", "tomb", "church",
    "cathedral", "masjid", "mosque", "gurudwara", "monastery", "stupa", "cave", "caves",
    "haveli", "baori", "stepwell", "minar", "darwaza", "gate", "park", "zoo", "gallery",
    "observatory", "sanctuary", "viewpoint", "point", "beach", "ruins", "heritage",
))

# Kept for backwards compatibility with code that imported it.
SOFT_NEGATIVE_RE = DROP_UNLESS_NOTABLE_RE


def _strip_city(name: str, city: str | None) -> str:
    """'Mandi' the town must not trip the 'mandi' (market) filter for a trip TO Mandi."""
    if not city:
        return name or ""
    return re.sub(r"\b" + re.escape(city.strip()) + r"\b", " ", name or "", flags=re.IGNORECASE)


def is_hard_negative(name: str, city: str | None = None) -> bool:
    # Checked on the full name: "Sabzi Mandi" is a wholesale market even on a
    # trip to Mandi. (The city name is only ignored by the softer filter.)
    return bool(HARD_NEGATIVE_RE.search(name or ""))


def is_soft_negative(name: str, city: str | None = None) -> bool:
    text = _strip_city(name, city)
    return bool(DROP_UNLESS_NOTABLE_RE.search(text))


def names_a_sight(name: str) -> bool:
    return bool(SIGHT_WORD_RE.search(name or ""))


# ---------------------------------------------------------------------------
# What it costs to visit - an honest, coarse tier (never a made-up rupee value)
# ---------------------------------------------------------------------------
# 0 free, 1 low, 2 moderate, 3 premium
COST_LEVEL = {
    "park": 0, "garden": 1, "viewpoint": 0, "peak": 0, "beach": 0, "waterfall": 1,
    "lake": 0, "zoo": 1, "place of worship": 0, "marketplace": 0, "sights": 0,
    "attraction": 1, "monument": 1, "ruins": 1, "nature reserve": 2, "museum": 1,
    "fort": 2, "castle": 2, "palace": 2, "mall": 0, "cafe": 1, "restaurant": 2, "bar": 3,
    "pub": 3, "nightclub": 3, "spa": 3, "event": 2,
}
COST_NOTE = {
    0: "Usually free to visit",
    1: "Usually a small entry fee",
    2: "Paid entry, moderate",
    3: "Premium - expect to spend",
}
# How much each spending level can comfortably absorb.
STYLE_COST_CAP = {"shoestring": 1, "normal": 2, "comfort": 3}

OUTDOOR_KINDS = {"park", "garden", "waterfall", "peak", "viewpoint", "nature reserve",
                 "beach", "ruins", "fort", "attraction", "sights", "lake", "zoo"}
INDOOR_KINDS = {"museum", "mall", "marketplace", "place of worship", "castle",
                "gallery", "restaurant", "cafe", "palace", "spa"}


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _is_famous_market(name: str) -> bool:
    lowered = (name or "").lower()
    return any(h in lowered for h in FAMOUS_MARKET_HINTS)


def _is_curated(place: dict) -> bool:
    return place.get("source") == "curated" or bool(place.get("curated_match"))


def _independent_sources(place: dict, votes: dict[str, int] | None) -> int:
    """How many INDEPENDENT live sources name the place. Our own hand-picked
    list is not an independent source, so it is never counted here."""
    n = (votes or {}).get(_normalize(place.get("name", "")))
    if n is None:
        n = place.get("source_votes", 1)
    return max(1, int(n or 1))


def _sitelinks_fraction(place: dict) -> float | None:
    """Wikidata sitelinks = how many language editions of Wikipedia cover the
    place. Free, global and a very good popularity proxy: the Taj Mahal has
    150+, a neighbourhood temple has 0-2."""
    n = place.get("sitelinks")
    if n is None:
        return None
    return max(0.0, min(1.0, math.log1p(int(n)) / math.log1p(80)))


def is_notable(place: dict, votes: dict[str, int] | None = None) -> bool:
    """Does anything independent vouch for this place?"""
    if _is_curated(place):
        return True
    if place.get("wikipedia") or place.get("source") in ("wikipedia", "wikivoyage"):
        return True
    if place.get("wikidata") or place.get("heritage"):
        return True
    if (place.get("sitelinks") or 0) >= 3:
        return True
    if _independent_sources(place, votes) >= 2:
        return True
    if place.get("fsq_rating") is not None and (place.get("fsq_ratings_count") or 0) >= 20:
        return True
    if _kind(place) == "marketplace" and _is_famous_market(place.get("name", "")):
        return True     # a shopping street people travel for is its own evidence
    return False


# ---------------------------------------------------------------------------
# Attraction score
# ---------------------------------------------------------------------------

def _interest_fraction(place: dict, interests: list[str]) -> tuple[float, list[str]]:
    """0-1: how well the place matches what the traveller asked for."""
    if not interests:
        return 0.5, []          # no preference stated: neutral, not a penalty
    kind = _kind(place)
    name = place.get("name") or ""
    best, matched = 0.0, []
    for interest in interests:
        if kind in INTEREST_KINDS.get(interest, set()):
            frac = 1.0
        elif interest in _INTEREST_WORD_RE and _INTEREST_WORD_RE[interest].search(name):
            frac = 0.65
        else:
            frac = 0.0
        if frac >= 0.65:
            matched.append(interest)
        best = max(best, frac)
    # something unrelated to ANY stated interest still has a small base value
    return max(best, 0.15), matched


def _popularity_fraction(place: dict, votes: dict[str, int] | None) -> tuple[float, str]:
    """0-1 from real popularity data when we have it, else notability evidence."""
    proxy = 0.2
    if _is_curated(place):
        # position in the hand-picked list: 0 = the city's headline sight
        proxy = 0.95 - 0.045 * min(place.get("curated_rank", 1), 10)
    elif place.get("wikipedia"):
        proxy = 0.65
    elif place.get("source") in ("wikipedia", "wikivoyage"):
        proxy = 0.55
    elif place.get("source") == "ticketmaster":
        proxy = 0.6
    elif _kind(place) == "marketplace" and _is_famous_market(place.get("name", "")):
        proxy = 0.6
    elif place.get("heritage"):
        proxy = 0.5
    elif place.get("wikidata"):
        proxy = 0.45
    elif place.get("osm_attraction"):
        proxy = 0.3
    if place.get("has_image"):
        proxy = min(1.0, proxy + 0.08)
    if _independent_sources(place, votes) >= 2:
        proxy = min(1.0, proxy + 0.1)

    real_parts, note = [], ""
    links = _sitelinks_fraction(place)
    if links is not None:
        real_parts.append(links)
        if place.get("sitelinks", 0) >= 10:
            note = f"Widely known - covered by Wikipedia in {place['sitelinks']} languages"
    rating = place.get("fsq_rating")          # Foursquare: 0-10
    popularity = place.get("fsq_popularity")  # Foursquare: 0-1
    if rating is not None:
        real_parts.append(max(0.0, min(1.0, float(rating) / 10.0)))
        note = f"Rated {float(rating):.1f}/10 by visitors"
    if popularity is not None:
        real_parts.append(max(0.0, min(1.0, float(popularity))))
        note = note or "Popular with visitors"
    if real_parts:
        real = sum(real_parts) / len(real_parts)
        return 0.7 * real + 0.3 * proxy, note
    return proxy, ""


def _significance_fraction(place: dict, votes: dict[str, int] | None) -> float:
    kind = _kind(place)
    name = place.get("name") or ""
    if _is_curated(place):
        return 1.0 - 0.05 * min(place.get("curated_rank", 1), 10)
    sig = 0.1 if kind in GENERIC_KINDS else 0.2
    if place.get("osm_attraction"):
        sig = max(sig, 0.35)
    if place.get("wikidata"):
        sig = max(sig, 0.5)
    if _independent_sources(place, votes) >= 2:
        sig = max(sig, 0.5)
    if place.get("source") == "wikivoyage":
        sig = max(sig, 0.6)
    if place.get("source") == "ticketmaster":
        sig = max(sig, 0.7)
    if place.get("wikipedia") or place.get("source") == "wikipedia":
        sig = max(sig, 0.75 if place.get("wikipedia") else 0.6)
    if place.get("heritage"):
        sig = max(sig, 0.8)
    if kind == "marketplace" and _is_famous_market(name):
        sig = max(sig, 0.8)
    links = _sitelinks_fraction(place)
    if links is not None:
        sig = max(sig, links)
    return sig


def _agreement_fraction(place: dict, votes: dict[str, int] | None) -> tuple[float, int]:
    n = _independent_sources(place, votes)
    frac = {1: 0.15, 2: 0.65}.get(n, 1.0)
    if n == 1 and _is_curated(place):
        frac = 0.4       # our own list adds some confidence, but it is not a second source
    return frac, n


def _route_fraction(place: dict, anchor: tuple[float, float] | None) -> tuple[float, float | None]:
    if not anchor or place.get("lat") is None or place.get("lon") is None:
        return 0.6, None
    km = haversine_km(anchor[0], anchor[1], place["lat"], place["lon"])
    for limit, frac in ((3, 1.0), (8, 0.8), (15, 0.55), (25, 0.3)):
        if km <= limit:
            return frac, km
    return 0.1, km


def _budget_fraction(place: dict, spend_style: str | None) -> float:
    if spend_style not in STYLE_COST_CAP:
        return 0.7
    level = COST_LEVEL.get(_kind(place), 1)
    cap = STYLE_COST_CAP[spend_style]
    if level <= cap:
        # free things are a small bonus for a tight spender
        return 1.0 if (spend_style != "shoestring" or level <= 1) else 0.8
    return max(0.1, 1.0 - 0.45 * (level - cap))


def _season_fraction(place: dict, rain_share: float | None) -> float:
    if rain_share is None:
        return 0.7
    kind = _kind(place)
    if rain_share >= 0.4:
        if kind == "waterfall":
            return 1.0                  # the monsoon is when waterfalls are worth seeing
        if kind in INDOOR_KINDS:
            return 1.0
        if kind in OUTDOOR_KINDS:
            return 0.3
        return 0.6
    if rain_share <= 0.15 and kind in OUTDOOR_KINDS:
        return 0.9
    return 0.7


def _blocked_by_preferences(place: dict, ctx: dict) -> bool:
    """Things the traveller said they do NOT want (parsed from their notes)."""
    kind = _kind(place)
    if kind in set(ctx.get("avoid_kinds") or ()):
        return True
    avoid_words = ctx.get("avoid_words") or ()
    if avoid_words and words_re(avoid_words).search(place.get("name") or ""):
        return True
    return False


def score_attraction(place: dict, interests: list[str], votes: dict[str, int] | None = None,
                     ctx: dict | None = None) -> dict | None:
    """
    Score one attraction. Returns None when it must not be recommended at all.

    ctx (all optional):
      anchor       (lat, lon) of where the traveller sleeps, or the city centre
      spend_style  "shoestring" | "normal" | "comfort"  - from the budget solver
      rain_share   0-1, share of trip days with a high rain chance
      city         the destination name (so "Mandi" the town is not a market)
      avoid_kinds  kinds the traveller ruled out in their notes
      avoid_words  name words the traveller ruled out in their notes
      must_include names the traveller asked for (always kept, boosted)
    """
    ctx = ctx or {}
    name = place.get("name", "")
    city = ctx.get("city")
    curated = _is_curated(place)
    notable = is_notable(place, votes)
    wanted_by_name = any(_normalize(m) and _normalize(m) in _normalize(name)
                         for m in (ctx.get("must_include") or ()))

    strongly_documented = bool(place.get("wikidata") or place.get("wikipedia")
                               or place.get("heritage") or place.get("source") == "wikipedia")
    if not curated and not wanted_by_name:
        # "Visvesvaraya Industrial and Technological Museum" contains
        # "industrial" - a sight word in the name PLUS an encyclopaedia entry
        # overrides the hard filter. "City Hospital" has neither.
        if is_hard_negative(name, city) and not (names_a_sight(name) and strongly_documented):
            return None
        if is_soft_negative(name, city) and not notable and not names_a_sight(name) \
                and not _is_famous_market(name):
            return None
        if _blocked_by_preferences(place, ctx):
            return None

    kind = _kind(place)
    all_interests = list(interests) + [i for i in (ctx.get("extra_interests") or ())
                                       if i not in interests]
    interest_f, matched = _interest_fraction(place, all_interests)
    pop_f, pop_note = _popularity_fraction(place, votes)
    sig_f = _significance_fraction(place, votes)
    agree_f, n_sources = _agreement_fraction(place, votes)
    type_f = KIND_BASE.get(kind, 4) / 10.0
    route_f, km = _route_fraction(place, ctx.get("anchor"))
    budget_f = _budget_fraction(place, ctx.get("spend_style"))
    season_f = _season_fraction(place, ctx.get("rain_share"))

    # A market is only a destination when it is a famous one, or when the
    # traveller said they want to shop.
    if kind == "marketplace" and "Shopping" in all_interests:
        type_f = max(type_f, 0.5)
    if kind == "mall" and "Shopping" in all_interests:
        type_f = max(type_f, 0.6)
    if kind == "place of worship" and "Spiritual" in all_interests:
        type_f = max(type_f, 0.8)

    raw = {
        "popularity": pop_f * ATTRACTION_WEIGHTS["popularity"],
        "significance": sig_f * ATTRACTION_WEIGHTS["significance"],
        "agreement": agree_f * ATTRACTION_WEIGHTS["agreement"],
        "type_quality": type_f * ATTRACTION_WEIGHTS["type_quality"],
    }
    quality_f = sum(raw.values()) / sum(ATTRACTION_WEIGHTS[k] for k in QUALITY_KEYS)
    # Interest points are EARNED in proportion to quality (see module docstring).
    raw["interest"] = ATTRACTION_WEIGHTS["interest"] * interest_f * (0.35 + 0.65 * quality_f)

    # Relevance scales the whole core: famous-but-not-what-you-asked-for loses
    # to famous-and-exactly-what-you-asked-for.
    # (No interests stated = no opinion, so nothing is scaled down.)
    relevance = (0.6 + 0.4 * interest_f) if all_interests else 1.0
    parts = {k: round(raw[k] * relevance, 1) for k in CORE_KEYS}
    parts["route"] = round(route_f * ATTRACTION_WEIGHTS["route"], 1)
    parts["budget_fit"] = round(budget_f * ATTRACTION_WEIGHTS["budget_fit"], 1)
    parts["season"] = round(season_f * ATTRACTION_WEIGHTS["season"], 1)
    parts = {k: parts[k] for k in ATTRACTION_WEIGHTS}
    total = sum(parts.values())

    # Generic things the traveller did not ask for are damped, not just
    # out-scored: a nightclub should not creep into a heritage trip.
    wanted_kinds = set()
    for interest in all_interests:
        wanted_kinds |= INTEREST_KINDS.get(interest, set())
    if kind in GENERIC_KINDS and not notable and kind not in wanted_kinds:
        total *= 0.6
    elif kind in MEAL_KINDS and "Food" not in all_interests:
        total *= 0.6        # restaurants are lunch stops, not sights

    # Evidence gate. A common kind of place (temple, park, market, bar...) that
    # NOTHING vouches for - no wikidata, no heritage tag, no tourism tag, no
    # second source, no rating - is the "neighbourhood shrine / colony park"
    # case. Matching an interest by type alone must not make it a pick, so it
    # stays below the "good" line and is only used if a town has nothing else.
    has_evidence = (notable or place.get("osm_attraction")
                    or place.get("fsq_rating") is not None)
    if not has_evidence and kind in EVIDENCE_REQUIRED_KINDS:
        total = min(total, NO_EVIDENCE_CAP)

    if wanted_by_name:
        total = max(total, 75.0)    # the traveller named it - it goes in

    total = max(0.0, min(100.0, total))

    # Evidence + confidence
    sitelinks = place.get("sitelinks")
    evidence = {
        "sources": n_sources,
        "curated": curated,
        "wikipedia": bool(place.get("wikipedia") or place.get("source") in ("wikipedia", "wikivoyage")),
        "wikidata": bool(place.get("wikidata")),
        "heritage": bool(place.get("heritage")),
        "sitelinks": sitelinks,
        "rating": place.get("fsq_rating"),
        "matched_interests": matched,
        "km_from_anchor": round(km, 1) if km is not None else None,
    }
    strong_evidence = (curated or evidence["wikipedia"] or evidence["heritage"]
                       or n_sources >= 2 or evidence["rating"] is not None
                       or (sitelinks or 0) >= 3)
    some_evidence = strong_evidence or evidence["wikidata"] or place.get("osm_attraction")
    if total >= 70 and strong_evidence:
        confidence = "high"
    elif total >= 45 and (some_evidence or total >= 60):
        confidence = "medium"
    else:
        confidence = "low"

    # Reasons - plain sentences built from the real parts, never from an LLM.
    reasons = []
    if wanted_by_name:
        reasons.append("You asked for this one")
    if curated:
        reasons.append("A well-known local landmark")
    if pop_note:
        reasons.append(pop_note)
    elif evidence["wikipedia"] and not curated:
        reasons.append("Has its own travel-guide entry")
    elif evidence["heritage"]:
        reasons.append("A listed heritage site")
    if n_sources >= 2:
        reasons.append(f"Named by {n_sources} independent sources")
    if matched:
        reasons.append("Matches your interest in " + " & ".join(matched[:2]))
    level = COST_LEVEL.get(kind, 1)
    if ctx.get("spend_style") and level <= STYLE_COST_CAP.get(ctx["spend_style"], 2) and level <= 1:
        reasons.append(COST_NOTE[level] + " - easy on your budget")
    if km is not None and km <= 5:
        reasons.append(f"Only {km:.1f} km from where you stay" if ctx.get("anchor_is_stay")
                       else f"Only {km:.1f} km from the centre")
    if ctx.get("rain_share") is not None and ctx["rain_share"] >= 0.4 and kind in INDOOR_KINDS:
        reasons.append("A good indoor option for the rainy forecast")
    if not reasons:
        reasons.append("A decent local option - fewer signals than the headline sights")

    return {
        "score": round(total),
        "parts": parts,
        "confidence": confidence,
        "evidence": evidence,
        "reasons": reasons[:4],
        "why": ". ".join(reasons[:3]) + ".",
        "cost_level": level,
        "cost_note": COST_NOTE[level],
        "kind": kind,
    }


# ---------------------------------------------------------------------------
# Stay score
# ---------------------------------------------------------------------------

STYLE_PREFERRED_KINDS = {
    "budget": {"hostel": 1.0, "guest house": 0.9, "apartment": 0.7, "hotel": 0.5, "resort": 0.2},
    "balanced": {"hotel": 1.0, "guest house": 0.8, "apartment": 0.8, "resort": 0.6, "hostel": 0.4},
    "comfort": {"resort": 1.0, "hotel": 0.9, "apartment": 0.6, "guest house": 0.4, "hostel": 0.15},
}
# Where in the affordable range each travel style wants to land.
STYLE_TARGET_SHARE = {"budget": 0.45, "balanced": 0.8, "comfort": 1.0}


def _stay_location_fraction(stay: dict, anchor: tuple[float, float] | None) -> tuple[float, float | None]:
    if not anchor:
        return 0.6, None
    km = haversine_km(anchor[0], anchor[1], stay["lat"], stay["lon"])
    for limit, frac in ((1.5, 1.0), (3, 0.85), (6, 0.6), (10, 0.35)):
        if km <= limit:
            return frac, km
    return 0.1, km


def group_price(stay: dict) -> int:
    """Per-night cost for the whole group (see tools/stays.price_for_group)."""
    return int(stay.get("group_price_per_night_inr", stay.get("price_per_night_inr", 0)))


def score_stay(stay: dict, ceiling: int | None, travel_style: str = "balanced",
               anchor: tuple[float, float] | None = None) -> dict:
    """
    Score one place to stay.

    ceiling: the most the budget leaves per night. None = unknown (no budget
    pressure), 0 = the budget has nothing left for a bed.
    """
    # What the WHOLE GROUP pays per night (rooms x rate, or beds x rate for a
    # hostel). Falls back to the single-room rate when the group size is unknown.
    price = group_price(stay)

    # --- budget fit ---
    if ceiling is None:
        budget_f = 0.7
        affordable = True
    elif price > ceiling:
        affordable = False
        # Slightly over is "stretch", far over is hopeless.
        budget_f = max(0.0, 0.25 - (price - ceiling) / max(1, ceiling or price) * 0.25)
    else:
        affordable = True
        target = ceiling * STYLE_TARGET_SHARE.get(travel_style, 0.8)
        cap = STYLE_PRICE_CAP.get(travel_style)
        if cap:
            target = min(target, cap)
        target = max(1.0, target)
        if price > target:
            # above what this style wants to pay, even though it is affordable
            floor = 0.05 if travel_style == "budget" else 0.3
            budget_f = max(floor, 1.0 - (price - target) / target * 0.6)
        else:
            budget_f = max(0.55, 1.0 - (target - price) / target * 0.45)

    # --- quality ---
    stars_f = min(1.0, stay.get("stars", 2) / 5.0)
    if not stay.get("stars_tagged", True):
        stars_f *= 0.85                 # an assumed rating is weaker evidence
    rating = stay.get("rating")          # Foursquare 0-10
    if rating is not None:
        quality_f = 0.45 * stars_f + 0.55 * max(0.0, min(1.0, float(rating) / 10.0))
    else:
        quality_f = stars_f

    # --- location ---
    loc_f, km = _stay_location_fraction(stay, anchor)

    # --- evidence ---
    n_src = stay.get("source_count", 1)
    evidence_f = 0.35 + 0.25 * min(2, n_src - 1) / 1 if n_src > 1 else 0.35
    if stay.get("website"):
        evidence_f += 0.15
    if stay.get("address"):
        evidence_f += 0.1
    if rating is not None:
        evidence_f += 0.15
    evidence_f = min(1.0, evidence_f)

    # --- style fit ---
    prefs = STYLE_PREFERRED_KINDS.get(travel_style, STYLE_PREFERRED_KINDS["balanced"])
    style_f = prefs.get(stay.get("kind", "hotel"), 0.5)

    fractions = {"budget": budget_f, "quality": quality_f, "location": loc_f,
                 "evidence": evidence_f, "style": style_f}
    weights = STAY_WEIGHTS_BY_STYLE.get(travel_style, STAY_WEIGHTS)
    parts = {k: round(fractions[k] * weights[k], 1) for k in weights}
    total = sum(parts.values())
    if not affordable:
        total = min(total, 45.0)        # an unaffordable bed can never be the pick

    if stay.get("is_price_band"):
        confidence = "low"
    elif rating is not None or n_src >= 2:
        confidence = "high" if total >= 65 else "medium"
    else:
        confidence = "medium" if total >= 55 else "low"

    reasons = []
    if ceiling is not None and affordable and ceiling > 0:
        left = ceiling - price
        if left >= 0:
            reasons.append(f"Fits your budget with ₹{left:,} a night to spare")
    elif ceiling is not None and not affordable:
        reasons.append(f"₹{price - ceiling:,} a night above your stay budget")
    if km is not None and km <= 3:
        reasons.append(f"Only {km:.1f} km from the places you will visit")
    elif km is not None and km <= 6:
        reasons.append(f"About {km:.1f} km from the main sights")
    if rating is not None:
        reasons.append(f"Rated {float(rating):.1f}/10 by visitors")
    elif stay.get("stars_tagged", True) and stay.get("stars", 0) >= 3:
        reasons.append(f"{stay['stars']}-star property")
    if n_src >= 2:
        reasons.append("Confirmed by two independent map sources")
    if style_f >= 0.9:
        reasons.append("Suits your " + travel_style + " travel style")
    if not reasons:
        reasons.append("A real, mapped property near the destination")

    return {
        "score": round(total),
        "parts": parts,
        "confidence": confidence,
        "affordable": affordable,
        "km_from_sights": round(km, 1) if km is not None else None,
        "reasons": reasons[:4],
        "why": ". ".join(reasons[:3]) + ".",
    }


def rank_stays(stays: list[dict], ceiling: int | None, travel_style: str = "balanced",
               anchor: tuple[float, float] | None = None) -> list[dict]:
    """Every stay with its recommendation attached, best first (copies, input untouched)."""
    ranked = []
    for s in stays:
        item = dict(s)
        item["recommendation"] = score_stay(s, ceiling, travel_style, anchor)
        item["score"] = item["recommendation"]["score"]
        item["why"] = item["recommendation"]["why"]
        ranked.append(item)
    # Ties go to the cheaper bed - the budget is the point of this product.
    ranked.sort(key=lambda s: (-s["score"], group_price(s)))
    return ranked


def centroid(places: list[dict], top: int = 8) -> tuple[float, float] | None:
    """Where the best attractions are, on average - a hotel here saves the most travel."""
    pts = [p for p in places[:top] if p.get("lat") is not None and p.get("lon") is not None]
    if not pts:
        return None
    return (sum(p["lat"] for p in pts) / len(pts), sum(p["lon"] for p in pts) / len(pts))


def best_area(places: list[dict], top: int = 10, radius_km: float = 5.0) -> tuple[float, float] | None:
    """
    Where to sleep: the centre of the densest group of GOOD sights.

    v23 used the plain average of the top 8, which a single day-trip spot
    drags off into nowhere (Dehradun + Mussoorie averaged to a point halfway
    up the hill road). Here each top place is weighted by how much good stuff
    sits within `radius_km` of it; the winner's neighbourhood is averaged.
    """
    pts = [p for p in places[:top] if p.get("lat") is not None and p.get("lon") is not None]
    if not pts:
        return None

    def neighbours(p):
        return [q for q in pts if haversine_km(p["lat"], p["lon"], q["lat"], q["lon"]) <= radius_km]

    hub = max(pts, key=lambda p: (sum(q.get("score", 50) for q in neighbours(p)), p.get("score", 0)))
    group = neighbours(hub)
    return (sum(q["lat"] for q in group) / len(group), sum(q["lon"] for q in group) / len(group))


def summarize_days(days: list[dict]) -> dict:
    """One honest line about how strong the chosen stops are, for the summary rail."""
    stops = [s for d in days for s in d.get("stops", []) if s.get("score") is not None]
    if not stops:
        return {"stops": 0}
    high = sum(1 for s in stops if s.get("confidence") == "high")
    weak = [s["name"] for s in stops if s.get("confidence") == "low"]
    return {
        "stops": len(stops),
        "high_confidence": high,
        "average_score": round(sum(s["score"] for s in stops) / len(stops)),
        "must_see": sum(1 for s in stops if s.get("must_see")),
        "weak_stops": weak,
    }
