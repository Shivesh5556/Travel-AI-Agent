# v22

- Language dropdown (English / हिन्दी) at the top right. Fixed labels come from a
  built-in dictionary (frontend/src/i18n.hi.js); trip text (day descriptions, tips,
  warnings, weather, budget lines, packing list) is translated by POST /api/translate
  using the same Groq model, cached, and falls back to English if the model is busy.
- Recommendations are now ranked (backend/tools/places.py: score_place / rank_places).
  Wholesale markets, bus stands, workshops are dropped ("Mandir" is not mistaken for
  "Mandi"). Famous sights, Wikipedia/Wikivoyage-backed places, live events and places
  several sources agree on rank first. Best stops are kept when a day is full, and
  headline places get a "Must-see" badge.
- Hand-picked landmarks are always added for known cities; Dehradun now includes a
  Mussoorie day trip, Jaipur/Delhi/Rishikesh lists are longer.
- City search no longer dies on Nominatim 403: set NOMINATIM_CONTACT in .env, and
  Geoapify autocomplete is used as a fallback.
