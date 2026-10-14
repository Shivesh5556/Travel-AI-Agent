"""
Weather from Open-Meteo. Free, keyless, and genuinely real data.

Two cases:
  - trip is within the next ~16 days -> real daily forecast
  - trip is further out              -> we return a seasonal estimate and
                                        clearly mark it as an estimate

The critic agent later reads this to catch things like "you planned a trek
on the one day it rains".
"""

from datetime import date, datetime, timedelta

import config
from tools import webclient

# Open-Meteo returns a WMO weather code. This maps it to something readable.
WEATHER_CODES = {
    0: "Clear sky", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Heavy showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Severe thunderstorm",
}

# Anything at or above this code means "keep the plan indoors that day".
RAIN_CODES = {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99}

# Rough monthly averages for the Indian plains, used when the trip is too far
# in the future for a real forecast. Marked as an estimate in the output.
SEASONAL_HINT = {
    1: ("Cool and dry", 8, 22), 2: ("Pleasant", 11, 26), 3: ("Warming up", 16, 32),
    4: ("Hot", 22, 38), 5: ("Very hot", 26, 41), 6: ("Hot, monsoon starting", 27, 38),
    7: ("Monsoon, heavy rain likely", 25, 33), 8: ("Monsoon, humid", 25, 32),
    9: ("Monsoon ending, humid", 24, 33), 10: ("Pleasant, post monsoon", 19, 32),
    11: ("Cool and clear", 13, 28), 12: ("Cold and dry", 9, 24),
}


async def get_forecast(lat: float, lon: float, start_date: str, end_date: str) -> dict:
    """
    Daily weather for the trip.

    Returns {"days": [...], "is_real_forecast": bool, "source": str,
             "rainy_days": ["2026-09-15", ...]}

    Three tiers, best first:
      1. trip within 16 days       -> real Open-Meteo forecast
      2. further out               -> what the SAME dates were actually like in
                                      the last three years (Open-Meteo archive)
      3. archive unreachable       -> a regional monthly table (v24: India is not
                                      one climate - Leh has no monsoon, Chennai's
                                      rains come in Oct-Dec, Goa's in Jun-Sep)
    """
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days_away = (start - date.today()).days

    # Open-Meteo only forecasts about 16 days ahead.
    if 0 <= days_away <= 16:
        data = await webclient.get_json(
            config.OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "Asia/Kolkata",
                "start_date": start_date,
                "end_date": end_date,
            },
            label="open_meteo_forecast",
            ttl=60 * 60 * 3,   # weather changes, only cache for 3 hours
        )

        if data and "daily" in data:
            return _parse_open_meteo(data)
        why = "the forecast service did not answer"
    else:
        why = "the trip is beyond the 16-day forecast window"

    climate = await _climate_from_history(lat, lon, start, end)
    if climate:
        climate["source"] = climate["source"].split(" - ")[0] + f" - {why}"
        return climate

    # No forecast and no archive -> regional seasonal estimate
    estimate = _seasonal_estimate(start, end, lat, lon)
    estimate["source"] = estimate["source"].split(" - ")[0] + f" - {why}"
    return estimate


def _same_day_in(year: int, d: date) -> date:
    try:
        return d.replace(year=year)
    except ValueError:          # 29 February
        return d.replace(year=year, day=28)


async def _climate_from_history(lat: float, lon: float, start: date, end: date,
                                years: int = 3) -> dict | None:
    """What these calendar dates were really like in each of the last `years` years."""
    this_year = date.today().year
    per_day: dict[int, list[dict]] = {}
    used_years = []
    for back in range(1, years + 1):
        y_start = _same_day_in(this_year - back, start)      # always a finished year
        y_end = y_start + (end - start)
        data = await webclient.get_json(
            config.OPEN_METEO_ARCHIVE_URL,
            params={"latitude": round(lat, 2), "longitude": round(lon, 2),
                    "start_date": y_start.isoformat(), "end_date": y_end.isoformat(),
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                    "timezone": "Asia/Kolkata"},
            label="open_meteo_archive",
            ttl=60 * 60 * 24 * 30,
        )
        daily = (data or {}).get("daily") or {}
        times = daily.get("time") or []
        if not times:
            continue
        used_years.append(y_start.year)
        for i in range(len(times)):
            per_day.setdefault(i, []).append({
                "high": (daily.get("temperature_2m_max") or [None] * len(times))[i],
                "low": (daily.get("temperature_2m_min") or [None] * len(times))[i],
                "rain": (daily.get("precipitation_sum") or [None] * len(times))[i],
            })
    if len(used_years) < 2:
        return None

    days, rainy = [], []
    current, i = start, 0
    while current <= end:
        rows = per_day.get(i, [])
        highs = [r["high"] for r in rows if r["high"] is not None]
        lows = [r["low"] for r in rows if r["low"] is not None]
        rains = [r["rain"] for r in rows if r["rain"] is not None]
        wet = sum(1 for r in rains if r >= 2.5)
        chance = round(100 * wet / len(rains)) if rains else None
        is_rainy = chance is not None and chance >= 50
        high = round(sum(highs) / len(highs)) if highs else None
        low = round(sum(lows) / len(lows)) if lows else None
        if is_rainy:
            desc = "Often rainy on these dates"
        elif chance is not None and chance >= 25:
            desc = "Sometimes showery on these dates"
        elif high is not None and high >= 38:
            desc = "Usually very hot"
        elif low is not None and low <= 5:
            desc = "Usually cold"
        else:
            desc = "Usually dry"
        days.append({
            "date": current.isoformat(),
            "description": desc + " (past years, not a forecast)",
            "low_c": low, "high_c": high,
            "rain_chance": chance,
            "is_rainy": is_rainy,
        })
        if is_rainy:
            rainy.append(current.isoformat())
        current += timedelta(days=1)
        i += 1

    years_text = f"{min(used_years)}-{max(used_years)}"
    return {
        "days": days,
        "rainy_days": rainy,
        "is_real_forecast": False,
        "source": f"climate of the same dates in {years_text} (open-meteo archive) - "
                  f"the trip is beyond the 16-day forecast window",
    }


