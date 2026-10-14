"""
Ghumi Ghumi - the API.

Run it with:
    uvicorn main:app --reload

Then open http://127.0.0.1:8000/docs to poke at every endpoint without the
frontend. FastAPI builds that page automatically from the Pydantic models.

The one design decision worth explaining: POST /api/trips does NOT wait for
the plan. It saves the trip, starts the agent in the background and returns
an id immediately. The browser then opens /api/trips/{id}/stream and watches
the agent work live. If we made it wait, the user would stare at a spinner
for fifteen seconds with no idea whether it was working.
"""

import asyncio
import contextlib
import time
from pathlib import Path
from collections import defaultdict, deque

from pydantic import BaseModel, Field
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import cache
import config
import llm
import metrics
import monitor
import payments
import translate
from agent import events
from agent.loop import plan_trip
from database import get_session, init_db
from db_models import Alert, Trip
from schemas import (INTEREST_CHOICES, TravelerDetails, TripDetail,
                     TripRequest, TripSummary)
from tools import geo


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the server starts, and once when it stops."""
    await init_db()
    await _fail_stale_trips()
    await monitor.purge_old_trips()
    monitor.start()

    missing = config.missing_required_keys()
    if missing:
        print("\n" + "=" * 66)
        print("  GHUMI GHUMI IS RUNNING BUT CANNOT PLAN TRIPS YET")
        print("=" * 66)
        for item in missing:
            print(f"  MISSING  {item['key']}")
            print(f"           {item['what']}")
            print(f"           Get one: {item['where']}  ({item['cost']})")
        print("  Put them in backend/.env, then restart this server.")
        print("=" * 66 + "\n")
    else:
        # Ask Groq which models are live and lock one in before any request
        # arrives. Groq retires models often; this stops that breaking a demo.
        await llm.verify_model()
        print("[app] all required keys present. Config:", config.status())
        print("[app] planning with OpenStreetMap + Wikipedia + Wikivoyage + Wikidata + "
             "Geoapify (+ Ticketmaster if set), merged and deduplicated, so every plan "
             "is built from a wide pool of real places before the budget "
             "solver picks what fits.")
        if not config.CONTACT_CONFIGURED:
            print("[app] WARNING: NOMINATIM_CONTACT is not an email address. Wikipedia/Wikivoyage usually "
                  "answer 403 without a contact in the User-Agent, and plans lose two of "
                  "their best sources. Put your email in NOMINATIM_CONTACT in backend/.env.")
        if config.FOURSQUARE_LEGACY_KEY:
            print("[app] Foursquare is OFF: FOURSQUARE_API_KEY is an old 'fsq3' key. Foursquare "
                  "retired that API on 15 May 2026 and the new Places API refuses these keys "
                  "(HTTP 401). Create a Service API key in the Foursquare developer console, "
                  "or leave FOURSQUARE_API_KEY empty - it is optional.")
        elif config.FOURSQUARE_ENABLED:
            print("[app] Foursquare places also active as a bonus cross-check.")
        else:
            print("[app] Foursquare not configured (optional) - add "
                 "FOURSQUARE_API_KEY in .env for one more cross-check source.")

    yield
    monitor.stop()


async def _fail_stale_trips():
    """A trip still 'running' at startup was interrupted by a restart - its
    background task is gone. Mark it failed so the page shows a clear message
    instead of waiting forever."""
    from database import SessionLocal
    async with SessionLocal() as session:
        result = await session.execute(
            update(Trip).where(Trip.status == "running").values(
                status="failed",
                plan={"ok": False, "reason": "The server restarted while this trip was "
                                             "being planned. Please plan it again."}))
        await session.commit()
        if result.rowcount:
            print(f"[app] marked {result.rowcount} interrupted trip(s) as failed")


# ---------------------------------------------------------------------------
# Who is asking (anonymous) and how often
# ---------------------------------------------------------------------------

def client_id(x_client_id: str | None = Header(default=None)) -> str | None:
    """The browser's anonymous id (a random UUID kept in localStorage)."""
    if x_client_id and 8 <= len(x_client_id) <= 64 and x_client_id.replace("-", "").isalnum():
        return x_client_id
    return None


_recent_plans: dict[str, deque] = defaultdict(deque)


def _rate_limit(key: str) -> None:
    """Sliding one-hour window, in memory. Enough for a single-server deploy."""
    now = time.monotonic()
    window = _recent_plans[key]
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= config.MAX_TRIPS_PER_HOUR:
        retry = int(3600 - (now - window[0])) + 1
        raise HTTPException(429, f"Too many trips planned from here in the last hour. "
                                 f"Try again in about {max(1, retry // 60)} minutes.")
    window.append(now)


