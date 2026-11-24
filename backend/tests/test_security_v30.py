"""Private trip access and bounded demo API regressions; no provider network calls."""

import asyncio
import hashlib
from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import config
import main
import payments
from database import Base, get_session
from db_models import Trip

pytestmark = pytest.mark.asyncio


def trip_body(**changes):
    start = date.today() + timedelta(days=10)
    body = {
        "source_city": "Delhi", "destination_city": "Jaipur",
        "start_date": str(start), "end_date": str(start + timedelta(days=2)),
        "travelers": 2, "budget_inr": 30000,
        "interests": ["Culture & Heritage"],
    }
    body.update(changes)
    return body


@pytest_asyncio.fixture
async def api_env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'security.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def session_dependency():
        async with sessions() as session:
            yield session

    calls = []

    async def offline_plan(trip_id, req):
        calls.append(trip_id)
        return {"ok": True, "fits": True, "budget": {"total_inr": 5000},
                "days": [], "attempts": [], "meta": {"ms": 1}}

    main.app.dependency_overrides[get_session] = session_dependency
    monkeypatch.setattr(main, "get_session_direct", sessions)
    monkeypatch.setattr(main, "plan_trip", offline_plan)
    monkeypatch.setattr(main, "_creation_lock", asyncio.Lock())
    monkeypatch.setattr(config, "STRICT_MODE", False)
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "MAX_TRIPS_PER_HOUR", 20)
    monkeypatch.setattr(config, "MAX_CONCURRENT_PLANS", 4)
    main._recent_plans.clear()
    main._idempotency.clear()
    main._running_plans.clear()
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as client:
        yield client, sessions, calls
    main.app.dependency_overrides.pop(get_session, None)
    main._recent_plans.clear()
    main._idempotency.clear()
    main._running_plans.clear()
    await engine.dispose()


async def establish(client):
    response = await client.post("/api/session")
    assert response.status_code == 200
    return response


async def test_cookie_is_private_and_owner_hash_is_stored(api_env):
    client, sessions, _ = api_env
    response = await establish(client)
    cookie_header = response.headers["set-cookie"]
    assert "HttpOnly" in cookie_header and "SameSite=lax" in cookie_header
    token = client.cookies.get(main.SESSION_COOKIE)
    assert len(token) == 64
    assert token not in response.text
    trip = (await client.post("/api/trips", json=trip_body())).json()
    assert len(trip["id"]) == 32
    async with sessions() as session:
        stored = await session.get(Trip, trip["id"])
        assert stored.owner_id == hashlib.sha256(token.encode()).hexdigest()
        assert stored.owner_id != token
    await establish(client)
    assert client.cookies.get(main.SESSION_COOKIE) == token


async def test_https_cookie_is_secure(api_env):
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="https://test") as client:
        response = await establish(client)
        assert "; Secure" in response.headers["set-cookie"]


async def test_session_is_required_and_legacy_header_cannot_authorize(api_env):
    client, _, _ = api_env
    response = await client.post("/api/trips", json=trip_body(),
                                 headers={"X-Client-Id": "old-browser-123"})
    assert response.status_code == 401
    assert (await client.get("/api/trips/unknown")).status_code == 401


async def test_stranger_cannot_read_stream_modify_or_monitor_trip(api_env, monkeypatch):
    client, _, _ = api_env
    await establish(client)
    trip_id = (await client.post("/api/trips", json=trip_body())).json()["id"]
    traveler = {"full_name": "Test Person", "email": "person@example.com", "phone": "9876543210"}

    async def fake_order(**kwargs):
        return {"id": "order_mocked", "amount": 500000}

    monkeypatch.setattr(config, "PAYMENTS_ENABLED", True)
    monkeypatch.setattr(payments, "create_order", fake_order)
    checkout = await client.post(f"/api/trips/{trip_id}/checkout", json=traveler)
    assert checkout.status_code == 200
    owner_trip = (await client.get(f"/api/trips/{trip_id}")).json()
    assert owner_trip["plan"]["checkout"]["traveler"]["email"] == traveler["email"]
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as stranger:
        await establish(stranger)
        requests = [
            ("GET", f"/api/trips/{trip_id}", {}),
            ("GET", f"/api/trips/{trip_id}/stream", {}),
            ("DELETE", f"/api/trips/{trip_id}", {}),
            ("POST", f"/api/trips/{trip_id}/checkout", {"json": traveler}),
            ("POST", f"/api/trips/{trip_id}/verify", {"json": {}}),
            ("POST", f"/api/monitor/run?trip_id={trip_id}", {}),
        ]
        for method, url, kwargs in requests:
            response = await stranger.request(method, url, **kwargs)
            assert response.status_code == 404, (method, url, response.text)
            assert traveler["email"] not in response.text
    assert (await client.get(f"/api/trips/{trip_id}")).status_code == 200
    stream = await client.get(f"/api/trips/{trip_id}/stream")
    assert stream.status_code == 200 and "__end__" in stream.text
    assert (await client.delete(f"/api/trips/{trip_id}")).status_code == 200