# Share of days with real rain, by region and month (1-12). Rough, public
# climate-normal level - only used when the archive cannot be reached.
REGION_RAIN = {
    "plains":    [5, 5, 5, 5, 10, 35, 60, 55, 35, 8, 3, 3],
    "hills":     [10, 12, 12, 10, 15, 40, 75, 75, 45, 8, 5, 8],
    "ladakh":    [5, 5, 5, 5, 5, 5, 10, 10, 5, 3, 3, 5],
    "northeast": [5, 8, 15, 30, 45, 65, 75, 70, 55, 20, 5, 3],
    "southeast": [15, 5, 5, 5, 10, 15, 20, 25, 30, 45, 55, 35],
    "westcoast": [2, 2, 2, 5, 20, 75, 85, 75, 50, 25, 10, 3],
    "desert":    [2, 2, 2, 2, 5, 15, 35, 30, 15, 2, 1, 1],
}
# Temperature offset from the plains table, (low, high).
REGION_TEMP_SHIFT = {
    "plains": (0, 0), "hills": (-9, -10), "ladakh": (-18, -14), "northeast": (-6, -8),
    "southeast": (10, -2), "westcoast": (8, -4), "desert": (-1, 2),
}


def region_for(lat: float | None, lon: float | None) -> str:
    """Very coarse climate region for a point in India."""
    if lat is None or lon is None:
        return "plains"
    if lat >= 33.0:
        return "ladakh"
    if lon >= 88.0 and lat >= 26.0:
        return "northeast"
    if lat >= 29.5 and lon <= 81.0:
        return "hills"
    if lat <= 15.5 and lon >= 77.8:
        return "southeast"
    if lon <= 76.5 and lat <= 20.5:
        return "westcoast"
    if 23.0 <= lat <= 30.0 and lon <= 75.0:
        return "desert"
    return "plains"


def _seasonal_estimate(start: date, end: date, lat: float | None = None,
                       lon: float | None = None) -> dict:
    region = region_for(lat, lon)
    chance = REGION_RAIN[region][start.month - 1]
    rainy_month = chance >= 50
    description, low, high = SEASONAL_HINT[start.month]
    d_low, d_high = REGION_TEMP_SHIFT[region]
    if region != "plains":
        description = ("Rainy season, showers likely" if rainy_month
                       else "Mostly dry" if chance <= 10 else "Some showers possible")
    low, high = low + d_low, high + d_high

    days = []
    current = start
    while current <= end:
        days.append({
            "date": current.isoformat(),
            "description": description + " (seasonal average, not a forecast)",
            "low_c": low,
            "high_c": high,
            "rain_chance": chance,
            "is_rainy": rainy_month,
        })
        current += timedelta(days=1)

    return {
        "days": days,
        "rainy_days": [d["date"] for d in days] if rainy_month else [],
        "is_real_forecast": False,
        "source": f"seasonal average for this region ({region}) - the trip is beyond the "
                  f"16-day forecast window",
    }


def _parse_open_meteo(data: dict) -> dict:
    daily = data["daily"]
    days = []
    rainy = []

    for i, day in enumerate(daily["time"]):
        code = daily["weathercode"][i]
        description = WEATHER_CODES.get(code, "Unknown")
        is_rainy = code in RAIN_CODES

        if is_rainy:
            rainy.append(day)

        days.append({
            "date": day,
            "description": description,
            "low_c": daily["temperature_2m_min"][i],
            "high_c": daily["temperature_2m_max"][i],
            "rain_chance": daily.get("precipitation_probability_max", [None] * 20)[i],
            "is_rainy": is_rainy,
        })

    return {
        "days": days,
        "rainy_days": rainy,
        "is_real_forecast": True,
        "source": "open-meteo (real forecast)",
    }


