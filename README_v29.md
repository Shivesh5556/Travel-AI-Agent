# Ghumi Ghumi

### *Bolo kahaan. Baaki hum dekh lenge.*

**Tell us where. We'll make it fit what you've got.**

**Ghumi Ghumi finds the places actually worth your time — based on your interests, your budget, quality evidence and the route — then builds the itinerary around them.**

An agentic travel planner for India. Name a place and a number — Ghumi Ghumi
looks up real fares, real weather and real places, then keeps re-planning
(flight to train, resort to guesthouse) until the trip actually fits. No
"sorry, out of budget" on the last screen.

FastAPI backend, React frontend, real data from real APIs.

---

## What makes it different from MakeMyTrip or Goibibo

Those sites give you a search box and let you discover at checkout that you
cannot afford it. This does four things they do not:

**1. The budget is a constraint, not a filter.**
The agent starts optimistic, notices it is ₹22,000 over, drops the daily
spending level, notices it is still ₹4,000 over, swaps the flight for an
overnight train, and lands inside the budget. Every one of those rounds is
shown on screen, struck through, so you see the reasoning rather than just
the answer.

**2. The plan is a road, not a list.**
A finished trip is drawn as a highway: your home city on a signboard at the
top, a numbered milestone stone for each day, the destination at the end.
The agent's internal steps are shown live while it plans, then the finished
page shows only the trip. (For a viva, the full step-by-step trace is
printed in the uvicorn terminal as `[agent:<id>] ...` lines.)

**3. Days are planned by geometry, not by vibes.**
Attractions are grouped with k-means clustering so each day is one part of
the city, then ordered from your hotel with nearest-neighbour so you are not
crossing town twice. Both algorithms are written out by hand in
`backend/dayplanner.py`. An LLM guessing at this will happily send you back
and forth across Jaipur.

**4. It keeps working after you close the tab.**
A background agent re-checks the forecast for every planned trip. If Day 3
becomes rainy, it works out that your Day 1 is 80% museums and tells you to
swap them.

---

## What's new in v29 (the same number of activities every day)

