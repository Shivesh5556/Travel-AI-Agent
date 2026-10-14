# v23

Goal: make Ghumi Ghumi recommend places actually worth the traveller's time, inside
their budget, and show why. Built from the analysis document plus a code review.

## Adopted from the analysis document
- Structured 0-100 attraction score (interest, popularity, significance, agreement,
  type, route, season) - **budget fit added** (our USP).
- Ranking before routing: shortlist by quality, then cluster, then order.
- Stronger negative filters; famous markets treated as notable, generic ones not.
- Evidence + confidence + "why Ghumi picked this" for stops and stays.
- Hotels ranked on budget / quality / location instead of cheapest-first, with
  several alternatives.
- Foursquare as an OPTIONAL quality layer (rating, popularity, price tier).
- LLM only writes up choices the code made.

## Deliberately not adopted
- **Opening-hours / practicality score (5 pts)**: we have no reliable opening-hours
  data, and a made-up number would break the "nothing is invented" rule. Those
  points went to budget fit.
- **Live hotel inventory (Booking.com Demand API)**: needs a commercial contract.
  Kept as a future optional provider; prices stay labelled as estimates.
- **Making Foursquare required**: it is not free-without-card, so the free path
  (OSM + Geoapify + Wikipedia + Wikivoyage + curated) stays complete.

## Added beyond the document
- Budget-aware ranking: place cost tiers vs spending level; budget-style trips keep
  the saving, balanced/comfort trips use spare money (solver "upgrade" rounds).
- "Worth stretching for" stay suggestion with the honest extra cost.
- Style-dependent stay weights and absolute price targets, so a large budget does
  not turn a budget trip into a resort stay.
- Curated-list order used as a headline-rank signal; Must-see requires evidence.
- Second ranking pass after the solver knows the hotel, spend level and forecast.

## Bugs fixed
- Stay ceiling of 0 was treated as "no limit" (could pick an expensive stay).
- Critic crashed when a stay had no `area`.
- replan_rounds counted upgrade rounds as re-plans.

## Tests
158 passing (120 before). New: tests/test_recommend.py.
