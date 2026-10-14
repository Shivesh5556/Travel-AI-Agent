"""
One place where all outgoing HTTP requests happen.

Rules we follow everywhere:
  1. Always send a User-Agent (OpenStreetMap will block us otherwise).
  2. Always have a timeout, so a dead API cannot freeze the whole plan.
  3. Never raise. If a call fails we return None and the tool that asked for
     it falls back to offline sample data. A demo that keeps working with no
     internet is worth a lot.
  4. Cache the answer, because free APIs rate limit you fast.
"""

import asyncio
import hashlib
import json
import time
from urllib.parse import urlparse

import httpx

import cache
import config
import metrics

# Overpass either answers fast or it is overloaded. 10s was cutting off
# mirrors that were just slow (not dead), producing false ReadTimeouts on
# ordinary connections. 15s gives a fair shot without stalling the plan.
OVERPASS_TIMEOUT = 15.0


def _describe_error(e: Exception) -> str:
    """
    `type(e).__name__` alone hides the one thing you actually need to debug
    a failing API call: was this a timeout, a 429 rate-limit, a 504 from an
    overloaded server, or a 400 because our query is malformed? Those need
    completely different fixes, so print the status code when we have one.
    """
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return type(e).__name__


# --- Per-host circuit breaker ---------------------------------------------
#
# The v26 log showed the same refusals again and again inside ONE plan:
# Wikipedia/Wikivoyage 403 x6, Foursquare 401 x10. A 401/403 will not fix
# itself in the next second, so after the first one that host is skipped for
# a while and the log says once, clearly, what to change.

BLOCK_SECONDS = {401: 30 * 60, 403: 30 * 60, 429: 90}
_blocked: dict[str, tuple[float, str]] = {}

_HINTS = {
    "wiki": ("Wikimedia (Wikipedia/Wikivoyage/Wikidata) refused the request. It requires a "
             "User-Agent with contact details: set NOMINATIM_CONTACT=<your email> in "
             "backend/.env and restart. Skipping it for now."),
    "foursquare": ("Foursquare rejected the key. Either remove FOURSQUARE_API_KEY from .env "
                   "(it is optional) or, for a new-style key, set "
                   "FOURSQUARE_URL=https://places-api.foursquare.com/places/search. "
                   "Skipping Foursquare for now."),
    "geoapify": "Geoapify rejected the key - check GEOAPIFY_API_KEY. Skipping it for now.",
    "ticketmaster": "Ticketmaster rejected the key - check TICKETMASTER_API_KEY. Skipping it.",
}


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return url


def _is_blocked(url: str) -> bool:
    host = _host(url)
    entry = _blocked.get(host)
    if not entry:
        return False
    if time.monotonic() > entry[0]:
        _blocked.pop(host, None)
        return False
    return True


CONNECT_PAUSE_SECONDS = 60


def _note_failure(url: str, e: Exception) -> None:
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError)):
        # Cannot even open a connection (offline, firewall, DNS): a retry a
        # second later inside the same plan will fail the same way.
        code, seconds = type(e).__name__, CONNECT_PAUSE_SECONDS
    elif isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        seconds = BLOCK_SECONDS.get(code)
    else:
        return
    if not seconds:
        return
    host = _host(url)
    if host in _blocked:
        return
    _blocked[host] = (time.monotonic() + seconds, str(code))
    hint = next((text for key, text in _HINTS.items() if key in host), "")
    what = f"answered HTTP {code}" if isinstance(code, int) else f"is unreachable ({code})"
    print(f"[http] {host} {what} - pausing calls to it for "
          f"{seconds // 60 or 1} min. {hint if isinstance(code, int) else ''}".rstrip())


def reset_blocked_hosts():
    _blocked.clear()


def _headers_for(url: str, extra: dict | None) -> dict:
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    if "wiki" in _host(url):
        # Wikimedia's API etiquette: identify the tool and a contact.
        headers["Api-User-Agent"] = config.USER_AGENT
    if extra:
        headers.update(extra)
    return headers


def _cache_key(url: str, params, body) -> str:
    """Turn a whole request into one short string we can use as a cache key."""
    blob = json.dumps({"u": url, "p": params, "b": body}, sort_keys=True, default=str)
    return "http:" + hashlib.sha256(blob.encode()).hexdigest()[:24]


async def get_json(url: str, params: dict | None = None, label: str = "http_get",
                   headers: dict | None = None, ttl: int | None = None,
                   timeout: float | None = None):
    """GET something and parse it as JSON. Returns None if anything goes wrong."""
    key = _cache_key(url, params, None)
    cached = await cache.get(key)
    if cached is not None:
        return cached
    if _is_blocked(url):
        return None

    all_headers = _headers_for(url, headers)

    try:
        with metrics.Timer(label):
            async with httpx.AsyncClient(timeout=timeout or config.HTTP_TIMEOUT) as client:
                response = await client.get(url, params=params, headers=all_headers)
                response.raise_for_status()
                data = response.json()
    except Exception as e:
        print(f"[http] GET {label} failed ({_describe_error(e)}) - using fallback data")
        _note_failure(url, e)
        return None

    await cache.set(key, data, ttl)
    return data


