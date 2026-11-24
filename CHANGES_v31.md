# v31 - destination-aware daily budget

## Problem
Food, local transport and activities cost the same everywhere
(Rs 1,750 per person per day at "normal"), while hotel prices already
used a city multiplier. Two price models that disagreed.

## What changed
- `backend/tools/city_prices.py` (new): one city price index, shared by hotels
  and daily costs. Name cleanup ("Jaipur, Rajasthan" -> jaipur, Bangalore ->
  bengaluru). Daily costs move only part of the way with the hotel index
  (food 0.5, local travel 0.4, activities 0.3), clamped to 0.75x-1.6x.
  `RESEARCHED_DAILY` lets real, dated figures override the index city by city.
- `backend/tools/budget.py`: `daily_rates(city)` gives all three styles for a
  city plus basis/confidence. `compute`, `cheapest_possible` and `stay_ceiling`
  take an optional `city`; no city = exactly the old numbers. Unknown city ->
  national average with a 10% buffer instead of 8%. Every budget now carries
  `daily_basis` so the UI can say where the numbers came from.
- `backend/agent/replan.py`: `solve` and `stay_alternatives` take `city` and pass
  it to every money function, so floor, hotel ceiling and totals agree.
- `backend/agent/loop.py`, `backend/agent/registry.py`: pass the destination to
  the solver and to the LLM's `calculate_budget` tool.
- `backend/tools/stays.py`: reads the shared index. Fixes a bug where a city
  picked from the autocomplete ("Jaipur, Rajasthan") got average hotel prices.
- `backend/agent/critic.py`, `loop.py`: same name fix for the altitude warning.
- `frontend/src/components/BudgetPanel.jsx`: shows the daily-cost basis note.
- `backend/tests/test_city_costs.py` (new): 27 tests.
