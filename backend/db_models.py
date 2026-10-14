"""
The database tables.

Two tables:
  trips   - one row per trip somebody planned
  alerts  - things the background monitor agent noticed later

The big plan JSON (itinerary, hotels, flights) is stored in a JSON column
instead of 10 separate tables. For a project this size that is the sane
choice, and SQLite and Postgres both support JSON fine.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def new_id() -> str:
    """Short random id like 'a3f9c1d2e5'. Nicer in a URL than a full UUID."""
    return uuid.uuid4().hex[:10]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)

    # what the user asked for
    source_city: Mapped[str] = mapped_column(String(120))
    destination_city: Mapped[str] = mapped_column(String(120))
    start_date: Mapped[str] = mapped_column(String(10))     # "2026-09-14"
    end_date: Mapped[str] = mapped_column(String(10))
    travelers: Mapped[int] = mapped_column(Integer, default=1)
    budget_inr: Mapped[int] = mapped_column(Integer)
    interests: Mapped[list] = mapped_column(JSON, default=list)
    travel_style: Mapped[str] = mapped_column(String(20), default="balanced")
    notes: Mapped[str] = mapped_column(Text, default="")     # free text like "no early flights"

    # what the agent produced
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed
    # Anonymous browser id (X-Client-Id header). "My trips" only lists your
    # own trips and only you can delete them; a share link still opens.
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, default=None)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    trace: Mapped[list] = mapped_column(JSON, default=list)  # every agent step, for the UI + viva
    replan_rounds: Mapped[int] = mapped_column(Integer, default=0)
    fits_budget: Mapped[bool] = mapped_column(Boolean, default=False)
    total_cost_inr: Mapped[int] = mapped_column(Integer, default=0)

    # how expensive was it for US to plan this
    plan_cost_inr: Mapped[float] = mapped_column(Float, default=0.0)
    plan_ms: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    alerts: Mapped[list["Alert"]] = relationship(back_populates="trip", cascade="all, delete-orphan")


class Alert(Base):
    """
    Written by monitor.py, the agent that keeps running after you close the
    tab. Example: rain now forecast for Day 3, here is a swapped plan.
    """
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    kind: Mapped[str] = mapped_column(String(40))       # "weather" / "budget"
    severity: Mapped[str] = mapped_column(String(20), default="info")  # info / warning
    message: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    trip: Mapped[Trip] = relationship(back_populates="alerts")
