"""
The live event stream.

While the agent works, the frontend shows what it is doing right now:

    Geocoding Jaipur...
    Searching transport: 3 flights, 4 ground options
    Budget is over by Rs 4,200 - re-planning
    Switched flight to train, saved Rs 6,800
    Fits the budget

We do that with Server-Sent Events (SSE), which is basically a HTTP response
that never closes and keeps dripping lines of text. It is much simpler than
WebSockets and perfect here because the data only flows one way.

Each running trip gets its own asyncio.Queue. The agent pushes events in, the
/api/trips/{id}/stream endpoint pulls them out and forwards them to the browser.
"""

import asyncio
import json
import time

# trip_id -> queue of events waiting to be sent to the browser
# v24: one queue PER LISTENER (fan-out). v23 had one queue per trip, so two
# open tabs (or a refresh while the old connection lingered) split the events
# between them, and whoever took the end marker left the other waiting forever.
_subscribers: dict[str, list[asyncio.Queue]] = {}
_history: dict[str, list[dict]] = {}
_counters: dict[str, int] = {}
_closed: set[str] = set()

END_EVENT = {"seq": -1, "step": "__end__", "message": "", "kind": "done", "data": {}}


def open_stream(trip_id: str) -> None:
    """Start recording a trip's events (called before the agent starts)."""
    _history.setdefault(trip_id, [])
    _subscribers.setdefault(trip_id, [])
    _closed.discard(trip_id)


def subscribe(trip_id: str) -> asyncio.Queue:
    """A private queue for one listener. Already-finished trips get the end marker."""
    queue: asyncio.Queue = asyncio.Queue()
    if trip_id in _closed:
        queue.put_nowait({**END_EVENT, "at": time.time()})
    else:
        _subscribers.setdefault(trip_id, []).append(queue)
    return queue


def unsubscribe(trip_id: str, queue: asyncio.Queue) -> None:
    listeners = _subscribers.get(trip_id) or []
    if queue in listeners:
        listeners.remove(queue)


async def emit(trip_id: str, step: str, message: str, kind: str = "info", data: dict | None = None):
    """
    Push one event to the history and to every listener.

    kind is used by the frontend to colour the milestone marker:
        info     normal step
        tool     an external API was called
        think    the LLM made a decision
        replan   the budget solver (or the critic) changed its mind
        warn     something the user should know
        done     finished
    """
    _counters[trip_id] = _counters.get(trip_id, 0) + 1
    event = {
        "seq": _counters[trip_id],
        "step": step,
        "message": message,
        "kind": kind,
        "data": data or {},
        "at": round(time.time(), 3),
    }
    _history.setdefault(trip_id, []).append(event)
    for queue in list(_subscribers.get(trip_id) or []):
        queue.put_nowait(event)
    print(f"[agent:{trip_id}] {step}: {message}")
    return event


async def close_stream(trip_id: str):
    """Tell every listener we are finished so it can stop listening."""
    _closed.add(trip_id)
    for queue in list(_subscribers.get(trip_id) or []):
        queue.put_nowait({**END_EVENT, "at": time.time()})
    _subscribers.pop(trip_id, None)
    # The trace is in the database now (main._run_agent saves it before
    # closing), so the in-memory copy can go - v23 kept every trip's events
    # in memory until the process died.
    _history.pop(trip_id, None)
    _counters.pop(trip_id, None)


def history(trip_id: str) -> list[dict]:
    return _history.get(trip_id, [])


def cleanup(trip_id: str):
    """Free the memory for a trip (it is saved in the database by then)."""
    _subscribers.pop(trip_id, None)
    _counters.pop(trip_id, None)
    _history.pop(trip_id, None)
    _closed.discard(trip_id)


def format_sse(event: dict) -> str:
    """Wrap an event in the SSE wire format: 'data: {...}\\n\\n'."""
    return f"data: {json.dumps(event)}\n\n"
