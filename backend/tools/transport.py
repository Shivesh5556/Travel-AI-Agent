"""
Getting there: flights, trains and buses.

FLIGHTS
    Real data from Duffel's sandbox if you put DUFFEL_API_KEY in .env.
    (We use Duffel because Amadeus shut its free self-service portal down on
    17 July 2026, so every tutorial you find online is now out of date.)
    Duffel's sandbox returns real API responses with test inventory, which is
    the same integration you would ship to production.

    Without a key we fall back to a distance-based fare model. It is an
    estimate and we say so on every single result.

TRAINS AND BUSES
    India has no free, legal, reliable train API. IRCTC does not publish one.
    So these are honest estimates from distance and standard per-km fares.
    They exist because the budget solver needs a cheaper option to switch to
    when flights blow the budget - that swap is the most interesting thing
    this project does.
"""

import httpx

import config
import metrics
from tools import geo

DUFFEL_URL = "https://api.duffel.com/air/offer_requests"

# Rough Indian fares per km, in rupees.
FARE_PER_KM = {
    "train_sleeper": 0.55,
    "train_3ac": 1.35,
    "bus_seater": 1.15,
    "bus_sleeper": 1.60,
    "flight_estimate": 5.20,   # only used when we have no Duffel key
}

AVG_SPEED_KMH = {"train": 55, "bus": 42, "flight": 700}

# Below this distance flying makes no sense once you add airport time.
MIN_FLIGHT_DISTANCE_KM = 350

# Duffel prices each offer in the airline's own currency, NOT in rupees.
# Its sandbox airline quotes in GBP, so a "168" came out looking like a
# Rs 168 flight from Pune to Goa - which is obviously wrong and would have
# been embarrassing to leave in. We convert everything to rupees here.
#
# These are rough static rates. A production app would fetch live FX; for a
# college project a static table that is honest about being static is fine.
TO_INR = {
    "INR": 1.0,
    "GBP": 112.0,
    "USD": 88.0,
    "EUR": 96.0,
    "AED": 24.0,
    "SGD": 65.0,
}


def _hours_to_text(hours: float) -> str:
    h = int(hours)
    m = int(round((hours - h) * 60))
    return f"{h}h {m:02d}m"


async def search_flights(origin_city: str, destination_city: str, depart_date: str,
                         passengers: int = 1) -> list[dict]:
    """
    Flight options for the route, cheapest first.
    Every result carries a "source" field so the UI can show where it came from.
    """
    origin = await geo.geocode(origin_city)
    destination = await geo.geocode(destination_city)
    distance = geo.haversine_km(origin["lat"], origin["lon"], destination["lat"], destination["lon"])

    if distance < MIN_FLIGHT_DISTANCE_KM:
        # Being useful sometimes means saying "do not do this".
        return []

    if config.DUFFEL_ENABLED:
        offers = await _duffel_search(origin["iata"], destination["iata"], depart_date, passengers)
        if offers:
            return offers

        # Duffel is configured but had nothing for this route on this date.
        # In strict mode we say so instead of inventing a fare - a made-up
        # number that LOOKS real is worse than no number at all.
        if config.STRICT_MODE:
            print(f"[transport] Duffel has no {origin['iata']}-{destination['iata']} "
                  f"inventory for {depart_date}. Returning no flights rather than a guess.")
            return []

        print("[transport] Duffel returned nothing, using the estimate model instead")

    if config.STRICT_MODE:
        return []

    return _estimate_flights(origin, destination, distance, passengers)