Every sightseeing day now gets the same number of stops (at most one apart when
the places don't divide evenly) - no more "four stops today, one tomorrow".
`dayplanner.level_stop_counts` first fills a light day with the best unused
place nearby, then moves a stop from a busy day, and only trims as a last
resort; then it tops up the lightest days while good places sit unused and the
day stays comfortable (about 5.5 h). It runs when the plan is built and again
after the agent's own fixes. Jaipur (4 days): 2,1,2,2 stops -> 3,3,3,3, all 12
places used. Across 48 test trips: 0 uneven, 91% of found places used.
Details in `CHANGES_v29.md`.

## What's new in v28

Debugged by running the real `.env` with every provider unreachable: Groq and
dead hosts now fail fast (57 s -> 6 s), small budget overshoots trim the
safety buffer instead of halving the food budget, old Foursquare `fsq3` keys
are detected (that API was retired on 15 May 2026), and the weather label says
why there is no forecast. Details in `CHANGES_v28.md`.

## What's new in v27 (faster, and the agent fixes its own plan)

| Change | Where |
|---|---|
| **The reviewer's findings go to the agent, not the traveller.** The LLM critic now returns structured findings (`travel_time`, `too_packed`, `too_light`, `weather`, `order`) and the agent fixes each one - moves a day trip off the departure day, drops the weakest stop, adds a nearby open place, swaps a rainy day. Rules re-check the fixed plan. The traveller only sees what no plan change can fix (over budget, altitude, rain every day, a very long journey). The review log is printed in the server terminal and kept in `plan["agent_review"]`. | `agent/repair.py`, `agent/critic.py`, `agent/loop._review_and_fix` |
| **Day trips include the drive.** Places more than 10 km out (Mussoorie, Kempty Falls, Fatehpur Sikri) get road distance and time each way, with winding hill roads detected from real elevations (Open-Meteo, free). Long drives stay off arrival/departure days, and outings are never mixed with city stops. Every day's time now includes the way back to the stay. | `dayplanner.py`, `tools/geo.elevations` |
| **Overpass mirrors are raced**, not tried one by one: if the best mirror has not answered in 4 s the next joins in, first answer wins, and the fastest mirror is remembered. | `tools/webclient.overpass` |
| **Failing sources are paused** after the first 401/403 with a clear hint in the log (Foursquare key rejected, Wikimedia needs a contact). v26 repeated them ~10 times per plan. | `tools/webclient.py` |
| **Tool calls run in parallel** (in the LLM path and the fallback path), the extra "Done." model round trip is skipped, and lunch repairs run together. | `agent/loop.py` |
| Stays not open to tourists (government / officers' guest houses, messes, institute hostels) are skipped. | `tools/stays.py` |

Measured with the network conditions from a real v26 server log (three Overpass
mirrors timing out, Wikipedia 403, Foursquare 401): gathering + planning went
from **99 s to 15 s**, before the LLM savings.

**Set `NOMINATIM_CONTACT` to your email.** Wikipedia and Wikivoyage refuse
requests (403) whose User-Agent has no contact - the server prints a warning
at startup if it is missing.

## What's new in v25

| Change | Where |
|---|---|
| **Even days.** Stops move from the fullest day to the emptiest (counting the drive out and back), light days are topped up, and no day has more than 5 stops unless you ask for a packed trip. No more "six stops today, one tomorrow". | `dayplanner.even_out`, `fill_light_days` |
| **Transport shown as the return fare for the whole group**, with the per-person figure beside it (the budget always counted both ways; the card used to show one way). | `agent/loop._with_return_fare`, `PlanView.jsx` |
| **View-once trips.** No "My trips" page and no list endpoint. The browser deletes its trip when you plan another, close the tab or refresh; a background job removes anything older than `TRIP_TTL_HOURS` (default 6). Itinerary edits and checklist ticks are no longer stored in the browser either. | `App.jsx`, `api.forgetTrip`, `monitor.purge_old_trips`, `main.py` |
| **Cleaner plan page**: the "how the planner worked this out" panel, the planning-rounds / style / time-to-plan / token-cost boxes and the share-link button are gone. | `PlanView.jsx` |

## What's new in v24 (recommendations you can trust, and a plan that works on the day)

v23 scored places, but the score had a hole: interest points were awarded for a
place's *type* alone, so with "Culture + Food" selected a neighbourhood temple or
a Domino's (about 52/100) outranked Amer Fort (43/100). v24 fixes that and every
other problem found in the review. All of it is covered by tests
(`tests/test_v24_quality.py`) and by an evaluation script with numbers
(`python eval_recommendations.py`).

**Recommendations**

| Change | Where |
|---|---|
| **Interest points scale with quality**, and the whole score scales with relevance. A famous landmark that matches beats a nobody that matches; a famous bazaar beats a famous waterfall for a shopper. | `recommend.py` |
| **Evidence gate**: a temple, park, market or bar that *nothing* vouches for (no wikidata, heritage tag, tourism tag, second source or rating) can no longer be a pick. Well-documented landmarks that are off-interest are kept as back-ups instead of vanishing. | `recommend.py`, `places.rank_places` |
| **Free popularity signal from Wikidata**: how many Wikipedia language editions cover a place (Taj Mahal 150+, a local shrine 0-2). One batched call, cached for a month. | `places.enrich_with_wikidata` |
| **OpenStreetMap query asks for sights**, and only *notable* temples/parks/markets (wikidata / heritage / tourism tags). v23 took the first N results in database-id order and cut the list before ranking. | `places._build_query`, `find_attractions` |
| **Kind comes from the `historic` tag first**: Amer Fort is a fort (10/10), not a generic "attraction" (6/10). One kind vocabulary for every source. | `places._clean_results`, `recommend.canonical_kind` |
| **Whole-word filters**: Umaid Bhawan Palace, Cellular Jail, Courtallam Falls, Dhobi Ghat, Parmarth Niketan and every place in Mandi are no longer dropped; "Bara Imambara" is no longer Nightlife ("bar"). | `places.py`, `recommend.py` |
| **Duplicates merged**: "Hawa Mahal", "Hawa Mahal, Jaipur" and "Palace of Winds (Hawa Mahal)" are one stop, and spelling variants count as agreeing sources. | `places._merge_sources`, `_source_votes` |
| Lakes are lakes (not waterfalls); Wikivoyage listings without coordinates are no longer pinned to the city centre; Ticketmaster only returns events **on your dates**; the hand-picked list is no longer called an "independent source". | `places.py` |
| **Hand-picked landmarks for 24 cities** (was 16), now with **closed days** (Taj Mahal: Fridays, many museums: Mondays) and **best time** (sunrise points, aartis, the Wagah ceremony). | `places.CURATED_ATTRACTIONS` |
| **Restaurants are ranked** (Wikivoyage write-ups, ratings, diet fit, chains last) and are lunch picks, never sightseeing stops. | `places.rank_restaurants` |

**A plan that works on the day**

| Change | Where |
|---|---|
| **Your notes are obeyed.** "Vegetarian, no early flights, one of us cannot walk far, must visit Nahargarh Fort" becomes rules for the ranking, the day planner, the transport choice and the lunch picks (rules first; the LLM adds understanding through a validated JSON schema). The plan shows what it understood. | `preferences.py` |
| **Balanced days**: overfull days hand stops to neighbouring days; a thin pool uses fewer, fuller days. The k-means seed bug (a centre built from two different points) is fixed. | `dayplanner.rebalance`, `kmeans` |
| **Closed days respected**: which group of places goes on which date is solved as a small assignment problem (closed days, rain, short days). | `dayplanner.assign_to_dates` |
| **Arrival and departure days**: Day 1 starts after the journey (an overnight train or a travel day when it is long), the last day ends in time to go home. Leh gets an acclimatisation day. | `dayplanner.day_windows`, `agent/loop.py` |
| **Morning/evening ordering**: sunrise points first, sunset views and aartis last. | `dayplanner.order_for_the_day` |
| **Lunch near the day's stops, never repeated**, and never at 10:00 AM. If it is far, the critic flags it and the agent searches near that day's stops before writing anything (visible in the trace). | `dayplanner.pick_lunch`, `agent/loop._repair` |
| **Group pricing**: four adults need two rooms; a hostel is priced per bed. The budget solver uses the group price. | `stays.price_for_group`, `agent/replan.py` |
| **Weather beyond 16 days** uses what the same dates were really like in the last three years (Open-Meteo archive), falling back to regional tables (Leh has no monsoon; Chennai's rain is Oct-Dec). | `tools/weather.py` |
| **The LLM is kept honest**: it cannot change the trip's facts in tool calls, and every paragraph it writes is checked (no prices, must mention the day's stops) or replaced by the template. | `agent/loop.py` |

**Bugs and deployment**

| Change | Where |
|---|---|
| **Saved trips and share links no longer spin forever** (the stream of a finished trip now ends at once; events fan out to every open tab; interrupted trips are marked failed on restart). | `main.py`, `agent/events.py`, `App.jsx` |
| **Dates are right in India** (v23's date maths used UTC, so a 4-day card became 3 days). Past start dates are rejected. | `TripForm.jsx`, `schemas.py` |
| **Trips are private to the browser that planned them** (anonymous id); only the owner can delete; share links still open. **Rate limit** on planning, optional **admin token** for metrics/monitor. | `main.py`, `api.js` |
| Duffel sandbox fares are labelled **"demo fare (test data)"**; checkout is clearly a **demo payment** and the reference is "not a PNR". Ticketmaster is optional. | `PlanView.jsx`, `Checkout.jsx`, `config.py` |

### Measuring it

```bash
cd backend
python eval_recommendations.py          # offline, about a second
python eval_recommendations.py --live   # real APIs, needs internet
```

The offline evaluation mixes each city's real landmarks (tagged the way
OpenStreetMap tags them) with typical noise - neighbourhood temples, cafes, a
bank, a bus stand, colony parks - and measures how many top picks are real
landmarks, how much noise reaches a day plan and whether anything lands on a
closed day. While building v24 it went from **79% precision / 89 noise stops**
to **100% / 1** (the 1 is a last-resort filler in a city with only five
hand-picked sights, labelled low confidence).

## What's new in v23 (recommendations that fit the budget, and say why)

v22 was good at finding *real* places. v23 is about deciding which are *worth
recommending* — and doing it inside the budget, which is the whole point of the
product. The LLM never ranks anything; it only writes up what the code chose.

| Change | Where |
|---|---|
| **0–100 recommendation score** for every attraction: interest match 25, popularity 20, tourist significance 15, source agreement 10, type quality 10, route fit 10, budget fit 5, season fit 5. Route fit is only 10 so a mediocre place cannot win by being 500 m closer. | `backend/recommend.py` |
| **Budget-fit and season-fit are part of the score**: a nightclub costs a shoestring trip points, a waterfall gains them in the monsoon, indoor places gain on a rainy forecast. | `recommend.py`, `agent/loop.py` |
| **Second ranking pass** once the budget solver has decided where you sleep and how freely you can spend. | `agent/loop.py`, `places.rerank_places` |
| **Much stronger negative filters** (railway/bus stations, hospitals, banks, offices, industrial areas, schools and colleges unless notable, generic residential) with false-positive guards (*Government Museum*, *Mandir*, *Chandni Chowk* all survive). | `recommend.py` |
| **Evidence, confidence and "why"** on every stop and stay: sources, rating, score parts, `high / medium / low` confidence, and a plain sentence built from the real evidence. **Must-see now needs evidence**, not just a high score. | `recommend.py`, `Why.jsx` |
| **Quality first, then geography**: the best places are shortlisted *before* clustering, so a cluster of mediocre places can no longer win a whole day. | `dayplanner.py` |
| **Hotels ranked, not cheapest-first**: budget fit, quality, distance to where the good sights are, evidence, and travel-style fit. Weights shift by style (budget leans on price, comfort on quality). Top picks plus a price-difference for each. | `recommend.rank_stays`, `tools/stays.py` |
| **"Worth stretching for"**: the better stay just above the ceiling, with the honest extra cost. The plan itself never leaves the budget. | `agent/replan.py`, `StayPicks.jsx` |
| **The solver now also spends spare money**: if a trip fits with a large amount left, daily spending moves up a level while it still fits (not for "budget" travellers, who keep the saving). | `agent/replan.py` |
| **Foursquare quality signals actually consumed** (rating, popularity, price tier) for places and hotels when a key is set; interest-specific queries. Stays optional — nothing breaks without it. | `tools/places.py`, `tools/stays.py` |
| **Bug fix**: a stay ceiling of ₹0 ("no money left for a bed") used to be treated as "no limit". It now picks the cheapest. | `agent/replan.py` |
| Critic warns when a day is mostly low-confidence places. | `agent/critic.py` |
| Hindi strings for all new labels. | `i18n.hi.js` |

Prices are still **estimates** (there is no free live Indian hotel-rate API) and
are labelled as such. A booking-inventory provider is the right *later* step and
should stay optional so the free deployment keeps working.

## What's new in v20 (frontend redesign)

Backend is **unchanged** (same API, same tests, same restricted-places filter).
Everything below lives in `frontend/src`.

Ideas borrowed from [trip-planner.in](https://trip-planner.in), kept inside Ghumi Ghumi's
own concept (the budget is a hard rule, nothing is invented, every estimate is labelled):

| Feature | Where |
|---|---|
| New look: dark hero with the form floating on it, rounded cards, sticky nav, print stylesheet | `index.css`, `App.jsx` |
| Popular-trip cards that pre-fill the planner (destination, days, interests, starting budget) | `HomeSections.jsx`, `destinations.js` |
| Adults / children steppers, budget slider + presets, live per-person and per-day maths, swap-cities button | `TripForm.jsx` |
| Day tabs with real clock times, travel legs and a lunch slot | `RoadmapView.jsx`, `lib/itinerary.js` |
| Route drawn per day (plain SVG, no map library) + "Open route in Google Maps" link | `RouteMap.jsx` |
| Edit the itinerary: drop a stop, reorder, move to another day; times and km re-calculate, reset anytime (stored in the browser only) | `RoadmapView.jsx` |
| Display currency switch (INR, USD, EUR, GBP, AED, SGD). Indicative fixed rates, labelled "approx." | `currency.jsx` |
| Budget meter and stacked "where the money goes" bar | `BudgetPanel.jsx` |
| Weather cards and a "Before you go" checklist built from the real forecast | `PlanView.jsx`, `Checklist.jsx` |
| Sticky summary rail: verdict, jump links, copy plan as text, print / save PDF | `PlanView.jsx` |
| How it works, Why Ghumi Ghumi and FAQ sections | `HomeSections.jsx` |

Currency rates are fixed placeholders. Update them in `frontend/src/currency.jsx`.

## Where every number comes from

This matters more than any feature. Nothing on screen is invented.

| Thing | Source | Real? |
|---|---|---|
| Flight fares | Duffel API | **Real**, live search |
| Weather | Open-Meteo | **Real** forecast within 16 days, seasonal average beyond |
| Attractions, restaurants | OpenStreetMap Overpass | **Real** places, real coordinates |
| Hotels & guesthouses | OpenStreetMap Overpass | **Real** properties |
| Hotel prices | Our own model in `tools/stays.py` | **Estimated** — no free API sells Indian hotel rates |
| Train / bus fares | Distance × standard per-km rates | **Estimated** — IRCTC has no public API |
| Attractions (fallback) | Wikipedia geosearch | **Real**, filtered for quality |
| Attractions, restaurants (optional upgrade) | Geoapify Places API (confirmed free, no card) | **Real**, merged into the same `find_attractions`/`find_restaurants` results if you add a key |
| Live events (optional upgrade) | Ticketmaster Discovery API (confirmed free, no card) | **Real** listings, folded into `find_attractions` as extra stops where Ticketmaster has coverage |
| Local tips | 45-entry corpus + TF-IDF retrieval | Written by hand |
| The itinerary prose | Groq / Llama 3.3 70B | Written by the model, from real data only |

Every estimated price is tagged `price_is_estimate` in the API and shows an
"estimated price" label in the UI. The distinction is never hidden.

> **Note on transport APIs we looked at and did not use.** CRIS's own
> "RailRadar" train tracker (railradar.trainenquiry.com) has no public
> developer API — CRIS has said publicly it does not open one up. Third-party
> aggregators exist (e.g. railwayapi.com) but need a station-code lookup this
> project does not yet have, so trains and buses stay honest distance
> estimates for now (see `tools/transport.py`). Adding a real station-code
> table is the natural next step if you want to take this further.

### Optional: food, places & events upgrades

Attractions and restaurants already work with **zero keys** — OpenStreetMap,
Wikipedia and Wikivoyage are free and keyless. Add either key below to
`.env` and it gets merged straight into the same `find_attractions` /
`find_restaurants` calls the agent already makes — no new tool, no extra
step, nothing else to wire up. Leave them blank and nothing changes.

Checked against each provider's own pricing page (Sep 2026) before putting
them here, because "free API key" claims go stale fast:

| Key | What it adds | Free tier | Sign up |
|---|---|---|---|
| `GEOAPIFY_API_KEY` | A second, independent places/food source | **Confirmed** free, no card — 3,000 requests/day (geoapify.com/pricing) | https://myprojects.geoapify.com |
| `TICKETMASTER_API_KEY` | Live events — concerts, festivals, parties — added as extra stops with the date in the name | **Confirmed** free, no card — 5,000 calls/day (developer.ticketmaster.com) | https://developer-acct.ticketmaster.com/user/register |
| `FOURSQUARE_API_KEY` | Best single source for food + places | **Not** unconditionally free — Foursquare's own pricing page describes "sandbox → $200/month credit → pay-as-you-go", not a plain free tier | Skip unless you already have a key you're happy with |

The code merges in whichever of these you set — get just Geoapify and
Ticketmaster and you already have two extra real sources, no billing risk
attached.

> **Note on Amadeus.** Most tutorials tell you to use the Amadeus
> Self-Service API for flights. It shut down on 17 July 2026 and the keys no
> longer work. That is why this project uses Duffel.

---

## Running it in VS Code

You need **Python 3.11+** and **Node 18+**. Check with `python --version` and
`node --version`.

### Step 1 — get the three free API keys

All take about two minutes and none asks for a card.

**Groq** (the model that drives the agent)
1. Go to https://console.groq.com/keys
2. Sign in with Google
3. Create API Key → copy it (starts with `gsk_`)

**Duffel** (flight search)
1. Go to https://app.duffel.com/join
2. Sign up
3. Developers → Access tokens → create a **Test** token (starts with `duffel_test_`)

A test token returns Duffel's test airline, so the app labels those fares
"demo fare (test data)". Real fares need a live token.

**Geoapify** (a second places and hotels source)
1. Go to https://myprojects.geoapify.com
2. Sign up, create a project, copy the API key

### Step 2 — open the project

Open VS Code → File → Open Folder → pick the `ghumi-ghumi` folder.

Install these two extensions if you do not have them:
- **Python** (Microsoft)
- **ES7+ React snippets** (optional, just nice to have)

### Step 3 — the backend

Open a terminal in VS Code with **Ctrl + `** (backtick), then:

```bash
cd backend

# make a virtual environment
python -m venv .venv

# turn it on
# Windows:
.venv\Scripts\activate
# Mac or Linux:
source .venv/bin/activate

# install everything
pip install -r requirements.txt

# make your settings file
# Windows:
copy .env.example .env
# Mac or Linux:
cp .env.example .env
```

Now open `backend/.env` in VS Code and paste your three keys:

```
GROQ_API_KEY=gsk_your_key_here
DUFFEL_API_KEY=duffel_test_your_key_here
GEOAPIFY_API_KEY=your_geoapify_key
```

Start the server:

```bash
uvicorn main:app --reload
```

You should see `[app] all required keys present`. Leave this terminal running.

Check it works: open http://127.0.0.1:8000/docs — that is the auto-generated
API documentation, and you can test every endpoint from there.

### Step 4 — the frontend

Open a **second** terminal in VS Code (the `+` icon in the terminal panel):

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**.

That is it. Plan a trip and watch the road on the right fill up with
milestones as the agent works.

### If something goes wrong

| Problem | Fix |
|---|---|
| "Cannot reach the backend" | The uvicorn terminal is not running, or it is on a different port |
| Setup screen keeps showing | Keys are in `.env` but the server was not restarted. Ctrl+C, then `uvicorn main:app --reload` again |
| `ModuleNotFoundError` | The virtual environment is off. Run the activate command again |
| `the greenlet library is required` | `pip install greenlet` (Python 3.13 does not always pull it in) |
| `model_not_found` / `model_decommissioned` from Groq | Groq retired that model. The app now auto-picks a live one on startup; if it still fails, check https://console.groq.com/docs/models and set `GROQ_MODEL=` in `.env` |
| Stay shows "typical rate, not a listing" | OpenStreetMap was unreachable, so you get a price band rather than an invented hotel name. Press "Re-check this trip now" and plan again |
| Places show as `[sample] ...` | Both real sources were unreachable. The app tries three Overpass mirrors, then Wikipedia geosearch, before falling back to samples. Try again in a minute |
| Groq `429 rate limit` | The free tier is 8,000 tokens/minute. The app now waits and retries automatically. If it still fails, wait a minute or set `GROQ_MODEL=openai/gpt-oss-20b` |
| `npm: command not found` | Node is not installed — get it from nodejs.org |
| No flights found for a route | Duffel's sandbox has limited inventory. Try a major route like Delhi → Mumbai |
| Overpass timeouts | The free OSM API is busy. Wait a minute; results are cached once they come through |

**No keys and a demo in ten minutes?** Set `STRICT_MODE=false` in
`backend/.env`. Everything runs on estimated numbers instead, clearly
labelled. Do not show this version to a recruiter — the real-data version is
the whole point.

---

## The tests

```bash
cd backend
pytest
```

238 tests, no API keys and no internet needed (the test config turns strict
mode off, so every external call uses its offline fallback — which also proves
the app survives an API being down).

```
tests/test_replan.py         the budget solver, including "re-planning must
                             never make the trip more expensive"
tests/test_dayplanner.py     clustering and route ordering, including a test
                             that the optimised route really is shorter
tests/test_rag.py            retrieval scoring, place-quality filtering
                             and currency conversion
tests/test_api.py            full end-to-end runs against the real app
tests/test_v24_quality.py    one regression test per problem fixed in v24:
                             landmarks beat noise, closed days, balanced days,
                             group pricing, notes -> rules, stream/ownership
```

Then `python eval_recommendations.py` for the quality numbers.

---

## How it is put together

```
ghumi-ghumi/
├── backend/
│   ├── main.py              FastAPI routes, including the SSE stream
│   ├── config.py            settings and strict mode
│   ├── schemas.py           Pydantic v2 request/response models
│   ├── db_models.py         SQLAlchemy tables
│   ├── database.py          async engine and session
│   ├── dayplanner.py        k-means + nearest neighbour, no AI
│   ├── monitor.py           the background agent that keeps watching
│   ├── payments.py          Razorpay test mode + signature check
│   ├── llm.py               Groq client with tool calling
│   ├── cache.py             Redis if configured, dict if not
│   ├── metrics.py           latency and token cost per step
│   ├── agent/
│   │   ├── loop.py          the agent run, start to finish
│   │   ├── replan.py        the budget solver  ← the interesting file
│   │   ├── critic.py        rule checks + an LLM review pass
│   │   ├── registry.py      tool schemas the model sees
│   │   └── events.py        the live event stream
│   ├── tools/               one file per external data source
│   ├── rag/                 TF-IDF retriever + the tips corpus
│   └── tests/
└── frontend/
    └── src/
        ├── App.jsx
        ├── api.js           every backend call, plus the SSE client
        └── components/
```

### How one plan runs

```
POST /api/trips  →  returns an id immediately, plans in the background
                    the browser opens /api/trips/{id}/stream and watches

  1  GATHER   the model is given seven tools and decides what to call
  2  SAFETY   anything it skipped, we fetch ourselves
  3  SOLVE    budget solver, pure Python, up to 5 re-planning rounds
  4  ROUTE    k-means into days, nearest neighbour within each day
  5  WRITE    the model writes the prose, using retrieved local tips
  6  CRITIC   rule checks, then an LLM review
```

**The design decision worth defending in a viva:** the model does language,
Python does maths and geometry. Prices, distances and day assignments never
touch the LLM. That is why `test_replan.py` and `test_dayplanner.py` can
exist at all — you cannot unit-test a hallucination.

### Things worth pointing at

- `agent/loop.py` sends the model a **one-line summary** of each tool result
  rather than the raw JSON. Thirty attractions with coordinates is thousands
  of tokens the model does not need — our Python code does. This alone cuts
  the cost of a plan by roughly ten times.
- `GET /api/metrics` shows tokens, rupees and latency per step. Each plan
  reports what it cost to produce.
- Every external call has a timeout, a cache and a fallback. A dead API
  degrades the plan; it never crashes it.

---

## Docker

```bash
cp backend/.env.example backend/.env    # add your keys
docker compose up --build
```

Frontend on http://localhost:5173, backend on http://localhost:8000.

---

## Deploying it free on Render

The repo contains `render.yaml`, so Render sets everything up from it: ONE free
web service that builds the React site and serves it from FastAPI together
with the API (same origin, no CORS).

1. Push this folder to a GitHub repo (`.gitignore` keeps `backend/.env` out).
2. render.com -> sign in with GitHub -> **New -> Blueprint** -> pick the repo.
3. Paste `GROQ_API_KEY`, `DUFFEL_API_KEY`, `GEOAPIFY_API_KEY` and
   `NOMINATIM_CONTACT` when asked. `ADMIN_TOKEN` is generated for you.
4. Wait for the first build (a few minutes), open `https://<name>.onrender.com`.

Free-tier facts to know: the service sleeps after 15 minutes without
visitors and takes about a minute to wake; the disk is temporary (fine here -
trips are view-once); 512 MB RAM; 750 instance hours and 5 GB bandwidth a
month. Run one instance only: the live progress stream lives in memory.

## Switching to Postgres

Change one line in `backend/.env`:

```
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/ghumi
```

Uncomment `asyncpg` in `requirements.txt` and reinstall. No code changes.

---

## Known limits

Said plainly, because pretending otherwise is worse:

- **Hotel prices are estimated.** Real Indian hotel rates need a Booking.com
  or Expedia partner contract. The properties are real; the prices are a
  model you can read in `tools/stays.py`.
- **Train and bus fares are estimated.** IRCTC publishes no public API.
- **Duffel's sandbox has limited inventory.** Some routes and dates return
  nothing. In strict mode we show no flights rather than invent a fare.
- **Nothing is actually booked.** The "demo payment" uses Razorpay test keys.
  The flow is real — order creation, the hosted card widget, server-side HMAC
  signature verification — but no money moves, no reservation exists and the
  reference is not a PNR. Test card: 4111 1111 1111 1111, any future expiry,
  any CVV. Do not switch it to live keys without real bookings behind it.
- **Opening days and best times come from the hand-picked list and OSM's
  `opening_hours` tag.** Places outside both have no closed-day information.
- **Arrival times are estimates.** Without a train timetable API the planner
  assumes a 6 AM departure for short journeys and an overnight one for long
  ones, and says so on Day 1.
- **Trips are view-once.** Nothing is kept: when you plan another trip, close
  the tab or refresh, the trip is deleted. Use "Copy plan as text" or
  "Print / save PDF" to keep one.
- **Weather is shown, not narrated.** Each day carries its own rain
  percentage. Only the single wettest day is mentioned in the text — six
  paragraphs each opening with "it might rain today" is filler, not planning.
- **India only.** The city table, the fare models and the tips corpus are all
  built around Indian travel.
- **Wikipedia fallback needs filtering.** When Overpass is busy we fall back to
  Wikipedia geosearch, which geotags everything — elections, schools, government
  offices. `tools/places.py` scores every title against a keep/drop keyword list
  and requires a photo for anything uncertain. It is crude and it will
  occasionally drop something good; the alternative was itineraries containing
  "2008 Dehradun Municipal Corporation election" as a 90-minute stop.

---

## What to build next

- Real hotel rates behind a partner API, replacing the estimate model
- Swap TF-IDF for sentence-transformers + pgvector (only `rag/retriever.py`
  changes — that is why retrieval sits behind one function)
- User accounts with JWT, so trips follow people across devices (v24 has an
  anonymous per-browser id)
- Redis pub/sub for the live event stream, so the API can run more than one
  worker (events are in memory today)
- Celery Beat instead of the asyncio background task, so the monitor survives
  a restart
- 2-opt on top of nearest-neighbour for a genuinely shorter daily route
