# v24 - recommendations you can trust, a plan that works on the day

Every item below came out of a code review of v23 and has a regression test in
`backend/tests/test_v24_quality.py` (213 tests in total, all offline).

## Recommendations
- Interest points now scale with quality, and the score with relevance
  (`recommend.score_attraction`). v23: Domino's 52 > Amer Fort 43.
- Evidence gate for common kinds (temple, park, market, bar, cafe) with no
  evidence at all; documented off-interest landmarks kept as back-ups.
- Wikidata sitelinks (Wikipedia language editions) as a free popularity signal.
- Overpass: always ask for sights; temples/parks/markets only when notable;
  no more cutting the pool before ranking; `historic` tag decides the kind.
- Whole-word filters with sight-word overrides (Umaid Bhawan Palace, Cellular
  Jail, Courtallam Falls, Dhobi Ghat, Mandi...). Interest words whole-word.
- Fuzzy de-duplication and agreement counting across sources.
- Lakes are lakes; Wikivoyage listings without coordinates flagged; Ticketmaster
  limited to trip dates; curated list not counted as an independent source.
- Hand-picked lists for 24 cities, with closed days and best times.
- Restaurants ranked; never sightseeing stops.

## Planning
- Notes -> structured preferences (rules + validated LLM JSON) applied to
  ranking, days, transport and lunch (`preferences.py`).
- Capacity rebalancing, k-means seed fix, fewer/fuller days for thin pools.
- Date assignment honours closed days, rain and short days.
- Arrival/departure windows from the journey; Leh acclimatisation day.
- Morning/evening ordering; lunch near the stops, not repeated, not at 10 AM.
- Critic -> repair step (far lunch) before the plan is written.
- LLM tool arguments locked to the trip; LLM prose checked against the plan.
- Group pricing for stays (rooms / hostel beds) used by the budget solver.
- Weather beyond 16 days from the same dates in past years; regional fallback.
- "Wettest day" only flagged when rain is actually likely.

## App and deployment
- Finished trips / share links no longer hang (stream ends at once; per-listener
  fan-out; stale "running" trips marked failed at startup; client fetches first
  and polls if the stream drops).
- Dates fixed for Indian time zone; past dates rejected.
- Anonymous per-browser ownership of trips; owner-only delete; rate limit;
  optional admin token; tiny SQLite/Postgres migration for the new column.
- Sandbox fares labelled as demo; checkout is a clearly labelled demo payment.
- Ticketmaster optional in strict mode.
- `eval_recommendations.py`: offline precision / noise / closed-day evaluation
  (79% -> 100% precision, 89 -> 1 noise stops while building v24) and a
  `--live` mode.
