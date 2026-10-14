"""
Database setup with SQLAlchemy (async).

The old version of this project stored trips in a Python dictionary, so every
time you restarted uvicorn all the plans disappeared. Now they go in a real
database. SQLite by default (zero setup), Postgres if you want it - just
change DATABASE_URL in .env, no code changes needed.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

import config


class Base(DeclarativeBase):
    """Every table below inherits from this."""
    pass


engine = create_async_engine(
    config.DATABASE_URL,
    echo=False,          # set True if you want to see every SQL query printed
    future=True,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Create the tables if they do not exist yet. Called once on startup."""
    import db_models  # noqa: F401  (importing registers the tables on Base)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    print("[db] tables ready ->", config.DATABASE_URL)


async def get_session():
    """FastAPI dependency: gives each request its own database session."""
    async with SessionLocal() as session:
        yield session


def _add_missing_columns(sync_conn):
    """
    create_all() never changes an existing table, so an old ghumi.db from
    v23 would crash on the new owner_id column. Add any missing nullable
    column by hand - a two-line migration instead of setting up Alembic.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(sync_conn)
    if "trips" not in inspector.get_table_names():
        return
    have = {c["name"] for c in inspector.get_columns("trips")}
    if "owner_id" not in have:
        sync_conn.execute(text("ALTER TABLE trips ADD COLUMN owner_id VARCHAR(64)"))
        print("[db] added trips.owner_id")
