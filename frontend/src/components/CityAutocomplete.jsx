import { useEffect, useRef, useState } from "react";
import { api } from "../api";

// Keep the selected result intact: names alone cannot distinguish places.
export default function CityAutocomplete({ id, label, value, location, onChange, placeholder }) {
  const [query, setQuery] = useState(value || "");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(-1);
  const boxRef = useRef(null);
  const debounceRef = useRef(null);
  const requestIdRef = useRef(0);
  const listId = `${id}-suggestions`;

  useEffect(() => { setQuery(value || ""); }, [value]);

  useEffect(() => {
    const outside = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    return () => {
      document.removeEventListener("mousedown", outside);
      clearTimeout(debounceRef.current);
      requestIdRef.current += 1;
    };
  }, []);

  function handleInput(text) {
    setQuery(text);
    onChange(text, null);
    setOpen(true);
    setActive(-1);
    setResults([]);
    clearTimeout(debounceRef.current);
    const thisRequest = ++requestIdRef.current;
    const trimmed = text.trim();
    if (trimmed.length < 2) { setLoading(false); return; }
    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const data = await api.searchCities(trimmed);
        if (thisRequest === requestIdRef.current) setResults(data.results || []);
      } catch {
        if (thisRequest === requestIdRef.current) setResults([]);
      } finally {
        if (thisRequest === requestIdRef.current) setLoading(false);
      }
    }, 300);
  }

  function pick(candidate) {
    clearTimeout(debounceRef.current);
    requestIdRef.current += 1;
    setQuery(candidate.name);
    onChange(candidate.name, candidate);
    setResults([]);
    setLoading(false);
    setActive(-1);
    setOpen(false);
  }

  function keyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      setActive(-1);
    } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && results.length) {
      event.preventDefault();
      setOpen(true);
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => current < 0 ? (delta > 0 ? 0 : results.length - 1) : (current + delta + results.length) % results.length);
    } else if (event.key === "Enter" && open && active >= 0 && results[active]) {
      event.preventDefault();
      pick(results[active]);
    }
  }

  return (
    <div className="field city-autocomplete" ref={boxRef}>
      <label htmlFor={id}>{label}</label>
      <input
        id={id} autoComplete="off" value={query} placeholder={placeholder}
        role="combobox" aria-autocomplete="list" aria-expanded={open && results.length > 0}
        aria-controls={listId}
        aria-activedescendant={open && active >= 0 ? `${id}-option-${active}` : undefined}
        aria-describedby={location ? `${id}-location` : undefined}
        onChange={(e) => handleInput(e.target.value)} onKeyDown={keyDown}
        onFocus={() => query.trim().length >= 2 && setOpen(true)}
        onBlur={(event) => { if (!boxRef.current?.contains(event.relatedTarget)) setOpen(false); }}
      />
      {location && <span className="hint" id={`${id}-location`}>{location.subtitle || [location.state, location.country].filter(Boolean).join(", ") || "India"}</span>}
      {open && (loading || results.length > 0) && (
        <div className="city-suggestions" id={listId} role="listbox" aria-label={`${label} suggestions`}>
          {loading && <div className="city-suggestion-loading" role="status">Searching…</div>}
          {!loading && results.map((r, i) => (
            <button type="button" tabIndex={-1} role="option" aria-selected={active === i}
              id={`${id}-option-${i}`} key={r.id || `${r.name}-${r.lat}-${r.lon}-${i}`}
              className={`city-suggestion ${active === i ? "active" : ""}`}
              onMouseDown={(e) => e.preventDefault()} onClick={() => pick(r)}>
              <span className="city-suggestion-code">{r.iata || "—"}</span>
              <span className="city-suggestion-text"><strong>{r.name}</strong>{r.subtitle && <span>{r.subtitle}</span>}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
