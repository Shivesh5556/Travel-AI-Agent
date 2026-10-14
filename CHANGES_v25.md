# v25

- Even days: `dayplanner.even_out` levels the days (visit time + the drive out
  and back), `fill_light_days` tops up light days from the reserve, and a day
  has at most 5 stops by default (6 for "packed", 3 for "relaxed" / limited
  mobility). Busiest-vs-lightest gap across 48 test trips: 1.4 h on average.
- Transport is shown as the return fare for the whole group, with the
  per-person figure (`return_total_inr`, `return_per_person_inr` in the plan).
- View-once trips: removed "My trips", the list endpoint, share links and
  `?trip=` URLs. The browser deletes its trip on "Plan another trip",
  tab close or refresh (`fetch(..., {keepalive: true})`); the monitor deletes
  anything older than `TRIP_TTL_HOURS` (default 6). Itinerary edits and
  checklist ticks are no longer stored in localStorage.
- Plan page: removed the planner-trace panel and the planning rounds / style /
  time-to-plan / token-cost boxes.
- Tests: 216 passing (new: level days, stop cap, view-once delete, purge,
  return fare).
