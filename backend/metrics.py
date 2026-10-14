"""
Keeps track of how slow and how expensive each trip plan was.

This is the part most student projects skip, and it is the part that proves
you thought about running this thing for real. Every LLM call and every tool
call records its time and its token usage here, and /api/metrics shows it.
"""

import time
from collections import defaultdict

# Groq's pricing for llama-3.3-70b, in US dollars per million tokens.
# (Check console.groq.com/pricing - these change. Rough numbers are fine,
# the point is to show cost awareness, not to do accounting.)
USD_PER_MILLION_INPUT = 0.59
USD_PER_MILLION_OUTPUT = 0.79
USD_TO_INR = 88.0

# Everything we have recorded since the server started.
_records: list[dict] = []


class Timer:
    """
    Use it like this:

        with Timer("search_flights") as t:
            do_the_work()
        print(t.ms)
    """

    def __init__(self, label: str, kind: str = "tool"):
        self.label = label
        self.kind = kind
        self.ms = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self._start) * 1000, 1)
        record(self.label, kind=self.kind, ms=self.ms)
        return False


def record(label: str, kind: str = "tool", ms: float = 0.0,
           input_tokens: int = 0, output_tokens: int = 0, trip_id: str | None = None):
    """Save one measurement."""
    _records.append({
        "label": label,
        "kind": kind,          # "tool" or "llm"
        "ms": ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_inr": token_cost_inr(input_tokens, output_tokens),
        "trip_id": trip_id,
        "at": time.time(),
    })


def token_cost_inr(input_tokens: int, output_tokens: int) -> float:
    """Convert a token count into rupees so it means something to us."""
    usd = (input_tokens / 1_000_000) * USD_PER_MILLION_INPUT
    usd += (output_tokens / 1_000_000) * USD_PER_MILLION_OUTPUT
    return round(usd * USD_TO_INR, 4)


def summary() -> dict:
    """Everything the /api/metrics endpoint needs."""
    if not _records:
        return {"total_calls": 0, "by_label": [], "total_cost_inr": 0.0,
                "total_tokens": 0, "llm_calls": 0, "tool_calls": 0}

    grouped = defaultdict(lambda: {"calls": 0, "total_ms": 0.0, "cost_inr": 0.0, "tokens": 0})
    for r in _records:
        g = grouped[r["label"]]
        g["calls"] += 1
        g["total_ms"] += r["ms"]
        g["cost_inr"] += r["cost_inr"]
        g["tokens"] += r["input_tokens"] + r["output_tokens"]

    by_label = []
    for label, g in grouped.items():
        by_label.append({
            "label": label,
            "calls": g["calls"],
            "avg_ms": round(g["total_ms"] / g["calls"], 1),
            "cost_inr": round(g["cost_inr"], 4),
            "tokens": g["tokens"],
        })
    by_label.sort(key=lambda x: x["avg_ms"], reverse=True)

    return {
        "total_calls": len(_records),
        "llm_calls": sum(1 for r in _records if r["kind"] == "llm"),
        "tool_calls": sum(1 for r in _records if r["kind"] == "tool"),
        "total_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in _records),
        "total_cost_inr": round(sum(r["cost_inr"] for r in _records), 4),
        "by_label": by_label,
    }


def cost_for_trip(trip_id: str) -> dict:
    """How much did this one trip cost us to plan?"""
    mine = [r for r in _records if r["trip_id"] == trip_id]
    return {
        "steps": len(mine),
        "tokens": sum(r["input_tokens"] + r["output_tokens"] for r in mine),
        "cost_inr": round(sum(r["cost_inr"] for r in mine), 4),
        "total_ms": round(sum(r["ms"] for r in mine), 1),
    }
