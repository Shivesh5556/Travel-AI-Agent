# Ghumi Ghumi v30

**Bolo kahaan. Baaki hum dekh lenge.**

An India-focused travel-planning prototype with React and FastAPI. It ranks
places using evidence and interests, selects a feasible estimated budget, and
builds an itinerary around dates, geography and traveller preferences.

Read [RUN_IN_VSCODE.md](RUN_IN_VSCODE.md) for setup and troubleshooting, and
[CHANGES_v30.md](CHANGES_v30.md) for this update. `README_v29.md` is retained
only as historical documentation; its earlier behavior descriptions are outdated.

## Quick start: Windows / VS Code

Install Python **3.12**, Node.js **20.19+ or 22.12+**, and VS Code with the
Python extension. Extract the ZIP and open the inner `ghumi-ghumi` folder.

From a PowerShell terminal at the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
```

Edit `backend/.env`: set `STRICT_MODE=false` for a first demo, or retain
`STRICT_MODE=true` and supply your own Groq, Duffel and Geoapify keys.
Set `NOMINATIM_CONTACT` to your contact email. Keep payment keys blank unless
using Razorpay test checkout. Internet is required to install dependencies and
access public travel-data services; demo mode is not a complete offline database.

Terminal 1:

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Terminal 2, starting from the project root:

```powershell
cd frontend
npm ci
npm run dev
```

Open **http://localhost:5173**. Keep using this hostname; `localhost` and
`127.0.0.1` have separate cookies. Leave both terminals running.
API documentation: http://127.0.0.1:8000/docs.

On macOS/Linux use `python3.12 -m venv .venv`, `.venv/bin/python` instead of
`.venv\Scripts\python.exe`, and `cp backend/.env.example backend/.env`.

## Data and estimates

| Component | What the app knows |
| --- | --- |
| City | Exact selected Indian location, retained throughout planning |
| Flight | Duffel outbound offer when available; test-token inventory is demo data |
| Return journey | Twice the outbound fare, explicitly estimated; return availability is not searched |
| Train / bus | Distance-based fare and duration estimates, not confirmed seats or timetables |
| Ground connection | Island and railhead checks remove known impossible modes; remaining services are unverified |
| Hotel | Named properties when found; rates and availability are estimated |
| Weather | Available forecast dates, then historical/seasonal data outside the forecast horizon |
| Place opening information | Public/curated information where available; missing hours are not an access guarantee |
| Display currency | Indicative fixed exchange rates |
| Checkout | Razorpay test mode; no travel reservation is made |

The total is an **estimated planning budget**, not a purchasable quote.
Airport transfers, actual inventory, taxes and seasonal changes can affect
the final price. Confirm prices, routes and opening times before travel.

## Privacy and trip lifetime

The server issues an HttpOnly, SameSite browser-session cookie and stores only
its hash as the owner. Trip reads, events, monitoring, deletion and checkout
require ownership. A trip ID alone grants no access. API clients should first
call `POST /api/session` and retain its cookie.

Refresh restores the current trip in the same tab using an ID in
`sessionStorage`. Traveller details and complete itineraries are not stored
there. **Plan another trip** deletes the previous trip. Trips expire after
`TRIP_TTL_HOURS` (six hours by default), with deletion on the cleanup schedule.
Export using Copy or Print / save PDF before expiry. There are no accounts,
public sharing links, or email/push notifications. Legacy v29 trips cannot be
claimed using the new session mechanism.

## Tests and build

Backend, from the project root on Windows:

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
```

Frontend, in another terminal from the project root:

```powershell
cd frontend
npm test
npm run build
```

Backend tests block live provider HTTP while allowing real in-process API
requests and explicitly mocked provider responses. v30 adds regression tests
for solver feasibility, scheduling constraints, location identity, weather,
session privacy, rate limits, idempotency and payment configuration. Frontend
tests use Node's built-in test runner. GitHub Actions runs both suites and the
frontend build when this folder is used as a repository.

## Docker and deployment

Create `backend/.env`, then run `docker compose up --build` from the root.
Open http://localhost:5173. Compose sets `API_PROXY_TARGET=http://backend:8000`;
ordinary host development defaults to `http://127.0.0.1:8000`.

`render.yaml` builds the frontend and serves the website and API from one
origin. Use **one web worker**: jobs, rate limits, idempotency records and live
events are held in that process. Multiple workers/replicas require a durable
job queue and shared event/limit storage. Interrupted jobs fail on restart.

Admin endpoints require a nonempty `ADMIN_TOKEN`; they are disabled if it is
absent. Use HTTPS and `SESSION_COOKIE_SECURE=true` in production. Cookies are
also Secure automatically on HTTPS requests. SQLite is suitable locally;
temporary deployment disks can lose data on restart.

For PostgreSQL, separately install `asyncpg==0.30.0`, provision a database,
and set `DATABASE_URL=postgresql+asyncpg://...`. Redis caching is optional;
turning it on does not make jobs or event streams distributed.

## Code layout

`backend/main.py` owns API access and job limits. `agent/` owns orchestration,
budget solving, reviews and repairs. `dayplanner.py` owns routing and dates,
`recommend.py` owns ranking, and `tools/` owns provider integrations.
`frontend/src/components/` contains the interface and `src/lib/` contains
timing/edit validation. Optional VS Code settings are in `.vscode/`.
