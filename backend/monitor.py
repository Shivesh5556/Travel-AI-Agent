"""
The agent that keeps working after you close the tab.

Every trip that is already planned gets re-checked on a timer. If the weather
forecast has changed since we planned it, we write an Alert row, and the next
time you open your trip the app tells you:

    "Day 3 is now forecast to rain. Your Day 1 is mostly museums.
     Swap them and you keep your plan."

MakeMyTrip sends you a booking confirmation and then forgets you exist. This
is the thing an actual agent does that a search box cannot.

We run it as a plain asyncio background task rather than Celery + Redis
because for a project this size that is one dependency instead of three.
The upgrade path is written at the bottom of this file.
"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import config
from database import SessionLocal
from db_models import Alert, Trip
from tools import weather, webclient

# How often to wake up and check everything. 30 minutes is plenty; the free
# weather API does not want to hear from us more often than that anyway.
CHECK_INTERVAL_SECONDS = 60 * 30
IST = timezone(timedelta(hours=5, minutes=30))

_task: asyncio.Task | None = None


async def check_one_trip(session, trip: Trip) -> list[Alert]:
    """Re-check a single trip and return any new alerts."""
    # Mutating a nested JSON value before reassigning can make SQLAlchemy
    # compare two already-mutated equal values and silently miss the update.
    plan = deepcopy(trip.plan or {})
    destination = plan.get("destination", {})

    if destination.get("lat") is None or destination.get("lon") is None:
        return []

    # Only bother with trips that have not happened yet.
    try:
        start = datetime.strptime(trip.start_date, "%Y-%m-%d").date()
        end = datetime.strptime(trip.end_date, "%Y-%m-%d").date()
    except ValueError:
        return []

    today = datetime.now(IST).date()
    if end < today:
        return []

    fresh = await weather.get_forecast(
        destination["lat"], destination["lon"], max(start, today).isoformat(), trip.end_date)

    # Only act on a REAL forecast. A seasonal estimate has not "changed",
    # it is the same table we used the first time.
    if not (fresh.get("is_real_forecast") or fresh.get("has_forecast")):
        return []

    old_rainy = set((plan.get("weather") or {}).get("rainy_days", []))
    real_dates = ({d["date"] for d in fresh.get("days", [])}
                  if fresh.get("is_real_forecast") else set(fresh.get("forecast_dates", [])))
    new_rainy = set(fresh.get("rainy_days", [])) & real_dates

    # Persist clear transitions as well as rain, and use this forecast when
    # choosing a swap (the original day's is_rainy flag may now be stale).
    fresh_days = {d["date"]: d for d in fresh.get("days", [])}
    for day in plan.get("days", []):
        current = fresh_days.get(day.get("date"))
        if current:
            day["is_rainy"] = bool(current.get("is_rainy"))
            day["rain_chance"] = current.get("rain_chance")
    plan["weather"] = fresh
    trip.plan = plan

    newly_rainy = new_rainy - old_rainy
    if not newly_rainy:
        return []

    alerts = []
    for rainy_date in sorted(newly_rainy):
        day = _find_day(plan, rainy_date)
        if not day:
            continue

        indoor = day.get("indoor_share", 0)
        swap = _best_swap(plan, rainy_date, real_dates)

        message = (f"Day {day['day_number']} ({rainy_date}) is now forecast to rain. "
                   f"It was not when we planned this.")

        if indoor >= 60:
            suggestion = "That day is already mostly indoors, so you are fine as it is."
            severity = "info"
        elif swap:
            suggestion = (f"Day {swap['day_number']} ({swap['date']}) is "
                          f"{swap['indoor_share']}% indoors. Swapping the two days keeps "
                          f"your whole plan and gets you out of the rain.")
            severity = "warning"
        else:
            suggestion = "Keep that day flexible and do the indoor stops first."
            severity = "warning"

        alerts.append(Alert(
            trip_id=trip.id,
            kind="weather",
            severity=severity,
            message=message,
            suggestion=suggestion,
        ))

    for alert in alerts:
        session.add(alert)

    return alerts


def _find_day(plan: dict, date_string: str) -> dict | None:
    for day in plan.get("days", []):
        if day.get("date") == date_string:
            return day
    return None


def _best_swap(plan: dict, rainy_date: str, real_dates: set[str] | None = None) -> dict | None:
    """Suggest only a dry, date-compatible swap that fits both day windows."""
    from dayplanner import _closed_on, day_elapsed_hours, order_for_the_day
    wet = _find_day(plan, rainy_date)
    if not wet or wet.get("travel_day") or wet.get("travel_note"):
        return None

    def fits(source, target):
        hours = source.get("elapsed_hours", source.get("hours", 0))
        base = plan.get("stay") or plan.get("destination") or {}
        stops = source.get("stops", [])
        if (base.get("lat") is not None and base.get("lon") is not None and
                all(s.get("lat") is not None and s.get("lon") is not None for s in stops)):
            hours = day_elapsed_hours(order_for_the_day(stops, base["lat"], base["lon"]),
                base["lat"], base["lon"], target.get("start_hour", 9), bool(source.get("lunch")))
        if hours > target.get("available_hours", 7):
            return False
        for stop in source.get("stops", []):
            if _closed_on(stop, target["date"]):
                return False
            fixed = stop.get("only_on") or stop.get("event_date") or stop.get("date")
            if fixed and str(fixed)[:10] != target["date"]:
                return False
        return True

    candidates = [d for d in plan.get("days", [])
                  if d.get("date") != rainy_date and not d.get("is_rainy") and d.get("stops")
                  and (real_dates is None or d.get("date") in real_dates)
                  and not d.get("travel_day") and not d.get("travel_note")
                  and fits(wet, d) and fits(d, wet)]
    if not candidates:
        return None
    best = max(candidates, key=lambda d: d.get("indoor_share", 0))
    return best if best.get("indoor_share", 0) > 30 else None


async def run_once(trip_id: str | None = None) -> int:
    """One pass over every finished trip (or just one). Returns how many alerts were created."""
    created = 0
    async with SessionLocal() as session:
        query = select(Trip).where(Trip.status == "done")
        if trip_id:
            query = query.where(Trip.id == trip_id)
        result = await session.execute(query)
        trips = result.scalars().all()

        for trip in trips:
            try:
                alerts = await check_one_trip(session, trip)
                created += len(alerts)
            except Exception as e:
                # One bad trip must not kill the whole background loop.
                print(f"[monitor] trip {trip.id} check failed: {type(e).__name__}: {e}")

        # Successful checks can change the weather baseline without producing
        # an alert (for example rain clearing). Those updates must survive.
        await session.commit()

    if created:
        print(f"[monitor] created {created} new alert(s)")
    return created


async def purge_old_trips(max_age_hours: float | None = None) -> int:
    """
    Trips are view-once: the browser deletes its trip when you leave it. This
    is the safety net for tabs that never said goodbye (crashed, offline):
    anything older than TRIP_TTL_HOURS is removed. Returns how many.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete
    hours = config.TRIP_TTL_HOURS if max_age_hours is None else max_age_hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with SessionLocal() as session:
        old_ids = (await session.execute(
            select(Trip.id).where(Trip.created_at < cutoff, Trip.status != "running"))).scalars().all()
        if old_ids:
            await session.execute(delete(Alert).where(Alert.trip_id.in_(old_ids)))
            await session.execute(delete(Trip).where(Trip.id.in_(old_ids)))
            await session.commit()
    if old_ids:
        from agent.events import cleanup
        for trip_id in old_ids:
            cleanup(trip_id)
        print(f"[monitor] removed {len(old_ids)} trip(s) older than {hours}h")
    return len(old_ids)


async def _loop():
    # Wait a bit on startup so we do not fight with the first page load.
    await asyncio.sleep(30)
    while True:
        try:
            await purge_old_trips()
            await run_once()

            # Give any Overpass mirror we marked as down another chance.
            # Those servers are usually just busy for a few minutes, and
            # without this a single bad moment would leave the app on
            # fallback data until you restarted it.
            webclient.reset_mirrors()
        except Exception as e:
            print(f"[monitor] pass failed: {type(e).__name__}: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def start():
    """Called from main.py on startup."""
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())
        print(f"[monitor] background agent started, checking every "
              f"{CHECK_INTERVAL_SECONDS // 60} minutes")


def stop():
    global _task
    if _task is not None:
        _task.cancel()
        _task = None


# If you ever deploy this properly, replace the asyncio task with Celery Beat
# so the schedule survives a restart and can run on a separate machine:
#
#   celery -A worker beat        (the scheduler)
#   celery -A worker worker      (the thing doing the work)
#
# run_once() above is already the task body - it would not need to change.
