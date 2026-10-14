# v28 - debugged with the real .env

Ran the project with the real keys in strict mode, with every provider
unreachable (a firewalled network), and fixed what showed up:

- Groq unreachable: every LLM call retried 4x with back-off (~45 s of a 57 s
  plan). Now a connection failure is retried once, then LLM steps use their
  fallbacks for 60 s. Same plan: 57 s -> 6 s.
- Hosts that cannot be connected to (not just 401/403) are paused for 60 s
  with one log line instead of being retried inside the plan.
- Budget: a small overshoot (Rs 552 on Rs 25,000 - also in the v26 log) used to
  halve the food budget (normal -> shoestring). The solver now first trims its
  OWN 8% safety buffer, never below 4%.
- Weather label said "beyond the 16-day window" for a trip 10 days away when
  the forecast service failed; it now says the service did not answer.
- Foursquare: the legacy v3 API was retired on 15 May 2026 and old "fsq3" keys
  are refused by the new API (the HTTP 401 in the log). An fsq3 key now
  switches Foursquare off with a clear startup message; the default URL is the
  new API and the request fields match it (fsq_place_id, latitude/longitude).
- NOMINATIM_CONTACT must look like an email; otherwise the User-Agent says
  "not-configured" and the server warns at startup.
- Tests: 238 passing (offline suite now runs in ~8 s).
