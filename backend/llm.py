"""
Our wrapper around the Groq API.

We call Groq's HTTP endpoint directly with httpx instead of using their SDK.
It is the same OpenAI-style /chat/completions endpoint, and doing it by hand
means you can actually SEE what a tool-calling request looks like, which is
the whole point of the project.

Three functions:
    ask_text()  -> plain writing (the itinerary prose)
    ask_json()  -> structured data, with one retry if the JSON is broken
    ask_tools() -> the important one: give the LLM a list of tools and let it
                   decide which one to call next. This is what makes it an
                   agent instead of a pipeline.
"""

import json
import re

import httpx

import config
import metrics

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_ENDPOINT = "https://api.groq.com/openai/v1/models"

# The model we are actually using. Set by verify_model() on startup, which is
# why nothing below reads config.GROQ_MODEL directly.
ACTIVE_MODEL = config.GROQ_MODEL

# How many times to wait-and-retry when Groq says we are going too fast.
MAX_RATE_LIMIT_RETRIES = 4

# How many times to wait-and-retry when Groq says we are going too fast.
MAX_RATE_LIMIT_RETRIES = 4


# If Groq cannot be reached at all (no network, a firewall, DNS), retrying
# every call 4 times with back-off wasted ~45 s of a 57 s plan in testing.
# After one call fails like that, later calls fail fast for a minute and the
# planner uses its non-LLM fallbacks straight away.
UNREACHABLE_PAUSE_SECONDS = 60
_unreachable_until = 0.0


def _connect_failure(e: Exception) -> bool:
    return isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError,
                          httpx.UnsupportedProtocol))


class LLMUnavailable(Exception):
    """Raised when there is no API key. Callers catch this and use Python rules."""
    pass


async def list_available_models() -> list[str]:
    """Ask Groq which models this key can actually use."""
    if not config.LLM_ENABLED:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                GROQ_MODELS_ENDPOINT,
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        print(f"[llm] could not list models ({type(e).__name__}), will use {config.GROQ_MODEL} as-is")
        return []

    return [m["id"] for m in data.get("data", [])]


async def verify_model() -> str:
    """
    Called once on startup.

    Groq retires models regularly - llama-3.3-70b-versatile was deprecated in
    June 2026 and now 404s. Rather than let that break the agent at the worst
    possible moment (during a demo), we check what is live and pick the best
    available option from config.MODEL_PREFERENCES.
    """
    global ACTIVE_MODEL

    available = await list_available_models()
    if not available:
        return ACTIVE_MODEL

    if config.GROQ_MODEL in available:
        ACTIVE_MODEL = config.GROQ_MODEL
        print(f"[llm] using {ACTIVE_MODEL}")
        return ACTIVE_MODEL

    for candidate in config.MODEL_PREFERENCES:
        if candidate in available:
            ACTIVE_MODEL = candidate
            print(f"[llm] '{config.GROQ_MODEL}' is not available on this key. "
                  f"Falling back to '{ACTIVE_MODEL}'.")
            print(f"[llm] set GROQ_MODEL={ACTIVE_MODEL} in .env to make this permanent.")
            return ACTIVE_MODEL

    ACTIVE_MODEL = available[0]
    print(f"[llm] none of the preferred models are available. Using '{ACTIVE_MODEL}'.")
    return ACTIVE_MODEL


def _headers():
    if not config.LLM_ENABLED:
        raise LLMUnavailable("GROQ_API_KEY is not set in .env")
    return {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }


