"""
The agent's toolbox.

Two things live here:

  TOOL_SCHEMAS - the JSON descriptions we send to the LLM. This is how the
                 model knows what it is allowed to do. The description text
                 matters a lot: it is literally the documentation the model
                 reads before choosing.

  run_tool()   - takes the name and arguments the model asked for and
                 actually runs the Python function.

This separation is what makes it an agent. The old version of this project
had a fixed asyncio.gather() - the order was decided by us, at coding time.
Here the model decides at runtime, and it can skip tools it does not need.
"""

from rag.retriever import retriever
from tools import budget as budget_tool
from tools import geo, places, stays, transport, weather

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the daily weather for a city between two dates. Returns a real forecast if the trip is within 16 days, otherwise a seasonal estimate. Call this early, because rain changes which activities make sense.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Destination city name"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["city", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_transport",
            "description": "Find every way to get from one city to another: flights (real fares if a Duffel key is configured), plus train and bus estimates. Returns options sorted cheapest first. Use this before deciding a hotel budget, since travel is usually the biggest single cost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin_city": {"type": "string"},
                    "destination_city": {"type": "string"},
                    "depart_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "passengers": {"type": "integer"},
                },
                "required": ["origin_city", "destination_city", "depart_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_stays",
            "description": "Find real, named hotels, hostels and guesthouses near a city (Geoapify + OpenStreetMap). Results are ranked best-first (budget fit, quality, location), each with a 'why'. Prices are estimated, not live rates. Pass max_price_per_night if the budget is tight so you only get affordable options.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "nights": {"type": "integer"},
                    "max_price_per_night": {"type": "integer", "description": "Optional rupee ceiling per night"},
                },
                "required": ["city", "nights"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_attractions",
            "description": "Find real places to visit near a city, filtered by the traveller's interests. Every result has real coordinates, which the day planner needs to group them into sensible days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "interests": {"type": "array", "items": {"type": "string"}},
                    "num_days": {"type": "integer", "description": "Trip length, so enough places are returned to fill every day"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_restaurants",
            "description": "Find real restaurants and cafes near a city, used to attach a lunch stop to each day.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_local_tips",
            "description": "Search a library of tips written by travellers for advice you will not find on a booking site: cheaper alternatives, quieter viewpoints, timing warnings. Use the traveller's interests as the query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "query": {"type": "string", "description": "What the traveller cares about"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_budget",
            "description": "Add up a full trip cost from the travel and stay totals. Never do this arithmetic yourself - always call this tool, it is exact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "travel_total_inr": {"type": "integer"},
                    "stay_total_inr": {"type": "integer"},
                    "days": {"type": "integer"},
                    "travelers": {"type": "integer"},
                    "budget_inr": {"type": "integer"},
                    "style": {"type": "string", "enum": ["shoestring", "normal", "comfort"]},
                },
                "required": ["travel_total_inr", "stay_total_inr", "days", "travelers", "budget_inr"],
            },
        },
    },
]


async def run_tool(name: str, args: dict, extra: dict | None = None) -> dict:
    """
    Run one tool and return its result.

    Everything is wrapped in try/except: if a tool blows up we return the
    error as a normal result so the model can read it and try something else.
    An agent that crashes on the first bad argument is not an agent.

    `extra` carries what the agent knows but the model never needs to see:
    trip dates, the parsed notes (preferences), the diet for lunch picks.
    """
    extra = extra or {}
    try:
        if name == "get_weather":
            spot = await geo.geocode(args["city"])
            result = await weather.get_forecast(
                spot["lat"], spot["lon"], args["start_date"], args["end_date"])
            return {"ok": True, "result": result}

        if name == "search_transport":
            options = await transport.all_transport_options(
                args["origin_city"], args["destination_city"],
                args["depart_date"], args.get("passengers", 1))
            return {"ok": True, "result": {"options": options, "count": len(options)}}

        if name == "search_stays":
            spot = await geo.geocode(args["city"])
            options = await stays.find_stays(
                spot["lat"], spot["lon"], args["city"],
                args["nights"], args.get("max_price_per_night"))
            return {"ok": True, "result": {"options": options, "count": len(options)}}

        if name == "find_attractions":
            spot = await geo.geocode(args["city"])
            found = await places.find_attractions(
                spot["lat"], spot["lon"], args.get("interests", []),
                num_days=args.get("num_days", 3), city=args["city"],
                start_date=extra.get("start_date"), end_date=extra.get("end_date"),
                preferences=extra.get("preferences"))
            return {"ok": True, "result": {"attractions": found, "count": len(found)}}

        if name == "find_restaurants":
            spot = await geo.geocode(args["city"])
            found = await places.find_restaurants(spot["lat"], spot["lon"],
                                                  diet=extra.get("diet"))
            return {"ok": True, "result": {"restaurants": found, "count": len(found)}}

        if name == "get_local_tips":
            gems = retriever.search(args.get("query", ""), city=args["city"], top_k=5)
            return {"ok": True, "result": {"tips": gems, "count": len(gems)}}

        if name == "calculate_budget":
            result = budget_tool.compute(
                travel_total_inr=args["travel_total_inr"],
                stay_total_inr=args["stay_total_inr"],
                days=args["days"],
                travelers=args["travelers"],
                budget_inr=args["budget_inr"],
                style=args.get("style", "normal"),
            )
            return {"ok": True, "result": result}

        return {"ok": False, "error": f"There is no tool called '{name}'"}

    except KeyError as e:
        return {"ok": False, "error": f"Missing required argument: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def short_summary(name: str, payload: dict) -> str:
    """
    A one-line human version of a tool result, for the live trace in the UI.
    We do NOT show the raw JSON, it is far too long.
    """
    if not payload.get("ok"):
        return f"failed: {payload.get('error', 'unknown error')}"

    result = payload["result"]

    if name == "get_weather":
        kind = "real forecast" if result.get("is_real_forecast") else "seasonal estimate"
        rainy = len(result.get("rainy_days", []))
        return f"{len(result.get('days', []))} days ({kind}), {rainy} likely rainy"

    if name == "search_transport":
        options = result["options"]
        if not options:
            return "no options found"
        cheapest = options[0]
        return f"{len(options)} options, cheapest {cheapest['carrier']} at Rs {cheapest['total_inr']:,}"

    if name == "search_stays":
        options = result["options"]
        if not options:
            return "no stays found"
        return f"{len(options)} stays, from Rs {options[0]['price_per_night_inr']:,}/night"

    if name == "find_attractions":
        return f"{result['count']} real places found"

    if name == "find_restaurants":
        return f"{result['count']} places to eat"

    if name == "get_local_tips":
        return f"{result['count']} traveller tips retrieved"

    if name == "calculate_budget":
        verdict = "fits" if result["fits"] else f"over by Rs {result['over_by_inr']:,}"
        return f"total Rs {result['total_inr']:,} ({verdict})"

    return "done"