def _require_admin(x_admin_token: str | None) -> None:
    if config.ADMIN_TOKEN and x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(403, "Admin token required")


app = FastAPI(
    title="Ghumi Ghumi",
    description="An agentic travel planner for India. FastAPI + React.",
    version="2.0.0",
    lifespan=lifespan,
)

# The React dev server runs on a different port, so the browser needs
# permission to call us. That is all CORS is.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["meta"])
async def health():
    """What is switched on. The frontend shows this in the footer."""
    status = config.status()
    status["model"] = llm.ACTIVE_MODEL if config.LLM_ENABLED else None
    return {"status": "ok", "config": status}


@app.get("/api/meta", tags=["meta"])
async def meta():
    status = config.status()
    status["model"] = llm.ACTIVE_MODEL if config.LLM_ENABLED else None
    return {
        "interests": INTEREST_CHOICES,
        "config": status,
        "note": ("Hotel prices are estimates. Flight fares are real only if a "
                 "Duffel key is configured. Everything else comes from live "
                 "free APIs."),
    }


@app.get("/api/metrics", tags=["meta"])
async def get_metrics(x_admin_token: str | None = Header(default=None)):
    """How slow and how expensive this server has been since it started."""
    _require_admin(x_admin_token)
    data = metrics.summary()
    data["cache_hit_rate_percent"] = cache.hit_rate()
    data["cache"] = cache.STATS
    return data


class TranslateRequest(BaseModel):
    lang: str = "hi"
    texts: list[str] = Field(default_factory=list, max_length=300)


@app.post("/api/translate", tags=["meta"])
async def translate_texts(body: TranslateRequest):
    """
    Translate plan text (day descriptions, tips, warnings...) into another
    language. Returns the strings in the same order. Place names, numbers and
    rupee amounts are left alone. If the model is busy the English text comes
    back unchanged, so the page never breaks.
    """
    if body.lang not in translate.LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language '{body.lang}'")
    texts = [t[:1500] for t in body.texts]
    translations, complete = await translate.translate_with_status(texts, body.lang)
    return {"lang": body.lang, "translations": translations, "complete": complete}


@app.get("/api/geo/search", tags=["meta"])
async def geo_search(q: str = ""):
    """
    Autocomplete for a city name.

    Returns several candidates rather than one guess, so the frontend can
    show a picker exactly when it matters: when two real places share a
    name (Goa in India vs Genoa in Italy, or India's several towns called
    Bilaspur). Used by the "Leaving from" / "Going to" fields.
    """
    results = await geo.search_candidates(q)
    return {"query": q, "results": results}


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------

def _to_summary(trip: Trip) -> dict:
    return {
        "id": trip.id,
        "source_city": trip.source_city,
        "destination_city": trip.destination_city,
        "start_date": trip.start_date,
        "end_date": trip.end_date,
        "travelers": trip.travelers,
        "budget_inr": trip.budget_inr,
        "status": trip.status,
        "fits_budget": trip.fits_budget,
        "total_cost_inr": trip.total_cost_inr,
        "created_at": trip.created_at.isoformat() if trip.created_at else "",
    }


@app.post("/api/trips", response_model=TripSummary, status_code=202, tags=["trips"])
async def create_trip(req: TripRequest, background: BackgroundTasks, request: Request,
                      session: AsyncSession = Depends(get_session),
                      owner: str | None = Depends(client_id)):
    """
    Start planning a trip.

    Returns straight away with status "running". Watch /api/trips/{id}/stream
    to see it happen, then GET /api/trips/{id} for the finished plan.
    """
    # Refuse rather than quietly plan with guessed data.
    missing = config.missing_required_keys()
    if missing:
        raise HTTPException(503, {
            "error": "setup_incomplete",
            "message": "Ghumi Ghumi needs API keys before it can plan a real trip.",
            "missing": missing,
        })

    _rate_limit(owner or (request.client.host if request.client else "anon"))

    trip = Trip(
        owner_id=owner,
        source_city=req.source_city,
        destination_city=req.destination_city,
        start_date=req.start_date.isoformat(),
        end_date=req.end_date.isoformat(),
        travelers=req.travelers,
        budget_inr=req.budget_inr,
        interests=req.interests,
        travel_style=req.travel_style,
        notes=req.notes,
        status="running",
    )
    session.add(trip)
    await session.commit()
    await session.refresh(trip)

    # Open the event stream BEFORE the background task starts, otherwise the
    # first few events can be emitted before anyone is listening.
    events.open_stream(trip.id)
    background.add_task(_run_agent, trip.id, req)

    return _to_summary(trip)


