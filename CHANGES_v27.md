# v27 - faster, and the agent fixes its own plan

From a real v26 server log (193 s for one plan, a review note shown to the user
about a Mussoorie day without travel time):

- Review findings are acted on, not displayed: structured LLM critic +
  `agent/repair.py` (travel_time / too_packed / too_light / weather / order),
  second rules pass on the fixed plan, only traveller-actionable notes shown,
  review log in the server terminal and `plan["agent_review"]`.
- Day trips: road distance and time each way (hill roads from real elevation),
  kept off arrival/departure days, never mixed with city stops; every day's
  hours include the return to the stay; "back at your stay ~time" in the UI.
- Overpass mirror racing (hedged requests) + fastest-mirror memory.
- Per-host pause after 401/403/429 with hints (Foursquare key, Wikimedia UA).
- User-Agent always has a contact field; startup warning without
  NOMINATIM_CONTACT.
- Parallel tool calls (LLM and fallback paths), no extra "Done." turn,
  parallel lunch repairs with shared searches.
- Government/officers' guest houses and other non-public stays filtered.
- Tests: 233 passing (new `tests/test_v27_agent.py`).
- Simulated with the log's network conditions: 99 s -> 15 s for gathering and
  planning (LLM time not included).