async def _duffel_search(origin_iata: str, destination_iata: str, depart_date: str,
                         passengers: int) -> list[dict]:
    """Real call to Duffel. Returns [] on any failure so the caller can fall back."""
    headers = {
        "Authorization": f"Bearer {config.DUFFEL_API_KEY}",
        "Duffel-Version": "v2",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "data": {
            "slices": [{
                "origin": origin_iata,
                "destination": destination_iata,
                "departure_date": depart_date,
            }],
            "passengers": [{"type": "adult"} for _ in range(passengers)],
            "cabin_class": "economy",
        }
    }

    try:
        with metrics.Timer("duffel_flight_search"):
            async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
                response = await client.post(
                    DUFFEL_URL,
                    params={"return_offers": "true"},
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
    except Exception as e:
        print(f"[transport] Duffel call failed ({type(e).__name__})")
        return []

    offers = []
    for offer in data.get("data", {}).get("offers", [])[:8]:
        slices = offer.get("slices", [])
        if not slices:
            continue
        segments = slices[0].get("segments", [])
        carrier = offer.get("owner", {}).get("name", "Unknown airline")

        currency = offer.get("total_currency", "INR")
        raw_amount = float(offer.get("total_amount", 0))

        rate = TO_INR.get(currency)
        if rate is None:
            # Unknown currency - better to skip the offer than to show a
            # number that means nothing.
            print(f"[transport] skipping a Duffel offer priced in {currency}")
            continue

        offers.append({
            "mode": "flight",
            "provider": "Duffel",
            "carrier": carrier,
            "code": segments[0].get("marketing_carrier_flight_number", "") if segments else "",
            "depart": segments[0].get("departing_at", "")[11:16] if segments else "",
            "arrive": segments[-1].get("arriving_at", "")[11:16] if segments else "",
            "duration": _readable_duration(slices[0].get("duration", "")),
            "stops": "Non-stop" if len(segments) <= 1 else f"{len(segments) - 1} stop",
            "total_inr": int(round(raw_amount * rate)),
            "currency": "INR",
            "original_amount": raw_amount,
            "original_currency": currency,
            "price_is_estimate": False,
            "source": (f"duffel sandbox (real API, test inventory - "
                       f"{currency} {raw_amount:,.0f} converted at {rate}/INR)"),
        })

    offers.sort(key=lambda o: o["total_inr"])
    return offers


def _estimate_flights(origin: dict, destination: dict, distance: float, passengers: int) -> list[dict]:
    """
    No Duffel key: build three plausible options from distance.
    Clearly marked as estimates everywhere.
    """
    base = distance * FARE_PER_KM["flight_estimate"]
    hours = distance / AVG_SPEED_KMH["flight"] + 1.2   # +1.2h for taxi, take-off, landing

    tiers = [
        ("Early morning departure", 0.85, "06:10", "Non-stop"),
        ("Midday departure", 1.00, "13:45", "Non-stop"),
        ("Evening departure", 1.18, "19:30", "1 stop"),
    ]

    options = []
    for label, multiplier, depart, stops in tiers:
        per_person = int(round(base * multiplier / 100) * 100)
        options.append({
            "mode": "flight",
            "provider": "estimate",
            "carrier": label,
            "code": "",
            "depart": depart,
            "arrive": "",
            "duration": _hours_to_text(hours if stops == "Non-stop" else hours + 2),
            "stops": stops,
            "per_person_inr": per_person,
            "total_inr": per_person * passengers,
            "currency": "INR",
            "price_is_estimate": True,
            "source": f"estimated from {int(distance)}km (add DUFFEL_API_KEY for real fares)",
        })

    options.sort(key=lambda o: o["total_inr"])
    return options


async def search_ground_transport(origin_city: str, destination_city: str,
                                  passengers: int = 1) -> list[dict]:
    """
    Trains and buses. These are the cheaper options the budget solver switches
    to when the flight does not fit. All fares are distance estimates.
    """
    origin = await geo.geocode(origin_city)
    destination = await geo.geocode(destination_city)
    distance = geo.haversine_km(origin["lat"], origin["lon"], destination["lat"], destination["lon"])

    # Roads and railway lines are never straight, so add 25% to the crow-flies
    # distance. This is a standard rule of thumb.
    road_distance = distance * 1.25

    options = []
    for key, mode, label in [
        ("train_sleeper", "train", "Train (Sleeper class)"),
        ("train_3ac", "train", "Train (3A class)"),
        ("bus_seater", "bus", "State/private bus (seater)"),
        ("bus_sleeper", "bus", "Private bus (sleeper)"),
    ]:
        per_person = int(round(road_distance * FARE_PER_KM[key] / 10) * 10)
        hours = road_distance / AVG_SPEED_KMH[mode]

        options.append({
            "mode": mode,
            "provider": "estimate",
            "carrier": label,
            "code": "",
            "depart": "",
            "arrive": "",
            "duration": _hours_to_text(hours),
            "stops": "",
            "per_person_inr": per_person,
            "total_inr": per_person * passengers,
            "currency": "INR",
            "distance_km": int(road_distance),
            "price_is_estimate": True,
            "source": f"estimated from {int(road_distance)}km road/rail distance",
        })

    options.sort(key=lambda o: o["total_inr"])
    return options


async def all_transport_options(origin_city: str, destination_city: str, depart_date: str,
                                passengers: int = 1) -> list[dict]:
    """Everything, cheapest first. This is what the budget solver works with."""
    flights = await search_flights(origin_city, destination_city, depart_date, passengers)
    ground = await search_ground_transport(origin_city, destination_city, passengers)
    combined = flights + ground
    combined.sort(key=lambda o: o["total_inr"])
    return combined


def _readable_duration(iso_duration: str) -> str:
    """
    Duffel sends durations as ISO 8601, like "PT2H15M". Nobody wants to read
    that on a booking page, so turn it into "2h 15m".
    """
    if not iso_duration.startswith("PT"):
        return iso_duration

    import re
    hours = re.search(r"(\d+)H", iso_duration)
    minutes = re.search(r"(\d+)M", iso_duration)

    h = int(hours.group(1)) if hours else 0
    m = int(minutes.group(1)) if minutes else 0
    return f"{h}h {m:02d}m"
