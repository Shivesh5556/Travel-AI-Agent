import { useEffect, useRef, useState } from "react";
import { api } from "../api";

/*
  A plain text field for "leaving from" / "going to" cannot tell two real
  places with the same name apart - type "Goa" and the planner has to guess
  whether you mean the Indian state or a village somewhere else entirely.

  This component asks /api/geo/search as the user types (debounced, so we
  are not hammering the free geocoder on every keystroke) and shows a
  dropdown of real candidates - same idea as a flight-booking site's city
  picker: name, an airport code where we have one, and the state/country so
  two "Jaipur"s or a "Goa" vs "Genoa" are impossible to mix up.

  The field still works as free text: whatever is typed is what gets
  submitted, the dropdown is there to disambiguate, not to gate the input.
*/
export default function CityAutocomplete({ id, label, value, onChange, placeholder }) {
  const [query, setQuery] = useState(value || "");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef(null);
  const debounceRef = useRef(null);
  const requestIdRef = useRef(0);

  // Stay in sync if the parent resets the form (e.g. loading a saved trip).
  useEffect(() => {
    setQuery(value || "");
  }, [value]);

  useEffect(() => {
    function onClickOutside(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  useEffect(() => () => clearTimeout(debounceRef.current), []);

  function handleInput(text) {
    setQuery(text);
    onChange(text);
    setOpen(true);

    clearTimeout(debounceRef.current);
    const trimmed = text.trim();
    if (trimmed.length < 2) {
      setResults([]);
      setLoading(false);
      return;
    }

    const thisRequest = ++requestIdRef.current;
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.searchCities(trimmed);
        if (thisRequest === requestIdRef.current) {
          setResults(data.results || []);
        }
      } catch {
        if (thisRequest === requestIdRef.current) setResults([]);
      } finally {
        if (thisRequest === requestIdRef.current) setLoading(false);
      }
    }, 300);
  }

  function pick(candidate) {
    setQuery(candidate.name);
    onChange(candidate.name);
    setResults([]);
    setOpen(false);
  }

  const namesRepeat =
    results.length > 1 &&
    new Set(results.map((r) => r.name.trim().toLowerCase())).size < results.length;

  return (
    <div className="field city-autocomplete" ref={boxRef}>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        autoComplete="off"
        value={query}
        placeholder={placeholder}
        onChange={(e) => handleInput(e.target.value)}
        onFocus={() => query.trim().length >= 2 && setOpen(true)}
      />

      {open && (loading || results.length > 0) && (
        <div className="city-suggestions">
          {loading && (
            <div className="city-suggestion-loading">Searching…</div>
          )}
          {!loading &&
            results.map((r, i) => (
              <button
                type="button"
                key={`${r.name}-${r.lat}-${r.lon}-${i}`}
                className="city-suggestion"
                onClick={() => pick(r)}
              >
                <span className="city-suggestion-code">{r.iata || "—"}</span>
                <span className="city-suggestion-text">
                  <strong>{r.name}</strong>
                  {r.subtitle && <span>{r.subtitle}</span>}
                </span>
              </button>
            ))}
        </div>
      )}

      {!loading && namesRepeat && (
        <div className="hint city-suggestion-note">
          More than one place is called "{query.trim()}" — pick the right one
          above so the plan doesn't land somewhere else.
        </div>
      )}
    </div>
  );
}