async def post_json(url: str, body=None, label: str = "http_post",
                    headers: dict | None = None, ttl: int | None = None,
                    form: bool = False, timeout: float | None = None):
    """
    POST something. `form=True` sends it as form data instead of JSON, which
    is what the Overpass API wants.
    """
    key = _cache_key(url, None, body)
    cached = await cache.get(key)
    if cached is not None:
        return cached
    if _is_blocked(url):
        return None

    all_headers = {"User-Agent": config.USER_AGENT}
    if headers:
        all_headers.update(headers)

    try:
        with metrics.Timer(label):
            async with httpx.AsyncClient(timeout=timeout or config.HTTP_TIMEOUT) as client:
                if form:
                    response = await client.post(url, data=body, headers=all_headers)
                else:
                    response = await client.post(url, json=body, headers=all_headers)
                response.raise_for_status()
                data = response.json()
    except Exception as e:
        if not isinstance(e, asyncio.CancelledError):
            print(f"[http] POST {label} failed ({_describe_error(e)}) - using fallback data")
            _note_failure(url, e)
        return None

    await cache.set(key, data, ttl)
    return data


# --- Overpass: race the mirrors ------------------------------------------
#
# v26 asked the mirrors ONE AT A TIME with a 15 s timeout each. In the log,
# three mirrors timed out in a row (45 s) before the fourth answered - twice
# per plan. Now the best mirror is asked first and, if it has not answered
# within HEDGE_SECONDS, the next one is asked IN PARALLEL, and so on. The
# first good answer wins and the rest are cancelled. Mirrors are ordered by
# their recent record (fewest failures, fastest answers first), so a mirror
# that just worked is tried first next time.

HEDGE_SECONDS = 4.0
OVERALL_DEADLINE = 25.0
MIRROR_FAILURE_LIMIT = 2

_mirror_failures: dict[str, int] = {}
_mirror_speed: dict[str, float] = {}       # seconds, exponential average


def _live_mirrors() -> list[str]:
    live = [url for url in config.OVERPASS_MIRRORS
            if _mirror_failures.get(url, 0) < MIRROR_FAILURE_LIMIT]
    order = {url: i for i, url in enumerate(config.OVERPASS_MIRRORS)}
    return sorted(live, key=lambda u: (_mirror_failures.get(u, 0),
                                       _mirror_speed.get(u, 8.0), order[u]))


def reset_mirrors():
    """Give the dead mirrors another chance (called by the background monitor)."""
    _mirror_failures.clear()


async def overpass(query: str, label: str, ttl: int | None = None,
                   timeout: float | None = None):
    """Run an Overpass query, racing the live mirrors (see above)."""
    cache_key = _cache_key("overpass", None, query)
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    mirrors = _live_mirrors()
    if not mirrors:
        print(f"[overpass] every mirror is marked down, skipping {label}")
        return None

    per_try = timeout or OVERPASS_TIMEOUT
    started = time.monotonic()

    async def attempt(index: int, url: str):
        t0 = time.monotonic()
        data = await post_json(url, body={"data": query}, form=True,
                               label=f"{label}_mirror{index + 1}", ttl=None, timeout=per_try)
        return url, data, time.monotonic() - t0

    pending: set[asyncio.Task] = set()
    queue = list(enumerate(mirrors))
    result = None
    try:
        while queue or pending:
            if queue:          # one more mirror joins the race each round
                index, url = queue.pop(0)
                pending.add(asyncio.create_task(attempt(index, url)))
            remaining = OVERALL_DEADLINE - (time.monotonic() - started)
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=min(HEDGE_SECONDS, remaining) if queue else remaining,
                return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                url, data, seconds = task.result()
                if data is not None and "elements" in data:
                    result = data
                    _mirror_failures[url] = 0
                    _mirror_speed[url] = 0.6 * _mirror_speed.get(url, seconds) + 0.4 * seconds
                    break
                _mirror_failures[url] = _mirror_failures.get(url, 0) + 1
                if _mirror_failures[url] >= MIRROR_FAILURE_LIMIT:
                    print(f"[overpass] marking {url.split('/')[2]} as down for this session")
            if result is not None:
                break
    finally:
        for task in pending:
            task.cancel()

    if result is None:
        print(f"[overpass] no mirror answered for {label}")
        return None
    await cache.set(cache_key, result, ttl)
    return result