async def _run_agent(trip_id: str, req: TripRequest):
    """Runs in the background. Saves whatever the agent produces."""
    try:
        plan = await plan_trip(trip_id, req)
        status = "done" if plan.get("ok") else "failed"
    except Exception as e:
        print(f"[app] planning {trip_id} blew up: {type(e).__name__}: {e}")
        await events.emit(trip_id, "error", f"Planning failed: {e}", kind="warn")
        plan = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
        status = "failed"

    cost = metrics.cost_for_trip(trip_id)

    try:
        async with get_session_direct() as session:
            trip = await session.get(Trip, trip_id)
            if trip is None:
                return
            trip.plan = plan
            trip.trace = events.history(trip_id)
            trip.status = status
            trip.fits_budget = bool(plan.get("fits"))
            trip.total_cost_inr = int((plan.get("budget") or {}).get("total_inr", 0))
            trip.replan_rounds = len([a for a in plan.get("attempts", []) if not a.get("upgrade")])
            trip.plan_cost_inr = cost["cost_inr"]
            trip.plan_ms = (plan.get("meta") or {}).get("ms", 0.0)
            await session.commit()
    finally:
        # Only now is it safe to tell the frontend the stream is done - that
        # is its cue to fetch the trip, and the plan above must already be
        # committed by the time it does. The finally makes sure the stream
        # still closes (rather than hanging the browser) even if the save
        # itself failed.
        await events.close_stream(trip_id)


def get_session_direct():
    """Background tasks cannot use Depends(), so they open a session by hand."""
    from database import SessionLocal
    return SessionLocal()


# There is deliberately no "list all trips" endpoint: trips are view-once and
# belong to nobody's history. The browser deletes its trip when it is done
# with it (DELETE below) and monitor.purge_old_trips clears anything left over.


@app.get("/api/trips/{trip_id}", response_model=TripDetail, tags=["trips"])
async def get_trip(trip_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Trip).where(Trip.id == trip_id).options(selectinload(Trip.alerts)))
    trip = result.scalar_one_or_none()

    if trip is None:
        raise HTTPException(404, "No trip with that id")

    data = _to_summary(trip)
    data.update({
        "interests": trip.interests or [],
        "travel_style": trip.travel_style or "balanced",
        "notes": trip.notes or "",
        "plan": trip.plan or {},
        "trace": trip.trace or [],
        "replan_rounds": trip.replan_rounds,
        "plan_cost_inr": trip.plan_cost_inr,
        "plan_ms": trip.plan_ms,
        "alerts": [{
            "id": a.id, "kind": a.kind, "severity": a.severity,
            "message": a.message, "suggestion": a.suggestion,
            "created_at": a.created_at.isoformat() if a.created_at else "",
        } for a in trip.alerts],
    })
    return data


@app.delete("/api/trips/{trip_id}", tags=["trips"])
async def delete_trip(trip_id: str, session: AsyncSession = Depends(get_session),
                      owner: str | None = Depends(client_id)):
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(404, "No trip with that id")
    if trip.owner_id and trip.owner_id != owner:
        raise HTTPException(403, "Only the person who planned this trip can delete it")
    await session.delete(trip)
    await session.commit()
    events.cleanup(trip_id)
    return {"deleted": trip_id}


