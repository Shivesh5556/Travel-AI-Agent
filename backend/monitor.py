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
from datetime import date, datetime

from sqlalchemy import select

import config
from database import SessionLocal
from db_models import Alert, Trip
from tools import weather, webclient

# How often to wake up and check everything. 30 minutes is plenty; the free
# weather API does not want to hear from us more often than that anyway.
CHECK_INTERVAL_SECONDS = 60 * 30

_task: asyncio.Task | None = None


async def check_one_trip(session, trip: Trip) -> list[Alert]:
    """Re-check a single trip and return any new alerts."""
    plan = trip.plan or {}
    destination = plan.get("destination", {})

    if not destination.get("lat"):
        return []

    # Only bother with trips that have not happened yet.
    try:
        start = datetime.strptime(trip.start_date, "%Y-%m-%d").date()
    except ValueError:
        return []

    if start < date.today():
        return []

    fresh = await weather.get_forecast(
        destination["lat"], destination["lon"], trip.start_date, trip.end_date)

    # Only act on a REAL forecast. A seasonal estimate has not "changed",
    # it is the same table we used the first time.
    if not fresh.get("is_real_forecast"):
        return []

    old_rainy = set((plan.get("weather") or {}).get("rainy_days", []))
    new_rainy = set(fresh.get("rainy_days", []))

    newly_rainy = new_rainy - old_rainy
    if not newly_rainy:
        return []

    alerts = []
    for rainy_date in sorted(newly_rainy):
        day = _find_day(plan, rainy_date)
        if not day:
            continue

        indoor = day.get("indoor_share", 0)
        swap = _best_swap(plan, rainy_date)

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

    # Remember the new forecast so we do not raise the same alert every 30 min.
    plan["weather"] = fresh
    trip.plan = dict(plan)          # reassign so SQLAlchemy notices the change

    for alert in alerts:
        session.add(alert)

    return alerts


def _find_day(plan: dict, date_string: str) -> dict | None:
    for day in plan.get("days", []):
        if day.get("date") == date_string:
            return day
    return None


def _best_swap(plan: dict, rainy_date: str) -> dict | None:
    """The most indoor day that is not itself forecast to rain."""
    candidates = [d for d in plan.get("days", [])
                  if d.get("date") != rainy_date and not d.get("is_rainy") and d.get("stops")]
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

        if created:
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
