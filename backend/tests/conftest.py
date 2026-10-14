"""
Shared test setup.

The tests run with STRICT_MODE off so they never need a live Groq or Duffel
key. That is the point of having the flag: strict for real use, permissive
for CI. Every external call falls back to offline data, so these tests also
prove the app does not crash when an API is down.
"""

import os

os.environ["STRICT_MODE"] = "false"
os.environ["GROQ_API_KEY"] = ""
os.environ["DUFFEL_API_KEY"] = ""
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_ghumi.db"