async def _post(payload: dict, label: str, trip_id: str | None) -> dict:
    """
    One HTTP call to Groq, with rate-limit handling.

    Groq's free tier allows 8,000 tokens per minute. A single trip plan can
    use more than that in a burst, and the answer is a 429 with a message
    like "Please try again in 1.4025s". That is not an error, it is Groq
    telling us exactly how long to wait - so we wait and try again instead of
    giving up and falling back to Python rules halfway through a plan.
    """
    import asyncio
    import time

    global _unreachable_until
    if time.monotonic() < _unreachable_until:
        raise LLMUnavailable("Groq was unreachable a moment ago - skipping the call")

    last_error = ""
    connect_failures = 0

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        start = time.perf_counter()

        try:
            async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
                response = await client.post(GROQ_ENDPOINT, headers=_headers(), json=payload)
        except httpx.HTTPError as e:
            # A dropped connection is not a reason to abandon the trip. This
            # is what killed a Pune-Goa plan outright with a bare "ReadError".
            # But a connection that cannot even be OPENED is retried once only.
            last_error = f"{type(e).__name__}: {e}"
            if _connect_failure(e):
                connect_failures += 1
            if attempt < MAX_RATE_LIMIT_RETRIES and connect_failures < 2:
                print(f"[llm] network problem ({type(e).__name__}), retrying")
                await asyncio.sleep(1.0 if connect_failures else 1.5 * (attempt + 1))
                continue
            if connect_failures:
                _unreachable_until = time.monotonic() + UNREACHABLE_PAUSE_SECONDS
                print(f"[llm] cannot reach Groq ({type(e).__name__}) - LLM steps will use "
                      f"fallbacks for the next {UNREACHABLE_PAUSE_SECONDS}s")
            raise LLMUnavailable(f"Could not reach Groq: {last_error}")

        ms = round((time.perf_counter() - start) * 1000, 1)

        if response.status_code == 429:
            wait = _retry_after_seconds(response)
            last_error = response.text[:200]

            if attempt < MAX_RATE_LIMIT_RETRIES:
                print(f"[llm] rate limited, waiting {wait:.1f}s "
                      f"(attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})")
                await asyncio.sleep(wait)
                continue

            raise LLMUnavailable(
                f"Groq rate limit hit {MAX_RATE_LIMIT_RETRIES} times in a row. "
                f"The free tier allows 8,000 tokens per minute. Wait a minute and "
                f"try again, or switch GROQ_MODEL to a smaller model like "
                f"openai/gpt-oss-20b in .env.")

        if response.status_code != 200:
            body = response.text[:300]
            if "model_not_found" in body or "model_decommissioned" in body:
                raise LLMUnavailable(
                    f"The model '{ACTIVE_MODEL}' is not available on your Groq key. "
                    f"Groq retires models regularly. Open https://console.groq.com/docs/models, "
                    f"pick a current one, and set GROQ_MODEL=<that name> in backend/.env.")
            raise LLMUnavailable(f"Groq returned {response.status_code}: {body}")

        data = response.json()
        usage = data.get("usage", {})
        metrics.record(
            label,
            kind="llm",
            ms=ms,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            trip_id=trip_id,
        )
        return data

    raise LLMUnavailable(f"Groq call failed: {last_error}")


def _retry_after_seconds(response: httpx.Response) -> float:
    """
    Work out how long to wait after a 429.

    Groq sends a Retry-After header, and also puts the exact wait inside the
    error message ("Please try again in 1.4025s"). We prefer the header, fall
    back to parsing the message, and add a small cushion so we do not come
    back a fraction of a second too early.
    """
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header) + 0.5
        except ValueError:
            pass

    match = re.search(r"try again in ([0-9.]+)s", response.text)
    if match:
        return float(match.group(1)) + 0.5

    return 5.0


async def ask_text(system_prompt: str, user_prompt: str, temperature: float = 0.6,
                   label: str = "llm_text", trip_id: str | None = None) -> str:
    """Normal text answer. Used for the itinerary and the critic's notes."""
    data = await _post({
        "model": ACTIVE_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }, label, trip_id)
    return data["choices"][0]["message"]["content"].strip()


def _strip_fences(text: str) -> str:
    """LLMs love wrapping JSON in ```json fences even when told not to."""
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


async def ask_json(system_prompt: str, user_prompt: str, temperature: float = 0.3,
                   label: str = "llm_json", trip_id: str | None = None) -> dict:
    """
    Structured answer. We ask for JSON mode, but we still check the result and
    retry once, because an agent that crashes on one bad character is useless.
    """
    system = system_prompt + "\n\nRespond with ONLY valid JSON. No markdown, no explanation."

    for attempt in range(2):
        data = await _post({
            "model": ACTIVE_MODEL,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
        }, label, trip_id)

        raw = data["choices"][0]["message"]["content"]
        try:
            return json.loads(_strip_fences(raw))
        except json.JSONDecodeError:
            if attempt == 0:
                user_prompt += "\n\nYour last reply was not valid JSON. Send strict JSON only."
                continue
            raise LLMUnavailable(f"LLM would not return valid JSON: {raw[:200]}")

    return {}


async def ask_tools(messages: list[dict], tools: list[dict], temperature: float = 0.2,
                    label: str = "llm_agent_step", trip_id: str | None = None) -> dict:
    """
    The agent step.

    We hand the model the conversation so far PLUS a list of tools it is
    allowed to use. The model replies with either:
      - some text (it thinks it is finished), or
      - one or more tool calls (it wants us to go fetch something)

    We return the raw message so agent/loop.py can decide what to do.
    """
    data = await _post({
        "model": ACTIVE_MODEL,
        "temperature": temperature,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }, label, trip_id)

    return data["choices"][0]["message"]