@app.get("/api/trips/{trip_id}/stream", tags=["trips"])
async def stream_trip(trip_id: str, session: AsyncSession = Depends(get_session)):
    """
    Server-Sent Events: the live agent trace.

    In the browser this is just:
        const source = new EventSource("/api/trips/abc123/stream");
        source.onmessage = (e) => console.log(JSON.parse(e.data));

    v24: a trip that is already finished (a share link, "My trips", a page
    refresh, or anything after a server restart) gets its saved trace plus the
    end marker straight away. v23 opened a fresh, empty queue and waited for
    an end marker that had already been consumed - the page spun forever.
    """
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(404, "No trip with that id")
    if trip.status != "running":
        saved = list(trip.trace or [])

        async def finished():
            for past in saved:
                yield events.format_sse(past)
            yield events.format_sse({"seq": -1, "step": "__end__", "message": "",
                                     "kind": "done", "data": {}, "at": time.time()})

        return StreamingResponse(finished(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    queue = events.subscribe(trip_id)

    async def generate():
        # Replay anything that already happened, so a refresh does not lose
        # the earlier steps.
        for past in events.history(trip_id):
            yield events.format_sse(past)

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                # A comment line keeps the connection alive through proxies.
                yield ": keep-alive\n\n"
                continue

            if event.get("step") == "__end__":
                yield events.format_sse(event)
                break

            yield events.format_sse(event)

    async def generate_and_unsubscribe():
        try:
            async for chunk in generate():
                yield chunk
        finally:
            events.unsubscribe(trip_id, queue)

    return StreamingResponse(
        generate_and_unsubscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

@app.post("/api/monitor/run", tags=["monitor"])
async def run_monitor_now(trip_id: str | None = None,
                          x_admin_token: str | None = Header(default=None)):
    """
    Force the background monitor to run right now - for one trip (what the
    "Re-check this trip" button does) or, with the admin token when one is
    configured, for every trip.

    It normally runs every 30 minutes, which is useless in a live demo. Press
    this button instead of waiting.
    """
    if not trip_id:
        _require_admin(x_admin_token)
    # Also un-blacklist any map server we gave up on. Pressing this button
    # after a failed plan is the natural "try again properly" action.
    from tools import webclient
    webclient.reset_mirrors()

    created = await monitor.run_once(trip_id)
    return {"alerts_created": created, "map_servers_reset": True}


# ---------------------------------------------------------------------------
# Checkout (Razorpay TEST MODE - no real money, ever)
# ---------------------------------------------------------------------------

@app.post("/api/trips/{trip_id}/checkout", tags=["checkout"])
async def start_checkout(trip_id: str, traveler: TravelerDetails,
                         session: AsyncSession = Depends(get_session)):
    """Create a Razorpay test order for the planned trip."""
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(404, "No trip with that id")
    if trip.status != "done":
        raise HTTPException(400, "This trip has not finished planning yet")
    if not config.PAYMENTS_ENABLED:
        raise HTTPException(400, "Checkout is off. Add RAZORPAY_KEY_ID and "
                                 "RAZORPAY_KEY_SECRET to backend/.env to switch it on.")

    amount = trip.total_cost_inr or (trip.plan.get("budget") or {}).get("total_inr", 0)
    if amount <= 0:
        raise HTTPException(400, "This trip has no cost to pay")

    try:
        order = await payments.create_order(amount_inr=amount, receipt=trip_id)
    except payments.PaymentsNotConfigured as e:
        raise HTTPException(400, str(e))

    plan = dict(trip.plan or {})
    plan["checkout"] = {
        "order_id": order["id"],
        "amount_inr": amount,
        "traveler": traveler.model_dump(),
    }
    trip.plan = plan
    await session.commit()

    return {
        "order_id": order["id"],
        "amount_paise": order["amount"],
        "amount_inr": amount,
        "key_id": config.RAZORPAY_KEY_ID,
        "test_mode": True,
    }


@app.post("/api/trips/{trip_id}/verify", tags=["checkout"])
async def verify_payment(trip_id: str, body: dict,
                         session: AsyncSession = Depends(get_session)):
    """
    Razorpay's widget calls back with three values. We check the signature
    ourselves before believing any of it.
    """
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(404, "No trip with that id")

    plan = dict(trip.plan or {})
    checkout = plan.get("checkout") or {}

    order_id = body.get("razorpay_order_id")
    payment_id = body.get("razorpay_payment_id")
    signature = body.get("razorpay_signature")

    if order_id != checkout.get("order_id"):
        raise HTTPException(400, "That order id does not belong to this trip")

    if not payments.verify_signature(order_id, payment_id, signature):
        raise HTTPException(400, "Signature check failed - this payment is not genuine")

    plan["booking"] = {
        "pnr": payments.make_pnr(trip_id, payment_id),   # a demo reference, NOT a railway PNR
        "payment_id": payment_id,
        "paid_inr": checkout.get("amount_inr", 0),
        "traveler": checkout.get("traveler", {}),
        "test_mode": True,
        "disclaimer": ("DEMO ONLY: created with Razorpay test keys. No money moved, and no "
                       "train, flight or hotel has been reserved - book those with the "
                       "providers directly. This reference is not a PNR."),
    }
    trip.plan = plan
    await session.commit()

    return plan["booking"]


# ---------------------------------------------------------------------------
# The website itself (production / Render)
#
# In development Vite serves the React app on :5173 and proxies /api here.
# In production `npm run build` writes frontend/dist, and this one FastAPI
# process serves BOTH the API and the site - one free Render service, same
# origin, no CORS. Declared last so every /api route above wins.
# ---------------------------------------------------------------------------

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIST.is_dir():
    if (FRONTEND_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def website(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "No such API route")
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and FRONTEND_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")

