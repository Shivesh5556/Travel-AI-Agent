"""
A tiny cache.

Why bother? Two reasons:
1. OpenStreetMap's Overpass API is free but slow and it will rate limit us
   if we hammer it. Same city searched twice should hit the cache.
2. LLM calls cost tokens. Cached answers cost nothing.

If REDIS_URL is set in .env we use Redis. If not, we use a normal Python
dict. Same two functions either way, so the rest of the code does not care.
"""

import json
import time

import config

# The fallback cache: key -> (value, expiry_timestamp)
_memory_cache: dict[str, tuple[str, float]] = {}

_redis = None
if config.REDIS_URL:
    try:
        import redis.asyncio as redis_lib

        _redis = redis_lib.from_url(config.REDIS_URL, decode_responses=True)
        print("[cache] using Redis at", config.REDIS_URL)
    except Exception as e:  # pragma: no cover - only happens on a bad setup
        print("[cache] Redis not available, falling back to memory:", e)
        _redis = None
else:
    print("[cache] REDIS_URL not set, using in-memory cache")


# Counters so we can show a cache hit-rate on the /api/metrics page.
STATS = {"hits": 0, "misses": 0}


async def get(key: str):
    """Return the cached value for a key, or None if we do not have it."""
    if _redis is not None:
        raw = await _redis.get(key)
        if raw is None:
            STATS["misses"] += 1
            return None
        STATS["hits"] += 1
        return json.loads(raw)

    entry = _memory_cache.get(key)
    if entry is None:
        STATS["misses"] += 1
        return None

    value, expires_at = entry
    if time.time() > expires_at:
        # expired, throw it away
        del _memory_cache[key]
        STATS["misses"] += 1
        return None

    STATS["hits"] += 1
    return json.loads(value)


async def set(key: str, value, ttl: int | None = None):
    """Store a value under a key for ttl seconds."""
    ttl = ttl or config.CACHE_TTL_SECONDS
    raw = json.dumps(value)

    if _redis is not None:
        await _redis.set(key, raw, ex=ttl)
    else:
        _memory_cache[key] = (raw, time.time() + ttl)


def hit_rate() -> float:
    total = STATS["hits"] + STATS["misses"]
    if total == 0:
        return 0.0
    return round(STATS["hits"] / total * 100, 1)
