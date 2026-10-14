"""
Translating the plan into Hindi (or another Indian language) on demand.

How it works:
  - The frontend collects every piece of plan text it wants to show (day
    descriptions, tips, issues, weather words...) and POSTs them in one go.
  - We de-duplicate, answer what we already know from the cache, and send the
    rest to the same Groq model in small batches.
  - Place names, numbers, times, rupee amounts and units stay exactly as they
    are - only the sentences around them are translated.
  - If the model is unreachable we hand back the English text unchanged. A
    plan in English beats a broken page.

Fixed interface labels (buttons, headings) are NOT translated here - those are
a static dictionary in the frontend, so they appear instantly and cost no tokens.
"""

import hashlib

import cache
import config
import llm

LANGUAGES = {
    "hi": "Hindi (Devanagari script)",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "gu": "Gujarati",
    "pa": "Punjabi (Gurmukhi script)",
}

TRANSLATE_TTL = 60 * 60 * 24 * 30
MAX_CHARS_PER_BATCH = 5000
MAX_ITEMS_PER_BATCH = 30

SYSTEM_PROMPT = """You are a careful translator for a travel-planning app used in India.
Translate each item's "text" into {language}.

Rules:
- Natural, simple, friendly spoken style that an ordinary traveller understands.
  Not stiff or bookish. Common English words people really use (hotel, train,
  bus, bus stand, budget) may stay in English.
- Do NOT translate or change: names of places, hotels, restaurants, trains and
  airlines; numbers; dates; times such as 9:00 AM; currency such as Rs 1,200 or
  the rupee sign; units such as km, h, C; URLs.
- Keep the meaning exactly. Do not add or remove information.
- Return the same ids you were given.

Reply as JSON: {{"items": [{{"id": 0, "text": "..."}}, ...]}}"""


def _key(lang: str, text: str) -> str:
    return "tr:" + lang + ":" + hashlib.sha256(text.encode()).hexdigest()[:24]


def _batches(items: list[tuple[int, str]]):
    batch, size = [], 0
    for item in items:
        if batch and (len(batch) >= MAX_ITEMS_PER_BATCH or size + len(item[1]) > MAX_CHARS_PER_BATCH):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += len(item[1])
    if batch:
        yield batch


async def _translate_batch(batch: list[tuple[int, str]], lang: str) -> dict[int, str]:
    """One model call. Returns {id: translated}. Missing ids are simply absent."""
    payload = {"items": [{"id": i, "text": t} for i, t in batch]}
    import json
    response = await llm.ask_json(
        SYSTEM_PROMPT.format(language=LANGUAGES[lang]),
        json.dumps(payload, ensure_ascii=False),
        temperature=0.1,
        label="llm_translate",
    )
    out = {}
    for row in response.get("items", []):
        try:
            idx, text = int(row["id"]), str(row["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if text:
            out[idx] = text
    return out


async def translate_texts(texts: list[str], lang: str = "hi") -> list[str]:
    """Translate a list of strings, same order out as in. Never raises."""
    results, _ = await translate_with_status(texts, lang)
    return results


async def translate_with_status(texts: list[str], lang: str = "hi") -> tuple[list[str], bool]:
    """
    Same as translate_texts, plus a flag: True when everything that needed
    translating was translated, False when the model was busy or unreachable
    for some of it (those strings come back in English). The frontend uses
    the flag to offer a retry instead of silently showing English forever.
    """
    if lang not in LANGUAGES or lang == "en":
        return list(texts), True

    results: list[str] = list(texts)
    todo: dict[str, list[int]] = {}      # unique text -> positions in `texts`

    for position, text in enumerate(texts):
        stripped = (text or "").strip()
        if not stripped:
            continue
        cached = await cache.get(_key(lang, stripped))
        if cached is not None:
            results[position] = cached
        else:
            todo.setdefault(stripped, []).append(position)

    if not todo:
        return results, True
    if not config.LLM_ENABLED:
        return results, False

    unique = list(todo.keys())
    numbered = list(enumerate(unique))

    complete = True
    for batch in _batches(numbered):
        try:
            translated = await _translate_batch(batch, lang)
        except Exception as e:                      # rate limit, network, bad JSON
            print(f"[translate] batch skipped ({type(e).__name__}: {e})")
            complete = False
            continue
        for idx, source in batch:
            text = translated.get(idx)
            if not text:
                complete = False
                continue
            await cache.set(_key(lang, source), text, TRANSLATE_TTL)
            for position in todo[source]:
                results[position] = text

    return results, complete
