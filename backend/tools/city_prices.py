"""
How expensive is this city?

ONE place answers that question for the whole project:

  * tools/stays.py uses it to estimate hotel prices
  * tools/budget.py uses it to estimate food, local transport and activities
  * agent/replan.py gets it through budget.py, so the solver, the hotel
    ceiling and the final bill can never disagree

Before v31, hotel prices knew Mumbai was expensive but the daily budget used
the same Rs 1,750 a day everywhere. Two price models that disagree is a bug,
so they now read the same index.

Pure Python. No network, no LLM, easy to test (tests/test_city_costs.py).
"""

import re

# How pricey a city is for a HOTEL night, compared with an average Indian
# city (1.0). Moved here unchanged from tools/stays.py.
HOTEL_INDEX = {
    "mumbai": 1.8, "delhi": 1.5, "bengaluru": 1.5, "goa": 1.6, "leh": 1.4,
    "udaipur": 1.4, "gangtok": 1.1, "jaipur": 1.2, "manali": 1.2, "shimla": 1.2,
    "munnar": 1.2, "andaman": 1.7, "varanasi": 0.9, "agra": 1.0, "hampi": 0.8,
    "pushkar": 0.8, "rishikesh": 0.9,
}

# Other spellings people (and map APIs) use for the same place.
ALIASES = {
    "bombay": "mumbai",
    "new delhi": "delhi",
    "bangalore": "bengaluru",
    "panaji": "goa", "panjim": "goa", "north goa": "goa", "south goa": "goa",
    "calangute": "goa", "baga": "goa",
    "port blair": "andaman", "andaman and nicobar islands": "andaman",
    "benaras": "varanasi", "banaras": "varanasi", "kashi": "varanasi",
}

# Food, autos and entry tickets do NOT swing between cities as much as hotel
# rooms do: a Mumbai hotel can cost 1.8x the average, but a thali there does
# not cost 1.8x. So the daily costs only move part of the way with the hotel
# index. 0.5 means "half as sensitive to the city as hotels are".
DAILY_SENSITIVITY = {
    "food": 0.5,
    "local_transport": 0.4,
    "activities": 0.3,
}

# Safety rails: one wrong number in HOTEL_INDEX must not produce a silly budget.
MIN_DAILY_MULTIPLIER = 0.75
MAX_DAILY_MULTIPLIER = 1.6

# Researched, per-city daily costs WIN over the index when you have them.
# Leave a city out until you have real figures - the index covers it until then.
# Every entry must give all three styles and say where the numbers came from:
#
#   "jaipur": {
#       "food":            {"shoestring": 400, "normal": 800,  "comfort": 1600},
#       "local_transport": {"shoestring": 150, "normal": 300,  "comfort": 800},
#       "activities":      {"shoestring": 250, "normal": 550,  "comfort": 1200},
#       "source": "menu prices from 15 restaurants + RTO auto fares + ASI tickets",
#       "checked": "2026-11",
#   },
RESEARCHED_DAILY = {}


def normalise_city(name: str | None) -> str:
    """
    "Jaipur, Rajasthan" -> "jaipur", "  Bangalore " -> "bengaluru".

    This matters: when somebody picks a city from the autocomplete box,
    schemas.py turns the name into "Jaipur, Rajasthan". A plain dict lookup on
    that string misses, and the city silently gets average prices.
    """
    if not name:
        return ""
    city = re.sub(r"\(.*?\)", " ", str(name))     # "Goa (12.34, 56.78)"
    city = city.split(",")[0]                     # "Jaipur, Rajasthan"
    city = re.sub(r"\s+", " ", city).strip().lower()
    return ALIASES.get(city, city)


def hotel_multiplier(city: str | None) -> float:
    """The hotel price index for this city, 1.0 if we have no figure."""
    return HOTEL_INDEX.get(normalise_city(city), 1.0)


def is_known(city: str | None) -> bool:
    key = normalise_city(city)
    return key in HOTEL_INDEX or key in RESEARCHED_DAILY


def daily_multipliers(city: str | None) -> dict:
    """
    How much to scale each daily cost for this city, e.g. Mumbai:
        {"food": 1.4, "local_transport": 1.32, "activities": 1.24}
    """
    index = hotel_multiplier(city)
    out = {}
    for item, sensitivity in DAILY_SENSITIVITY.items():
        value = 1 + (index - 1) * sensitivity
        out[item] = round(min(MAX_DAILY_MULTIPLIER, max(MIN_DAILY_MULTIPLIER, value)), 3)
    return out
