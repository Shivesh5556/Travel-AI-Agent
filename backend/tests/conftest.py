"""
Shared test setup.

The tests run with STRICT_MODE off so they never need a live Groq or Duffel
key. That is the point of having the flag: strict for real use, permissive
for CI. Every external call falls back to offline data, so these tests also
prove the app does not crash when an API is down.
"""

import os

import httpx
import pytest

os.environ["STRICT_MODE"] = "false"
os.environ["GROQ_API_KEY"] = ""
os.environ["DUFFEL_API_KEY"] = ""
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_ghumi.db"


@pytest.fixture(autouse=True)
def no_live_provider_http(monkeypatch):
    """Offline tests must never wait on or consume a real provider's quota.

    ASGITransport still exercises our actual API; provider-specific tests can
    supply MockTransport or mock clients without changing this guard.
    """
    async def blocked(self, request):
        raise httpx.ConnectError("Live provider HTTP disabled in tests", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
