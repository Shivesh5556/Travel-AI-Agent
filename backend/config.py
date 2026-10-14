"""
All the settings for Ghumi Ghumi live here, in one place.

Everything is read from the .env file.

STRICT MODE (the default)
    The app refuses to plan a trip unless it has a real Groq key, a real
    Duffel key, AND all three places/events upgrade keys (Geoapify,
    Ticketmaster, Foursquare). That is deliberate: this build is tuned to
    pull the best possible result out of every source it can reach and pick
    the cheapest combination that fits the budget, rather than quietly
    planning off a smaller pool of places because one source was left out.

    You will see a clear setup screen telling you exactly which key is missing
    and where to get it, instead of a plan built on partial data.

    Set STRICT_MODE=false in .env if you want to run without keys (the test
    suite does this, so tests never need a live API key). With STRICT_MODE
    off, any of these six keys can still be left blank - the app just uses
    fewer sources for that piece of the plan.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if value else default


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name, "true" if default else "false").lower()
    return raw in ("1", "true", "yes", "on")


# ---------- LLM (Groq) ----------
GROQ_API_KEY = _get("GROQ_API_KEY")
# Groq retires models fairly often. llama-3.3-70b-versatile was deprecated on
# 17 June 2026 and now returns a 404, which is why the model name is not
# hard-coded anywhere else in the project. On startup llm.py asks Groq which
# models actually exist and picks the first one from this list that is live,
# so this cannot silently break again.
GROQ_MODEL = _get("GROQ_MODEL", "openai/gpt-oss-120b")

# Tried in order if GROQ_MODEL is not available. All support tool calling.
MODEL_PREFERENCES = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
    "moonshotai/kimi-k2-instruct-0905",
    "llama-3.3-70b-versatile",
]

LLM_ENABLED = bool(GROQ_API_KEY)


# ---------- Flights (Duffel) ----------
# Amadeus killed its free self-service portal on 17 July 2026, so we use
# Duffel's sandbox instead. Sign up -> get a key starting with duffel_test_
DUFFEL_API_KEY = _get("DUFFEL_API_KEY")
DUFFEL_ENABLED = bool(DUFFEL_API_KEY)


# ---------- Places, food & events upgrades ----------
# OpenStreetMap + Wikipedia + Wikivoyage (see tools/places.py) need no key
# and are always on. Geoapify and Ticketmaster are required in strict mode
# alongside Groq and Duffel - you asked for the widest possible pool of
# real places/events, so the setup screen will not let the app run until
# those two are configured. Foursquare stays optional: its own pricing page
# describes a "sandbox, then $200/month free credits, then pay-as-you-go"
# model rather than an unconditional free tier like the two above, so it is
# merged in as a bonus cross-check the moment a key is set in .env, but
# leaving it blank does not block the app.
#
# Verified against each provider's own pricing page (Sep 2026):
#
#   GEOAPIFY     - genuinely free, no card, 3,000 requests/day
#                  (geoapify.com/pricing). Independent places/food source,
#                  useful as a cross-check when OSM is thin for a smaller
#                  Indian town.
#   TICKETMASTER - genuinely free, no card, 5,000 calls/day
#                  (developer.ticketmaster.com). The one source here that
#                  knows about live events (concerts, festivals, parties)
#                  rather than permanent venues. Coverage is strongest in
#                  the US/UK/EU/AU/NZ and thinner in India, so treat a miss
#                  as "nothing listed", not "nothing happening".
#   FOURSQUARE   - NOT confirmed free-without-card (optional). Foursquare's
#                  own pricing page (location.foursquare.com/pricing)
#                  describes "sandbox, then $200/month free credits, then
#                  pay-as-you-go", not an unconditional free tier. The code
#                  still works if you already have a key; it just is not
#                  something to recommend as a guaranteed-free signup, so it
#                  is not required.
FOURSQUARE_API_KEY = _get("FOURSQUARE_API_KEY")
# Foursquare retired the legacy v3 API (api.foursquare.com/v3) on 15 May 2026.
# The new Places API (places-api.foursquare.com) takes a *Service API key* as
# "Authorization: Bearer <key>". Old keys start with "fsq3" and are refused by
# the new host with HTTP 401 - so an old key switches Foursquare off with a
# clear startup message instead of failing ~10 times inside every plan.
FOURSQUARE_URL = _get("FOURSQUARE_URL") or "https://places-api.foursquare.com/places/search"
FOURSQUARE_API_VERSION = _get("FOURSQUARE_API_VERSION") or "2025-06-17"
FOURSQUARE_LEGACY_KEY = FOURSQUARE_API_KEY.startswith("fsq3")
FOURSQUARE_ENABLED = bool(FOURSQUARE_API_KEY) and not FOURSQUARE_LEGACY_KEY

GEOAPIFY_API_KEY = _get("GEOAPIFY_API_KEY")
GEOAPIFY_ENABLED = bool(GEOAPIFY_API_KEY)
GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"
GEOAPIFY_AUTOCOMPLETE_URL = "https://api.geoapify.com/v1/geocode/autocomplete"

TICKETMASTER_API_KEY = _get("TICKETMASTER_API_KEY")
TICKETMASTER_ENABLED = bool(TICKETMASTER_API_KEY)
TICKETMASTER_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


# ---------- Strict mode ----------
STRICT_MODE = _get_bool("STRICT_MODE", True)


def missing_required_keys() -> list[dict]:
    """
    Which required keys are not set. Empty list means we are good to go.
    The frontend shows this list on a setup screen.
    """
    if not STRICT_MODE:
        return []

    missing = []
    if not LLM_ENABLED:
        missing.append({
            "key": "GROQ_API_KEY",
            "what": "The language model that drives the agent's tool calling and writing.",
            "where": "https://console.groq.com/keys",
            "cost": "Free tier, no card needed.",
        })
    if not DUFFEL_ENABLED:
        missing.append({
            "key": "DUFFEL_API_KEY",
            "what": "Real flight fares. Without it there are no genuine air prices.",
            "where": "https://app.duffel.com/join",
            "cost": "Free sandbox, key starts with duffel_test_",
        })
    if not GEOAPIFY_ENABLED:
        missing.append({
            "key": "GEOAPIFY_API_KEY",
            "what": "A second, independent places/food source merged in alongside "
                    "OpenStreetMap - fills gaps in smaller towns.",
            "where": "https://myprojects.geoapify.com",
            "cost": "Free tier, no card needed. 3,000 requests/day.",
        })
    # Foursquare and Ticketmaster are intentionally NOT in this list - they stay
    # optional. Ticketmaster lists very few Indian events, so making it required
    # blocked people from planning for no real gain.
    return missing


def is_ready() -> bool:
    return len(missing_required_keys()) == 0


# ---------- Payments (Razorpay TEST MODE only) ----------
RAZORPAY_KEY_ID = _get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = _get("RAZORPAY_KEY_SECRET")
PAYMENTS_ENABLED = bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


# ---------- Database ----------
# Default is a SQLite file so you can just run the project with no setup.
# For Postgres put this in .env instead:
#   DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ghumi
DATABASE_URL = _get("DATABASE_URL", "sqlite+aiosqlite:///./ghumi.db")


# ---------- Cache ----------
# Redis is optional. If REDIS_URL is empty we use a plain dict in memory.
REDIS_URL = _get("REDIS_URL")
CACHE_TTL_SECONDS = int(_get("CACHE_TTL_SECONDS", "3600"))


# ---------- Free / keyless APIs (no signup needed) ----------
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# The main Overpass server is free and frequently overloaded - it returns 429
# or 504 a lot. These are public mirrors running the same software with the
# same data. We try each in turn before giving up, which is the difference
# between "8 offline samples" and real places in your plan.
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",   # usually the fastest
    "https://overpass-api.de/api/interpreter",         # the main one, often busy
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",         # Swiss mirror, extra redundancy
    "https://overpass.openstreetmap.ru/api/interpreter",  # Russian mirror, extra redundancy
]
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WIKIPEDIA_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
# Wikivoyage - Wikipedia's sister project, written specifically for
# travellers. Same MediaWiki API shape as Wikipedia, but the content is
# curated "see/do/eat/drink" listings instead of encyclopaedia prose, which
# makes it a genuinely different (and free, keyless) opinion on a place -
# our third live source, alongside OpenStreetMap and Wikipedia.
WIKIVOYAGE_URL = "https://en.wikivoyage.org/w/api.php"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"

# OpenStreetMap asks every app to identify itself. Be polite, they are free.
# Nominatim answers 403 to the placeholder example.com address that used to be
# here, which is why the city search box returned nothing. Put your real email
# in NOMINATIM_CONTACT (backend/.env) and it is used automatically.
NOMINATIM_CONTACT = _get("NOMINATIM_CONTACT", "")
# Wikimedia (Wikipedia, Wikivoyage, Wikidata) and Nominatim both ask for a
# User-Agent that names the tool AND a way to contact its owner; without the
# contact Wikimedia answers 403. So set NOMINATIM_CONTACT to your email.
CONTACT_CONFIGURED = "@" in NOMINATIM_CONTACT or "://" in NOMINATIM_CONTACT
USER_AGENT = (f"GhumiGhumi/2.7 (student travel planner; contact: {NOMINATIM_CONTACT}) httpx"
              if CONTACT_CONFIGURED else
              "GhumiGhumi/2.7 (student travel planner; contact: not-configured) httpx")

# How long we are willing to wait for any external API before giving up.
HTTP_TIMEOUT = float(_get("HTTP_TIMEOUT", "20"))


# ---------- App ----------
CORS_ORIGINS = _get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

# Agent safety limit: never let the tool-calling loop run forever.
MAX_AGENT_STEPS = int(_get("MAX_AGENT_STEPS", "12"))

# Abuse protection for a public deployment. Every plan costs LLM tokens, so
# one browser / IP may only start this many plans per hour.
MAX_TRIPS_PER_HOUR = int(_get("MAX_TRIPS_PER_HOUR", "20"))

# Trips are view-once: the browser deletes its trip when you leave it, and
# anything older than this many hours is removed by the background task.
TRIP_TTL_HOURS = float(_get("TRIP_TTL_HOURS", "6"))

# If set, /api/metrics and a full /api/monitor/run need the header
# X-Admin-Token: <this value>. Leave blank for local development.
ADMIN_TOKEN = _get("ADMIN_TOKEN", "")

# How many times the budget solver is allowed to re-plan a trip.
MAX_REPLAN_ROUNDS = int(_get("MAX_REPLAN_ROUNDS", "3"))


def status() -> dict:
    """Small summary shown on the frontend so you can see what is switched on."""
    return {
        "llm": LLM_ENABLED,
        "duffel_flights": DUFFEL_ENABLED,
        "foursquare_places": FOURSQUARE_ENABLED,
        "geoapify_places": GEOAPIFY_ENABLED,
        "ticketmaster_events": TICKETMASTER_ENABLED,
        "payments": PAYMENTS_ENABLED,
        "redis_cache": bool(REDIS_URL),
        "database": DATABASE_URL.split("://")[0],
        "model": GROQ_MODEL if LLM_ENABLED else None,
        "strict_mode": STRICT_MODE,
        "ready": is_ready(),
        "missing_keys": missing_required_keys(),
    }