async def test_cross_origin_write_is_rejected(api_env):
    client, _, _ = api_env
    await establish(client)
    response = await client.post("/api/trips", json=trip_body(),
                                 headers={"Origin": "https://unrelated.example"})
    assert response.status_code == 403


async def test_ip_limit_survives_cookie_and_header_rotation(api_env, monkeypatch):
    client, _, calls = api_env
    monkeypatch.setattr(config, "MAX_TRIPS_PER_HOUR", 2)
    codes = []
    for index in range(3):
        client.cookies.clear()
        await establish(client)
        response = await client.post("/api/trips", json=trip_body(),
                                     headers={"X-Client-Id": f"rotating-client-{index}"})
        codes.append(response.status_code)
    assert codes == [202, 202, 429]
    assert len(calls) == 2


async def test_session_limit_also_applies_across_ips(api_env, monkeypatch):
    client, _, _ = api_env
    monkeypatch.setattr(config, "MAX_TRIPS_PER_HOUR", 2)
    await establish(client)
    token = client.cookies.get(main.SESSION_COOKIE)
    codes = []
    for index in range(3):
        transport = ASGITransport(app=main.app, client=(f"192.0.2.{index + 1}", 1234))
        async with AsyncClient(transport=transport, base_url="http://test",
                               cookies={main.SESSION_COOKIE: token}) as other_ip:
            codes.append((await other_ip.post("/api/trips", json=trip_body())).status_code)
    assert codes == [202, 202, 429]


async def test_idempotency_replays_one_trip_and_rejects_changed_payload(api_env):
    client, _, calls = api_env
    await establish(client)
    headers = {"Idempotency-Key": "submission-one"}
    first, second = await asyncio.gather(
        client.post("/api/trips", json=trip_body(), headers=headers),
        client.post("/api/trips", json=trip_body(), headers=headers),
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert len(calls) == 1
    changed = await client.post("/api/trips", json=trip_body(budget_inr=45000), headers=headers)
    assert changed.status_code == 409


async def test_running_job_bound_releases_after_completion(api_env, monkeypatch):
    client, _, _ = api_env
    await establish(client)
    monkeypatch.setattr(config, "MAX_CONCURRENT_PLANS", 1)
    started, finish = asyncio.Event(), asyncio.Event()

    async def blocked_plan(trip_id, req):
        started.set()
        await finish.wait()
        return {"ok": False, "reason": "Mocked result"}

    monkeypatch.setattr(main, "plan_trip", blocked_plan)
    first = asyncio.create_task(client.post("/api/trips", json=trip_body()))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        blocked = await client.post("/api/trips", json=trip_body())
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "10"
    finally:
        finish.set()
        await first
    assert not main._running_plans


async def test_translation_size_and_rate_are_bounded(api_env, monkeypatch):
    client, _, _ = api_env
    await establish(client)
    calls = []

    async def translate_stub(texts, lang):
        calls.append(texts)
        return texts, True

    monkeypatch.setattr(main.translate, "translate_with_status", translate_stub)
    monkeypatch.setattr(config, "MAX_TRANSLATION_CHARS", 100)
    monkeypatch.setattr(config, "MAX_TRANSLATIONS_PER_HOUR", 1)
    assert (await client.post("/api/translate", json={"texts": ["x" * 101]})).status_code == 422
    assert (await client.post("/api/translate", json={"texts": ["hello"]})).status_code == 200
    assert (await client.post("/api/translate", json={"texts": ["again"]})).status_code == 429
    assert calls == [["hello"]]


async def test_live_payment_keys_fail_before_any_network(api_env, monkeypatch):
    monkeypatch.setattr(config, "PAYMENTS_ENABLED", True)
    monkeypatch.setattr(config, "RAZORPAY_KEY_ID", "rzp_live_example")
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", "fake-secret")
    with pytest.raises(ValueError, match="test keys"):
        config.validate_payment_config()
    with pytest.raises(payments.PaymentsNotConfigured, match="test keys"):
        await payments.create_order(5000, "test-trip")


async def test_unconfigured_admin_endpoints_fail_closed(api_env, monkeypatch):
    client, _, _ = api_env
    await establish(client)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    assert (await client.get("/api/metrics")).status_code == 403
    assert (await client.post("/api/monitor/run")).status_code == 403
