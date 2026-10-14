# v29 - the same number of activities every day

## What the traveller sees
Every sightseeing day has the same number of stops (at most one apart when the
places don't divide evenly). Arrival or departure days with less than 4 free
hours are left lighter on purpose.

| Trip (offline data) | before | v29 |
|---|---|---|
| Jaipur, 4 days | 2, 1, 2, 2 stops (7 of 12 places) | 3, 3, 3, 3 (all 12) |
| 48 test trips (12 cities x 2-6 days) | 37 uneven | 0 uneven, 91% of places used |

## How (backend/dayplanner.py)
`level_stop_counts(day_stops, dates, avail, pool, hotel)`:
1. While two days differ by 2+ stops, raise the lightest day: add the best
   unused place nearby (open that date, within 12 km of the day's stops, still
   fits the day's hours with the drive), else move a stop from a busy day.
2. Only if nothing can be added anywhere, drop the busiest day's weakest stop
   (it becomes an "if you have time" idea).
3. **Top-up:** once the days are within one, the lightest day takes the best
   spare place nearby while the day stays under ~5.5 h; repeat. Only days at
   the minimum are raised, so the days never drift apart again.

It runs in `build_day_plan` (Step 4.6) and again in `agent/repair.Fixer.finish`,
because the agent's own fixes can drop or add a stop.

`_same_kind_of_day` still keeps a city temple off the Mussoorie outing, but a
place within 4 km (`NEXT_DOOR_KM`) of a day's stops now always fits that day.
Amer Fort is 10.3 km out (just past the 10 km "out of town" line), and Jal Mahal,
3.6 km away on the same road, was being refused. That left one 1-stop day, which
then trimmed every other day down to 2.

## Tests
245 pass (`tests/test_v27_agent.py`: levelling, top-up, next-door rule).
