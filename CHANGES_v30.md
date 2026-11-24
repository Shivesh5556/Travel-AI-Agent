# Ghumi Ghumi v30

This update addresses the code-review findings while keeping the existing
React/FastAPI design. Setup is documented in `RUN_IN_VSCODE.md`.

## Budget and itinerary correctness

- Check available transport/stay/spending combinations instead of stopping
  before the affordable option; recompute totals from the selected combination.
- Refuse a successful itinerary when no complete estimate fits the budget.
- Preserve explicit exclusions for curated places, maximum daily stops,
  opening weekdays and date-specific events through repairs and balancing.
- Include lunch, waiting, drive time and the return to the stay in capacity
  checks. Morning-only windows can omit lunch; multi-day outbound travel blocks
  all days before arrival.
- Apply a deterministic final validator before presenting a successful plan.
- Prefer useful, feasible days over mechanically identical activity counts.
- Validate manual edits; prevent city/day-trip crossover using stale road
  assumptions. Replace an unrelated edited lunch pick with a clear meal break.

## Geography and providers

- Retain exact selected locations through parallel tool calls and geocoding.
- Distinguish same-name cities, reject invalid/unknown locations, and restrict
  suggestions to India. Unresolved names never become central India/Delhi.
- Remove mainland train/bus options to island destinations; limit rail
  estimates to supported railheads. Remaining services are explicitly unverified.
- Mark the doubled return fare as estimated, even when outbound has a provider
  quote. Return search remains a future integration.
- Preserve near-term forecasts for trips extending past the forecast horizon.
  Seasonal estimates use each date's month.
- Persist weather changes including rain clearing; base swap suggestions on
  fresh weather, compatible dates and complete day capacity.

## Private access and bounded work

- Replace the public owner header with a random HttpOnly/SameSite browser
  cookie; store only its hash. Require ownership for reads, streams, monitoring,
  deletion and checkout. Older header-owned trips cannot be claimed.
- Use full random UUID identifiers. Public trip-sharing is not provided.
- Apply independent IP/session limits, a concurrent-job bound, translation
  size limits and idempotent create requests. Reject duplicate keys with changed
  request bodies. These controls are process-local.
- Enforce Razorpay test keys at startup and payment operations. No bookings
  are created. Admin endpoints require an explicitly configured token.
- Avoid logging database/Redis credentials.

## Interface and setup

- Prevent duplicate submission while the creation request is pending.
- Restore a retained trip on same-tab refresh; explicitly delete it when
  choosing Plan another trip. Default server retention remains six hours.
- Print every itinerary day, including current edits, instead of only the
  selected tab. Edits themselves remain in memory and reset on refresh.
- Add keyboard navigation for city suggestions and day tabs, and Hindi text
  for new labels. Existing translation coverage is not claimed complete.
- Clearly label estimated totals, return fares and per-day weather provenance.
- Fix the Compose proxy address, use locked frontend dependency installation,
  add VS Code run/test settings, and add GitHub Actions checks.

## Validation and remaining limits

See `VALIDATION.md` for checks actually executed and unavailable checks.
Live provider integration, an end-to-end browser run, real booking inventory,
verified rail/bus timetables, real return-fare search and multi-worker durable
jobs are not implemented or certified by this update. Install dependencies
and run the supplied full checks on your machine before deployment.
