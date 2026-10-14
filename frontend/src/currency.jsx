import { createContext, useContext, useMemo, useState } from "react";

/*
  Display currency.

  Inspired by trip-planner.in, which lets you flip a whole budget between
  currencies. The plan itself is always computed in rupees - this only
  changes how numbers are SHOWN. The rates below are rough, fixed, indicative
  values (1 INR = x units). They are not live. Edit them here if you want to
  keep them current; the UI labels any non-rupee view as "approx.".
*/
export const CURRENCIES = {
  INR: { symbol: "₹", rate: 1, locale: "en-IN", label: "INR ₹" },
  USD: { symbol: "$", rate: 0.0113, locale: "en-US", label: "USD $" },
  EUR: { symbol: "€", rate: 0.0097, locale: "de-DE", label: "EUR €" },
  GBP: { symbol: "£", rate: 0.0084, locale: "en-GB", label: "GBP £" },
  AED: { symbol: "AED ", rate: 0.0415, locale: "en-AE", label: "AED" },
  SGD: { symbol: "S$", rate: 0.0146, locale: "en-SG", label: "SGD S$" },
};

const CurrencyContext = createContext({
  code: "INR",
  setCode: () => {},
  money: (n) => "₹" + Math.round(n || 0).toLocaleString("en-IN"),
  approx: false,
});

export function CurrencyProvider({ children }) {
  const [code, setCodeState] = useState(() => {
    try {
      const saved = localStorage.getItem("gg-currency");
      return saved && CURRENCIES[saved] ? saved : "INR";
    } catch {
      return "INR";
    }
  });

  const value = useMemo(() => {
    const cfg = CURRENCIES[code];
    return {
      code,
      approx: code !== "INR",
      setCode: (c) => {
        setCodeState(c);
        try {
          localStorage.setItem("gg-currency", c);
        } catch {
          /* private mode - ignore */
        }
      },
      money: (amountInr) => {
        if (amountInr === null || amountInr === undefined || Number.isNaN(amountInr)) return "—";
        const converted = amountInr * cfg.rate;
        return cfg.symbol + Math.round(converted).toLocaleString(cfg.locale);
      },
    };
  }, [code]);

  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>;
}

export const useMoney = () => useContext(CurrencyContext);

export function CurrencyPicker() {
  const { code, setCode } = useMoney();
  return (
    <label className="currency-picker">
      <span>Show prices in</span>
      <select value={code} onChange={(e) => setCode(e.target.value)} aria-label="Display currency">
        {Object.entries(CURRENCIES).map(([c, cfg]) => (
          <option key={c} value={c}>
            {cfg.label}
          </option>
        ))}
      </select>
    </label>
  );
}
