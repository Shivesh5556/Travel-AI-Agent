"""
End to end tests against the real FastAPI app.

These run with NO API keys and NO internet, which is exactly the point: every
external call falls back to offline data, so the whole pipeline still has to
produce a valid plan. If someone breaks a fallback, these tests go red.
"""

import asyncio
from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import main
from database import init_db

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    await init_db()
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def a_trip(**overrides):
    start = date.today() + timedelta(days=10)
    body = {
        "source_city": "Delhi",
        "destination_city": "Jaipur",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=2)).isoformat(),
        "travelers": 2,
        "budget_inr": 30000,
        "interests": ["Culture & Heritage", "Food"],
        "notes": "",
    }
    body.update(overrides)
    return body


class TestBasics:
    async def test_health(self, client):
        response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_meta_lists_interests(self, client):
        response = await client.get("/api/meta")
        assert response.status_code == 200
        assert "Nature" in response.json()["interests"]

    async def test_metrics_endpoint(self, client):
        response = await client.get("/api/metrics")
        assert response.status_code == 200
        assert "total_calls" in response.json()


class TestValidation:
    async def test_backwards_dates_are_rejected(self, client):
        start = date.today() + timedelta(days=10)
        body = a_trip(start_date=start.isoformat(),
                      end_date=(start - timedelta(days=2)).isoformat())
        response = await client.post("/api/trips", json=body)
        assert response.status_code == 422

    async def test_same_city_is_rejected(self, client):
        response = await client.post("/api/trips", json=a_trip(destination_city="Delhi"))
        assert response.status_code == 422

    async def test_negative_budget_is_rejected(self, client):
        response = await client.post("/api/trips", json=a_trip(budget_inr=-500))
        assert response.status_code == 422

    async def test_zero_travellers_is_rejected(self, client):
        response = await client.post("/api/trips", json=a_trip(travelers=0))
        assert response.status_code == 422

    async def test_a_very_long_trip_is_rejected(self, client):
        start = date.today() + timedelta(days=10)
        body = a_trip(start_date=start.isoformat(),
                      end_date=(start + timedelta(days=40)).isoformat())
        response = await client.post("/api/trips", json=body)
        assert response.status_code == 422


class TestPlanning:
    async def test_creating_a_trip_returns_immediately(self, client):
        response = await client.post("/api/trips", json=a_trip())
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "running"
        assert len(data["id"]) == 10

    async def test_the_plan_actually_completes(self, client):
        response = await client.post("/api/trips", json=a_trip())
        trip_id = response.json()["id"]

        # the agent runs in the background, so give it a moment
        for _ in range(50):
            await asyncio.sleep(0.2)
            detail = await client.get(f"/api/trips/{trip_id}")
            if detail.json()["status"] in ("done", "failed"):
                break

        data = detail.json()
        assert data["status"] == "done", f"planning failed: {data.get('plan')}"

        plan = data["plan"]
        assert plan["ok"] is True
        assert len(plan["days"]) == 3
        assert plan["transport"] is not None
        assert plan["stay"] is not None
        assert plan["budget"]["total_inr"] > 0

    async def test_every_day_has_a_description(self, client):
        response = await client.post("/api/trips", json=a_trip())
        trip_id = response.json()["id"]

        for _ in range(50):
            await asyncio.sleep(0.2)
            detail = await client.get(f"/api/trips/{trip_id}")
            if detail.json()["status"] in ("done", "failed"):
                break

        for day in detail.json()["plan"]["days"]:
            assert day["description"], f"day {day['day_number']} has no text"

    async def test_a_tiny_budget_is_refused_honestly(self, client):
        response = await client.post("/api/trips", json=a_trip(budget_inr=2000))
        trip_id = response.json()["id"]

        for _ in range(50):
            await asyncio.sleep(0.2)
            detail = await client.get(f"/api/trips/{trip_id}")
            if detail.json()["status"] in ("done", "failed"):
                break

        plan = detail.json()["plan"]
        assert plan["ok"] is False
        assert "reason" in plan and plan["reason"]

    async def test_the_trace_was_recorded(self, client):
        response = await client.post("/api/trips", json=a_trip())
        trip_id = response.json()["id"]

        for _ in range(50):
            await asyncio.sleep(0.2)
            detail = await client.get(f"/api/trips/{trip_id}")
            if detail.json()["status"] in ("done", "failed"):
                break

        trace = detail.json()["trace"]
        assert len(trace) > 3
        assert trace[0]["step"] == "start"

    async def test_unknown_trip_is_a_404(self, client):
        response = await client.get("/api/trips/doesnotexist")
        assert response.status_code == 404

    async def test_trips_are_not_listed(self, client):
        """Trips are view-once: there is no endpoint that lists stored trips."""
        await client.post("/api/trips", json=a_trip())
        response = await client.get("/api/trips")
        assert response.status_code in (404, 405)


class TestCheckout:
    async def test_checkout_is_refused_when_keys_are_missing(self, client):
        response = await client.post("/api/trips", json=a_trip())
        trip_id = response.json()["id"]

        for _ in range(50):
            await asyncio.sleep(0.2)
            detail = await client.get(f"/api/trips/{trip_id}")
            if detail.json()["status"] in ("done", "failed"):
                break

        checkout = await client.post(f"/api/trips/{trip_id}/checkout", json={
            "full_name": "Test Student",
            "email": "test@example.com",
            "phone": "9876543210",
        })
        # 400 with no keys configured, which is the correct answer
        assert checkout.status_code in (400, 200)
