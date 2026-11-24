# Run Ghumi Ghumi v30 in VS Code

## 1. Prerequisites

Install Python 3.12, Node.js 20.19+ (or 22.12+), and VS Code.
On Windows enable **Add Python to PATH**, and install VS Code's Python extension.
Check a new terminal using `py -3.12 --version`, `node --version`, and `npm --version`.

## 2. Extract and open

Extract `Ghumi_Ghumi_v30.zip`. Choose **File > Open Folder** in VS Code and
open the inner `ghumi-ghumi` folder containing `backend` and `frontend`.
Do not run from inside the ZIP viewer.

## 3. Install the backend

Choose **Terminal > New Terminal**. From the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
```

These commands use the virtual environment's Python directly, so PowerShell
activation/execution-policy changes are unnecessary. If you already have an
`.env`, keep your keys and add the new settings from the example.

## 4. Choose demo or configured-provider mode

Open `backend/.env`. For a first demo, set:

```dotenv
STRICT_MODE=false
```

Keys may stay empty. Some results are estimates/fallbacks. Public map/weather
services still need internet, and coverage can be limited.

For provider mode:

```dotenv
STRICT_MODE=true
GROQ_API_KEY=your_key
DUFFEL_API_KEY=your_key
GEOAPIFY_API_KEY=your_key
NOMINATIM_CONTACT=your_email
```

Use your own credentials. Duffel test tokens return demo inventory. Razorpay
is optional and only accepts `rzp_test_...` keys. Save `.env` and restart the
backend after configuration changes.

## 5. Start the backend

In terminal 1:

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Leave it running. It should listen at http://127.0.0.1:8000.

## 6. Start the frontend

Open a second terminal from the project root:

```powershell
cd frontend
npm ci
npm run dev
```

Leave it running and open **http://localhost:5173**. Keep using that hostname.
If 5173 is busy, stop the earlier process with Ctrl+C and restart.

## 7. Use the planner

Try Delhi to Jaipur with future dates and a total group budget. Select the
exact city from each picker. The browser establishes its private session
automatically. Refresh restores the trip in the same tab; **Plan another trip**
deletes it. Trips expire after six hours by default. Copy or print to keep one.

The **Print / save PDF** output includes every day of the edited itinerary.
All prices remain planning estimates where no bookable provider quote exists.

## Optional VS Code shortcuts

Run **Python: Select Interpreter** from the Command Palette and select `.venv`.
The included settings run pytest from `backend`. The Run and Debug panel has
a backend configuration, and **Terminal > Run Task** offers backend/frontend
tasks. Install dependencies and create `.env` before using them.

## Troubleshooting

| Problem | Solution |
| --- | --- |
| `py` is not recognized | Install Python 3.12 with the Windows launcher and restart VS Code; or use `python` after confirming its version. |
| `npm` is not recognized | Install Node.js and restart VS Code. |
| PowerShell blocks `npm.ps1` | Use `npm.cmd ci` and `npm.cmd run dev`, or switch to Command Prompt. |
| `No module named fastapi` | Install requirements with the same `.venv` Python used to start uvicorn. |
| Backend cannot be reached | Keep terminal 1 running and inspect its errors; frontend `/api` proxies to port 8000. |
| Missing-key setup screen | Add the three keys or set `STRICT_MODE=false`, then restart the backend. |
| Session/trip disappears after changing host | Use the same hostname. `localhost` and `127.0.0.1` store separate cookies. Trips also expire. |
| Unknown city | Select a suggestion again; unresolved locations now fail explicitly. |
| Provider 401/403/timeout | Check that provider's key/quota, your contact email, and network access. |
| Port already in use | Stop the previous terminal process with Ctrl+C before restarting. |
| Old v29 trip unavailable | Start a fresh trip; old header-owned trips cannot be claimed by a new cookie session. |

## Tests

From the project root:

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
```

In a separate terminal from the root:

```powershell
cd frontend
npm test
npm run build
```

For macOS/Linux, create the environment with `python3.12 -m venv .venv`, use
`.venv/bin/python` instead of `.venv\Scripts\python.exe`, and copy the example
using `cp backend/.env.example backend/.env`. Other commands stay the same.
