import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { HI } from "./i18n.hi.js";

/*
  Language support (English / Hindi).

  Two kinds of text, handled differently on purpose:

  1. FIXED LABELS (buttons, headings, form fields) - t("English text").
     Looked up in a hand-written dictionary (i18n.hi.js). Instant, free, and
     a missing entry just shows English.

  2. PLAN CONTENT that differs per trip (day descriptions, tips, warnings,
     budget line names) - useTranslated([...texts]). Sent to /api/translate in
     one batch, translated by the same Groq model, cached on the server and in
     this tab. Place names, numbers and rupee amounts are left alone.

  The chosen language is remembered in this browser.
*/

export const LANGS = [
  { code: "en", label: "English" },
  { code: "hi", label: "हिन्दी" },
];

const DICTS = { hi: HI };

function fill(text, vars) {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? vars[k] : m));
}

const LangContext = createContext({
  lang: "en",
  setLang: () => {},
  t: (key, vars) => fill(key, vars),
});

export function LanguageProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    try {
      const saved = localStorage.getItem("gg-lang");
      return LANGS.some((l) => l.code === saved) ? saved : "en";
    } catch {
      return "en";
    }
  });

  const setLang = useCallback((code) => {
    setLangState(code);
    try {
      localStorage.setItem("gg-lang", code);
    } catch {
      /* private mode - ignore */
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const value = useMemo(
    () => ({
      lang,
      setLang,
      t: (key, vars) => fill(DICTS[lang]?.[key] ?? key, vars),
    }),
    [lang, setLang]
  );

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export const useI18n = () => useContext(LangContext);

/* The dropdown in the top-right of the header. */
export function LanguageSwitcher() {
  const { lang, setLang, t } = useI18n();
  return (
    <label className="lang-picker" title={t("Language")}>
      <span aria-hidden="true">🌐</span>
      <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Language / भाषा">
        {LANGS.map((l) => (
          <option key={l.code} value={l.code}>
            {l.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/* ---------------- dynamic (per-trip) text ---------------- */

// "hi\0some english sentence" -> Hindi. Lives for the life of the tab, so
// switching language back and forth never asks the server twice.
const memo = new Map();
const keyOf = (lang, text) => lang + "\u0000" + text;

export const TrContext = createContext((text) => text);
export const useTr = () => useContext(TrContext);

/*
  useTranslated(texts) -> { tr, status, retry }

  tr(text)  the translation if we have it, otherwise the original (so the
            page is never blank while translating).
  status    "idle" | "loading" | "partial" (model busy, some text still English)
            | "error" (could not reach the server)
  retry()   try again.
*/
export function useTranslated(texts) {
  const { lang } = useI18n();
  const [version, setVersion] = useState(0);
  const [status, setStatus] = useState("idle");
  const [attempt, setAttempt] = useState(0);

  const signature = JSON.stringify(texts);
  const unique = useMemo(
    () => [...new Set(texts.filter((x) => typeof x === "string" && x.trim().length > 0))],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [signature]
  );

  useEffect(() => {
    if (lang === "en") {
      setStatus("idle");
      return undefined;
    }
    const dict = DICTS[lang] || {};
    const missing = unique.filter((x) => !(x in dict) && !memo.has(keyOf(lang, x)));
    if (missing.length === 0) {
      setStatus("idle");
      return undefined;
    }

    let cancelled = false;
    setStatus("loading");

    (async () => {
      let allGood = true;
      for (let i = 0; i < missing.length; i += 40) {
        const chunk = missing.slice(i, i + 40);
        try {
          const res = await api.translate(lang, chunk);
          chunk.forEach((source, k) => {
            const out = res.translations?.[k];
            // Only remember real translations. If the model was busy the
            // server returns English for that item - leave it unremembered so
            // "Retry" can fetch it properly.
            if (out && (out !== source || res.complete)) memo.set(keyOf(lang, source), out);
          });
          if (!res.complete) allGood = false;
        } catch {
          if (!cancelled) setStatus("error");
          return;
        }
        if (cancelled) return;
        setVersion((v) => v + 1);
      }
      if (!cancelled) setStatus(allGood ? "idle" : "partial");
    })();

    return () => {
      cancelled = true;
    };
  }, [lang, unique, attempt]);

  const tr = useCallback(
    (text) => {
      if (lang === "en" || !text) return text;
      const fixed = DICTS[lang]?.[text];
      if (fixed) return fixed;
      return memo.get(keyOf(lang, text)) ?? text;
    },
    // version makes consumers re-render as translations arrive
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [lang, version]
  );

  return { tr, status, retry: () => setAttempt((n) => n + 1) };
}

/* Small banner shown while translating, or when part of it did not work. */
export function TranslationNotice({ status, retry }) {
  const { lang } = useI18n();
  if (lang === "en" || status === "idle") return null;
  return (
    <div className={`tr-notice ${status}`} role="status">
      {status === "loading" && <span>हिन्दी में बदल रहा है… / Translating to Hindi…</span>}
      {(status === "partial" || status === "error") && (
        <span>
          कुछ हिस्सा अभी हिन्दी में नहीं बदल पाया, अंग्रेज़ी दिख रही है। / Some text could not be translated right now.{" "}
          <button className="link-btn" onClick={retry}>फिर कोशिश करें / Retry</button>
        </span>
      )}
    </div>
  );
}
