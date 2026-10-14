"""
The agent itself. This file runs one whole trip plan from start to finish.

The shape of a run:

    1. GATHER    the LLM is given the toolbox and decides what to look up.
                 If there is no Groq key, we call the tools ourselves in a
                 sensible order. Either way we end up with the same data.

    2. SAFETY    whatever the LLM forgot, we fetch ourselves. An agent that
                 skips the weather call must not produce a plan with no
                 weather in it.

    3. SOLVE     the budget solver runs. Pure Python. Re-plans up to 5 times.

    4. ROUTE     k-means + nearest neighbour turn the places into days.

    5. WRITE     the LLM writes the actual prose, using the retrieved local
                 tips as context. This is the only place it writes for the user.

    6. CRITIC    a second pass checks the plan for contradictions.

Notice which steps use the LLM and which do not. Language goes to the model;
maths and geometry stay in Python. That split is the main design decision in
this whole project.
"""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta

import config
import dayplanner
import llm
import metrics
import preferences
import recommend
from agent import critic, events, repair, replan
from agent.registry import TOOL_SCHEMAS, run_tool, short_summary
from dayplanner import build_day_plan, total_distance
from rag.retriever import retriever
from schemas import TripRequest
from tools import geo, places, stays, transport, weather
from tools.geo import haversine_km

SYSTEM_PROMPT = """You are the planning agent for Ghumi Ghumi, a travel planner
for travellers in India.

You have tools. Use them to gather everything you need about one trip. Work in
this rough order, calling several tools in one go where you can:

  1. get_weather for the destination and dates
  2. search_transport for the OUTBOUND route only - the return journey is
     priced automatically, so do NOT call it a second time with the cities
     swapped. That wastes tokens and the free tier is small.
  3. find_attractions and find_restaurants for the destination
  4. get_local_tips using the traveller's interests
  5. search_stays for the destination

Rules:
- Never invent a price, a hotel name or an opening time. If you did not get it
  from a tool, do not say it.
- Never do arithmetic yourself. calculate_budget exists for that.
- Once you have gathered the data, reply with a short plain sentence saying you
  are done. Do not write the itinerary - a later step does that.

- Call each tool ONCE, and request ALL of them in your FIRST reply (they run
  in parallel). Every extra round trip costs time and tokens we do not have.

Keep it to a handful of tool calls. You are gathering, not deciding."""


ITINERARY_PROMPT = """You are writing the day-by-day text for a travel itinerary
that has ALREADY been planned. The route, the stops, the hotel and the budget
are fixed and correct. Your job is only to describe them well.

Rules:
- Write one short paragraph per day, 3 to 5 sentences.
- Use ONLY the stops given to you for that day, in the order given. The order
  was optimised by distance, so do not reorder it.
- Do NOT mention the weather. The interface shows the rain chance next to each
  day already, and repeating "it might rain today" in all six paragraphs reads
  like filler. The ONE exception: if a day is marked WETTEST, you may give it a
  single short clause about keeping it flexible. Nowhere else.
- Weave in the local tips where they are genuinely relevant. Do not force them.
- Mention the lunch spot naturally if one is given.
- Stops marked MUST-SEE are the headline of the day: open with or build the
  paragraph around them, and say in a few words what makes each one worth
  the trip. Keep the tone upbeat and specific, like a friend who has been.
- When a MUST-SEE stop comes with "chosen because", use that reason (in your
  own words) - it is the real, computed reason. Do not add other claims.
- Never describe an everyday market or shop as a highlight unless it is
  marked MUST-SEE.
- If a day has a TRAVEL note, open with one short clause about arriving or
  leaving (for example "After the morning train gets in, ..."). Keep it brief.
- If a day has no stops because it is a travel or rest day, write one or two
  sentences about settling in or resting - do not invent sightseeing.
- Respect the traveller's constraints listed as "Already applied" - never
  suggest something they ruled out.
- Never mention a price. Never invent a place that is not in the list.
- Start each paragraph with "Day N:" and nothing before it. No introduction,
  no summary at the end."""


async def plan_trip(trip_id: str, req: TripRequest) -> dict:
    """Run the whole thing. Returns the finished plan dictionary."""
    started = time.perf_counter()
    events.open_stream(trip_id)

    await events.emit(trip_id, "start",
                      f"Planning {req.source_city} to {req.destination_city}, "
                      f"{req.num_days} days, budget Rs {req.budget_inr:,}", kind="info")

    context: dict = {}

    # ---------- 0: read the traveller's notes ----------
    # The notes box used to reach only the prose. Now it becomes structured
    # rules (diet, pace, things to avoid, places they asked for) that the
    # ranking, the day planner, the transport choice and the lunch picks obey.
    prefs = await preferences.understand(req.notes, req.children, trip_id)
    context["preferences"] = prefs
    if prefs["understood"]:
        await events.emit(trip_id, "understood",
                          "Read your notes: " + "; ".join(prefs["understood"]), kind="think",
                          data={"preferences": prefs})

    # ---------- 1 & 2: gather ----------
    if config.LLM_ENABLED:
        await _gather_with_llm(trip_id, req, context)
    elif config.STRICT_MODE:
        # Should never happen - main.py refuses the request first - but an
        # agent module that can be called directly should defend itself.
        raise RuntimeError(
            "GROQ_API_KEY is not set. Ghumi Ghumi will not plan a trip without "
            "the model, because that would mean shipping guessed data.")
    else:
        await events.emit(trip_id, "no_llm",
                          "STRICT_MODE is off, so the agent is running on Python rules "
                          "instead of the model.", kind="warn")

    await _fill_gaps(trip_id, req, context)

    # Price every stay for the WHOLE group (rooms, or beds for a hostel).
    context["stay_options"] = stays.price_for_group(
        context.get("stay_options", []), req.num_adults, req.children)

    # Transport the traveller ruled out ("no flights", "no early flights").
    transport_options, transport_note = preferences.filter_transport(
        context.get("transport_options", []), prefs)
    if transport_note:
        await events.emit(trip_id, "transport_prefs", transport_note, kind="warn")

    # ---------- 3: the budget solver ----------
    await events.emit(
        trip_id, "solve",
        f"Checking whether this trip fits the budget ({req.travel_style} style)",
        kind="think")

    # Where the best sights are: a hotel near them saves the most travel, so the
    # stay ranking is judged against this point, not against the cheapest price.
    stay_anchor = recommend.best_area(context.get("attractions", []))

    decision = replan.solve(
        anchor=stay_anchor,
        days=req.num_days,
        nights=req.num_nights,
        travelers=req.travelers,
        budget_inr=req.budget_inr,
        transport_options=transport_options,
        stay_options=context.get("stay_options", []),
        travel_style=req.travel_style,
    )

    # Replay each round of the solver as its own event, so the UI shows the
    # agent changing its mind rather than one silent jump to the answer.
    for attempt in decision["attempts"]:
        verdict = "fits" if attempt["fits"] else f"over by Rs {attempt['over_by_inr']:,}"
        await events.emit(
            trip_id, f"budget_round_{attempt['round']}",
            f"Round {attempt['round']}: {attempt['transport']} + {attempt['stay']} "
            f"({attempt['style']}) = Rs {attempt['total_inr']:,} - {verdict}",
            kind="replan" if not attempt["fits"] else "info",
            data=attempt,
        )
        if attempt["change_made"]:
            await events.emit(trip_id, f"replan_{attempt['round']}",
                              attempt["change_made"], kind="replan")

    if decision.get("impossible"):
        await events.emit(trip_id, "impossible", decision["reason"], kind="warn")
        plan = {"ok": False, "reason": decision["reason"],
                "minimum_needed_inr": decision.get("minimum_needed_inr", 0),
                "attempts": decision["attempts"]}
        # Do NOT close the stream here. The frontend treats "stream closed" as
        # "safe to fetch the trip now" - closing before _run_agent (main.py)
        # has actually saved this plan to the database is a race: on a fast
        # local network the GET can land before the commit, and the page
        # renders blank because trip.plan is still empty. main.py closes the
        # stream itself, right after the save.
        return plan

    # ---------- 4: turn places into days ----------
    await events.emit(trip_id, "route",
                      "Grouping the places into days by location and ordering each day "
                      "from the hotel", kind="think")

    stay = decision["stay"]
    dates = _date_list(req.start_date.isoformat(), req.num_days)
    forecast = context.get("weather", {})

    # ---------- 3b: second look at the places, now we know the rest ----------
    # The first ranking only knew the interests. Now the budget solver has
    # decided where the traveller sleeps and how freely they can spend, and the
    # forecast is known, so score every place again with that context. The
    # ranking is code, not the LLM: it only writes up what is chosen here.
    real_stay = stay if stay and not stay.get("is_price_band") else None
    rain_days = len(forecast.get("rainy_days", []))
    ctx = {
        "anchor": (stay["lat"], stay["lon"]) if real_stay else
                  (context["destination"]["lat"], context["destination"]["lon"]),
        "anchor_is_stay": bool(real_stay),
        "spend_style": decision["budget"]["style"] if decision.get("budget") else None,
        "rain_share": (rain_days / max(1, req.num_days)) if forecast.get("days") else None,
        "city": req.destination_city,
    }
    for key in ("avoid_kinds", "avoid_words", "must_include", "extra_interests"):
        if prefs.get(key):
            ctx[key] = prefs[key]
    context["attractions"] = places.rerank_places(
        context.get("attractions", []), req.interests, ctx, min_keep=req.num_days * 2)
    await events.emit(
        trip_id, "rank",
        f"Ranked {len(context['attractions'])} places by interest match, quality evidence, "
        f"cost fit and distance from your stay", kind="tool")

    # date -> chance of rain, so each day can show its own number instead of
    # the prose repeating "it might rain" six times in a row.
    rain_by_date = {d["date"]: d.get("rain_chance")
                    for d in forecast.get("days", [])}

    # How much of each day is really free: the arrival day starts after the
    # journey, the last day ends in time to go home.
    windows = dayplanner.day_windows(req.num_days, decision["transport"])
    if req.destination_city.strip().lower() in critic.HIGH_ALTITUDE and req.num_days >= 3:
        windows[0].update(available_hours=0.0, travel_note=(
            (windows[0]["travel_note"] + " ").lstrip() +
            "Day 1 is kept free to acclimatise to the altitude - rest, drink water, no exertion."))

    hotel_lat = stay["lat"] if stay else context["destination"]["lat"]
    hotel_lon = stay["lon"] if stay else context["destination"]["lon"]

    # Heights of the stay and every place (one free call): a climb of 1,400 m
    # to Mussoorie is a very different drive from a flat 15 km.
    await _attach_elevations(context.get("attractions", []), (hotel_lat, hotel_lon))

    days = build_day_plan(
        attractions=context.get("attractions", []),
        restaurants=context.get("restaurants", []),
        dates=dates,
        hotel_lat=hotel_lat,
        hotel_lon=hotel_lon,
        rainy_dates=forecast.get("rainy_days", []),
        rain_chances=rain_by_date,
        windows=windows,
        max_stops_per_day=prefs.get("max_stops_per_day"),
    )

    # ---------- 4b: self-check and repair, BEFORE writing anything ----------
    # The critic's rules run once on the draft; anything it can fix with a
    # tool call (today: a lunch spot far from that day's stops) is fixed, and
    # the event stream shows the agent correcting itself.
    await _repair(trip_id, req, days, prefs)

    await events.emit(
        trip_id, "route_done",
        f"{len(days)} days planned, {total_distance(days)}km of travel in total",
        kind="tool", data={"total_km": total_distance(days)})

    # Score the chosen stay and its alternatives at the final budget level, so
    # every "why" below is consistent with the money actually left.
    stay_view = replan.stay_alternatives(
        context.get("stay_options", []), stay,
        budget_inr=req.budget_inr, transport_total=decision["transport"]["total_inr"] * 2,
        days=req.num_days, nights=req.num_nights, travelers=req.travelers,
        style=decision["budget"]["style"], travel_style=req.travel_style, anchor=stay_anchor)
    if stay:
        scored_pick = next((s for s in stay_view["picks"] if s["name"] == stay["name"]), None)
        if scored_pick is None:
            scored_pick = recommend.rank_stays(
                [stay], stay_view["ceiling_per_night_inr"], req.travel_style, stay_anchor)[0]
        stay = {**stay, **{k: scored_pick[k] for k in ("score", "why", "recommendation")}}

    tips = context.get("tips", [])
    plan = {
        "ok": True,
        "destination": context.get("destination", {}),
        "transport": _with_return_fare(decision["transport"], req.travelers),
        "transport_options": [_with_return_fare(o, req.travelers)
                              for o in transport_options[:6]],
        "stay": stay,
        "stay_options": stay_view["picks"] or context.get("stay_options", [])[:4],
        "stay_stretch": stay_view["stretch"],
        "stay_ceiling_per_night_inr": stay_view["ceiling_per_night_inr"],
        "budget": decision["budget"],
        "attempts": decision["attempts"],
        "savings_note": replan.savings_summary(decision["attempts"]),
        "weather": forecast,
        "days": days,
        "tips": tips,
        "total_km": total_distance(days),
        "fits": decision["fits"],
        "reason": decision.get("reason", ""),
        "preferences": prefs,
        "transport_note": transport_note,
    }

    # ---------- 5: review the draft and FIX it (the traveller never sees this) ----------
    await events.emit(trip_id, "review", "Double-checking the plan", kind="think")
    plan["agent_review"] = await _review_and_fix(
        trip_id, req, plan, context, (hotel_lat, hotel_lon))
    plan["recommendation_summary"] = recommend.summarize_days(plan["days"])

    # ---------- 6: write it up (after the fixes, so the text matches) ----------
    prose = await _write_itinerary(trip_id, req, plan["days"], tips, prefs)
    for day in plan["days"]:
        day["description"] = prose.get(day["day_number"], "")

    # ---------- 7: what the traveller should know ----------
    # Only things no change to the plan can fix: over budget, altitude, rain on
    # every day, a very long journey, a stay far from everything.
    plan["issues"] = [i for i in critic.rule_checks(plan, req.as_text(), req.destination_city)
                      if i.get("for_traveller")]
    await events.emit(trip_id, "review_done",
                      f"{len(plan['agent_review'])} finding(s) handled by the agent, "
                      f"{len(plan['issues'])} note(s) for the traveller", kind="info")

    # ---------- done ----------
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    cost = metrics.cost_for_trip(trip_id)
    plan["meta"] = {"ms": elapsed, "cost_inr": cost["cost_inr"], "tokens": cost["tokens"]}

    await events.emit(
        trip_id, "done",
        f"Finished in {elapsed / 1000:.1f}s. Planning this trip cost us "
        f"Rs {cost['cost_inr']:.3f} in tokens.",
        kind="done", data=plan["meta"])
    # Stream stays open until main.py has actually saved the plan - see the
    # note above.
    return plan


# ---------------------------------------------------------------------------
# Step 1: let the model drive
# ---------------------------------------------------------------------------

GATHER_KEYS = ("weather", "transport_options", "attractions", "restaurants", "stay_options", "tips")


async def _gather_with_llm(trip_id: str, req: TripRequest, context: dict):
    """
    The tool-calling loop.

    We keep going until the model stops asking for tools or we hit
    MAX_AGENT_STEPS. That limit is not optional - without it a confused model
    can loop forever and burn your whole free tier in a minute.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": req.as_text()},
    ]

    for step in range(config.MAX_AGENT_STEPS):
        try:
            reply = await llm.ask_tools(messages, TOOL_SCHEMAS, trip_id=trip_id)
        except Exception as e:
            # Catch everything, not just our own exception type. A raw httpx
            # ReadError used to escape here and abort the whole plan, which
            # is a terrible outcome when we already have all the data we need
            # and only the writing step is left.
            await events.emit(trip_id, "llm_error",
                              f"Model unavailable ({type(e).__name__}). Carrying on "
                              f"with what we have.", kind="warn")
            return

        messages.append(reply)
        tool_calls = reply.get("tool_calls") or []

        if not tool_calls:
            text = (reply.get("content") or "").strip()
            if text:
                await events.emit(trip_id, "agent_says", text[:200], kind="think")
            return

        # Run every tool the model asked for in this turn AT THE SAME TIME.
        # v26 ran them one after another - the attraction and restaurant
        # searches each waited for the other's map servers.
        prepared = []
        for call in tool_calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            args = _lock_args(name, args, req)
            prepared.append((call, name, args))
            await events.emit(trip_id, f"tool:{name}",
                              f"Calling {name} {_args_preview(args)}", kind="tool")

        extra = _tool_extra(req, context)
        payloads = await asyncio.gather(*(run_tool(name, args, extra)
                                          for _, name, args in prepared))

        for (call, name, _), payload in zip(prepared, payloads):
            _absorb(name, payload, context)
            summary = short_summary(name, payload)
            await events.emit(trip_id, f"tool:{name}:result", summary,
                              kind="tool" if payload.get("ok") else "warn")
            # IMPORTANT: we send the model a SHORT summary, not the full JSON.
            # Thirty attractions with coordinates is thousands of tokens and
            # the model does not need them - our Python code does.
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps({"ok": payload.get("ok", False), "summary": summary}),
            })

        # Everything the planner needs is in hand: do not spend another model
        # round trip (and free-tier tokens) just to hear "Done."
        if all(key in context for key in GATHER_KEYS):
            await events.emit(trip_id, "agent_says",
                              "Gathered everything needed - moving on to planning", kind="think")
            return

    await events.emit(trip_id, "step_limit",
                      f"Hit the {config.MAX_AGENT_STEPS} step limit, moving on with what we have",
                      kind="warn")


def _with_return_fare(option: dict | None, travelers: int) -> dict | None:
    """
    Every fare the traveller sees is the RETURN fare for the WHOLE group.

    Tools price one way for all passengers (total_inr); the budget has always
    counted it twice ("Getting there and back"), but the card used to show the
    one-way number. Now the headline is the return total, with the per-person
    figure next to it.
    """
    if not option:
        return option
    item = dict(option)
    one_way_all = int(item.get("total_inr", 0))
    people = max(1, int(travelers or 1))
    item["travelers"] = people
    item["one_way_total_inr"] = one_way_all
    item["return_total_inr"] = one_way_all * 2
    item["return_per_person_inr"] = round(one_way_all * 2 / people)
    return item


def _lock_args(name: str, args: dict, req: TripRequest) -> dict:
    """
    The model chooses WHICH tools to call; the facts of the trip are not its to
    change. v23 let it call find_attractions with no num_days (default 3) or
    with interests it made up, which gave a worse result than the no-LLM path.
    """
    args = dict(args or {})
    if name in ("find_attractions", "find_restaurants", "search_stays", "get_weather",
                "get_local_tips"):
        args["city"] = req.destination_city
    if name == "find_attractions":
        args["interests"] = list(req.interests)
        args["num_days"] = req.num_days
    if name == "search_stays":
        args["nights"] = req.num_nights
        args.pop("max_price_per_night", None)   # the budget solver decides affordability
    if name == "get_weather":
        args["start_date"] = req.start_date.isoformat()
        args["end_date"] = req.end_date.isoformat()
    if name == "search_transport":
        args["origin_city"] = req.source_city
        args["destination_city"] = req.destination_city
        args["depart_date"] = req.start_date.isoformat()
        args["passengers"] = req.travelers
    return args


def _tool_extra(req: TripRequest, context: dict) -> dict:
    prefs = context.get("preferences") or {}
    return {"start_date": req.start_date.isoformat(), "end_date": req.end_date.isoformat(),
            "preferences": prefs, "diet": prefs.get("diet")}


def _args_preview(args: dict) -> str:
    """Short readable version of tool arguments for the live trace."""
    if not args:
        return ""
    bits = [f"{k}={v}" for k, v in list(args.items())[:3] if not isinstance(v, (dict, list))]
    return "(" + ", ".join(bits) + ")" if bits else ""


def _absorb(name: str, payload: dict, context: dict):
    """Copy the FULL tool result into our context, keyed by what it is."""
    if not payload.get("ok"):
        return
    result = payload["result"]

    if name == "get_weather":
        context["weather"] = result
    elif name == "search_transport":
        context["transport_options"] = result["options"]
    elif name == "search_stays":
        context["stay_options"] = result["options"]
    elif name == "find_attractions":
        context["attractions"] = result["attractions"]
    elif name == "find_restaurants":
        context["restaurants"] = result["restaurants"]
    elif name == "get_local_tips":
        context["tips"] = result["tips"]


# ---------------------------------------------------------------------------
# Step 2: the safety net
# ---------------------------------------------------------------------------

async def _fill_gaps(trip_id: str, req: TripRequest, context: dict):
    """
    Fetch anything the model did not.

    This is also the whole gather step when there is no Groq key at all, which
    is why the project still runs with zero API keys.
    """
    destination = await geo.geocode(req.destination_city)
    context["destination"] = destination

    # Whatever is still missing is fetched IN PARALLEL (v26 did it one by one).
    lat, lon = destination["lat"], destination["lon"]
    prefs = context.get("preferences") or {}
    jobs = {}
    if "weather" not in context:
        jobs["weather"] = weather.get_forecast(lat, lon, req.start_date.isoformat(),
                                               req.end_date.isoformat())
    if "transport_options" not in context:
        jobs["transport_options"] = transport.all_transport_options(
            req.source_city, req.destination_city, req.start_date.isoformat(), req.travelers)
    if "attractions" not in context:
        jobs["attractions"] = places.find_attractions(
            lat, lon, req.interests, num_days=req.num_days, city=req.destination_city,
            start_date=req.start_date.isoformat(), end_date=req.end_date.isoformat(),
            preferences=prefs)
    if "restaurants" not in context:
        jobs["restaurants"] = places.find_restaurants(lat, lon, diet=prefs.get("diet"))
    if "stay_options" not in context:
        jobs["stay_options"] = stays.find_stays(lat, lon, req.destination_city, req.num_nights)

    if jobs:
        await events.emit(trip_id, "fill:start",
                          "Fetching " + ", ".join(k.replace("_options", "").replace("_", " ")
                                                  for k in jobs) + " in parallel", kind="tool")
        results = await asyncio.gather(*jobs.values())
        for key, value in zip(jobs, results):
            context[key] = value
        bits = []
        if "weather" in jobs:
            bits.append(context["weather"].get("source", "weather"))
        if "transport_options" in jobs:
            bits.append(f"{len(context['transport_options'])} travel options")
        if "attractions" in jobs:
            bits.append(f"{len(context['attractions'])} places")
        if "restaurants" in jobs:
            bits.append(f"{len(context['restaurants'])} places to eat")
        if "stay_options" in jobs:
            bits.append(f"{len(context['stay_options'])} stays")
        await events.emit(trip_id, "fill:done", "; ".join(bits), kind="tool")

    if "tips" not in context:
        query = " ".join(req.interests) + " " + req.notes
        if (context.get("preferences") or {}).get("diet"):
            query += " vegetarian food"
        context["tips"] = retriever.search(query, city=req.destination_city, top_k=5)
        if context["tips"]:
            await events.emit(trip_id, "fill:tips",
                              f"{len(context['tips'])} traveller tips retrieved for "
                              f"{req.destination_city}", kind="tool")


# ---------------------------------------------------------------------------
# Step 5: the writing
# ---------------------------------------------------------------------------

async def _attach_elevations(attractions: list[dict], hotel: tuple[float, float]) -> None:
    """One Open-Meteo call for the stay + every place. Silent if offline."""
    points = [hotel] + [(p["lat"], p["lon"]) for p in attractions if p.get("lat") is not None]
    heights = await geo.elevations(points)
    dayplanner.set_base_elevation(hotel, heights[0] if heights else None)
    located = [p for p in attractions if p.get("lat") is not None]
    for place, height in zip(located, heights[1:]):
        if height is not None:
            place["elevation_m"] = height


async def _review_and_fix(trip_id: str, req: TripRequest, plan: dict, context: dict,
                          hotel: tuple[float, float]) -> list[dict]:
    """
    The rules and the LLM reviewer look at the draft; the agent fixes what they
    find (agent/repair.py). Findings go to the server log and plan["agent_review"],
    never to the traveller. The rules run again on the fixed plan, so anything
    the first pass of fixes created is caught once more.
    """
    skip = {"lunch_far", "closed_info", "weak_day"}
    findings = [i for i in critic.rule_checks(plan, req.as_text(), req.destination_city)
                if not i.get("for_traveller") and i.get("kind") not in skip]
    findings += await critic.llm_review(critic.plan_to_text(plan), req.as_text(), trip_id)

    log = repair.fix_plan(plan, findings, context.get("attractions", []),
                          context.get("restaurants", []), hotel)

    # second, rules-only pass on the fixed plan (no extra LLM call)
    again = [i for i in critic.rule_checks(plan, req.as_text(), req.destination_city)
             if not i.get("for_traveller") and i.get("kind") not in skip]
    if again:
        log += repair.fix_plan(plan, again, context.get("attractions", []),
                               context.get("restaurants", []), hotel)

    for entry in log:
        where = f"Day {entry['day']}" if entry.get("day") else "Plan"
        source = "review" if entry.get("from_llm") else "rules"
        print(f"[agent:{trip_id}] {source}: {where} {entry.get('kind')}: "
              f"{entry.get('issue', '')[:140]} -> {entry.get('action')}")
    if log:
        await events.emit(trip_id, "review_fixes",
                          f"Fixed or checked {len(log)} thing(s) before writing the plan",
                          kind="replan", data={"count": len(log)})
    return [{k: v for k, v in e.items() if k in ("kind", "day", "issue", "action", "from_llm")}
            for e in log]


async def _repair(trip_id: str, req: TripRequest, days: list[dict], prefs: dict) -> None:
    """
    Act on the critic's repairable findings before the plan is written up.

    Today that is "lunch is far from the day's stops": search for restaurants
    around that day's stops and take the best one close by. This is the
    agent noticing its own mistake and fixing it with a tool call, and the
    event stream shows it happening.
    """
    draft = {"days": days}
    actions = [i["action"] for i in critic.rule_checks(draft, req.as_text(), req.destination_city)
               if i.get("action")]
    targets = []
    for action in actions:
        if action.get("type") != "find_lunch_nearby":
            continue
        day = next((d for d in days if d["day_number"] == action["day"]), None)
        if day and day.get("stops"):
            targets.append(day)
    if not targets:
        return

    await events.emit(trip_id, "repair_lunch",
                      f"Finding lunch closer to the stops on {len(targets)} day(s)", kind="replan")

    # All the searches at once (v26 ran them one after another), and searches
    # whose points are within ~1 km of each other share one call.
    def key(day):
        mid = day["stops"][len(day["stops"]) // 2]
        return (round(mid["lat"], 2), round(mid["lon"], 2))

    unique = {}
    for day in targets:
        unique.setdefault(key(day), day["stops"][len(day["stops"]) // 2])
    results = await asyncio.gather(*(
        places.find_restaurants(mid["lat"], mid["lon"], limit=10, radius_m=3000,
                                diet=prefs.get("diet")) for mid in unique.values()))
    by_key = dict(zip(unique.keys(), results))

    used = {d["lunch"]["name"] for d in days if d.get("lunch") and d not in targets}
    for day in targets:
        old = day.get("lunch") or {}
        nearby = by_key.get(key(day)) or []
        nearby = [r for r in nearby if r.get("source") != "offline-sample"] or nearby
        better = dayplanner.pick_lunch(nearby, day["stops"], used)
        if better and better.get("distance_km", 99) < old.get("distance_km", 99):
            day["lunch"] = better
        if day.get("lunch"):
            used.add(day["lunch"]["name"])
        print(f"[agent:{trip_id}] lunch Day {day['day_number']}: "
              f"{old.get('name')} ({old.get('distance_km', 0):.1f} km) -> "
              f"{day['lunch'].get('name') if day.get('lunch') else '-'}")


async def _write_itinerary(trip_id: str, req: TripRequest, days: list[dict],
                           tips: list[dict], prefs: dict | None = None) -> dict[int, str]:
    """
    Ask the model for the day-by-day prose.

    Returns {day_number: paragraph}. Falls back to a plain template if there
    is no key or the call fails, so a day always has a description. Every
    paragraph the model writes is CHECKED against the fixed plan (see
    _paragraph_is_faithful) and replaced by the template if it drifts.
    """
    if not config.LLM_ENABLED:
        return {d["day_number"]: _template_description(d) for d in days}

    await events.emit(trip_id, "write", "Writing the day-by-day plan", kind="think")

    lines = []
    for day in days:
        stop_text = "; ".join(
            f"{s['name']} ({s['kind']}, about {s['visit_hours']}h"
            f"{', MUST-SEE' if s.get('must_see') else ''}"
            f"{', best in the ' + s['best_time'] if s.get('best_time') else ''}"
            f"{', chosen because: ' + s['why'].rstrip('.') if s.get('must_see') and s.get('why') else ''})"
            for s in day["stops"]
        ) or "no stops planned"
        lunch = f" Lunch near: {day['lunch']['name']}." if day.get("lunch") else ""
        travel = f" TRAVEL: {day['travel_note']}" if day.get("travel_note") else ""
        # Only the single wettest day is flagged to the model. Every day
        # being flagged is why every paragraph used to open with the weather.
        rain = " WETTEST DAY OF THE TRIP." if day.get("is_wettest") else ""
        lines.append(f"Day {day['day_number']} ({day['date']}):{rain}{travel} {stop_text}.{lunch}")

    applied = ""
    if prefs and prefs.get("understood"):
        applied = "Already applied: " + "; ".join(prefs["understood"]) + "\n\n"

    prompt = (
        f"{req.as_text()}\n\n{applied}"
        f"{retriever.as_prompt_context(tips)}\n\n"
        f"The fixed plan:\n" + "\n".join(lines) +
        f"\n\nWrite the {len(days)} paragraphs now."
    )

    try:
        text = await llm.ask_text(ITINERARY_PROMPT, prompt, temperature=0.6,
                                  label="llm_itinerary", trip_id=trip_id)
    except Exception as e:
        await events.emit(trip_id, "write_failed",
                          f"Could not write the prose ({e}), using a plain version", kind="warn")
        return {d["day_number"]: _template_description(d) for d in days}

    result = _split_days(text, days)
    replaced = []
    for day in days:
        if not _paragraph_is_faithful(result.get(day["day_number"], ""), day):
            result[day["day_number"]] = _template_description(day)
            replaced.append(str(day["day_number"]))
    if replaced:
        await events.emit(trip_id, "write_checked",
                          f"Day {', '.join(replaced)}: the model's text did not match the plan "
                          f"(missing stops or a price), so the plain version is used.", kind="warn")
    return result


_PRICE_RE = re.compile(r"(₹|\brs\.?\s*\d|\binr\b|\brupees?\b)", re.I)


def _paragraph_is_faithful(text: str, day: dict) -> bool:
    """
    Cheap, strict guard on the LLM's paragraph:
      - no prices (the budget panel owns every number)
      - if the day has stops, the paragraph mentions at least half of them
        (by their first distinctive word)
    """
    if not text:
        return False
    if _PRICE_RE.search(text):
        return False
    stops = day.get("stops") or []
    if not stops:
        return True
    lowered = text.lower()
    hits = 0
    for s in stops:
        words = [w for w in re.findall(r"[a-z']{4,}", s["name"].lower())
                 if w not in {"temple", "mandir", "garden", "park", "museum", "road", "the"}]
        if not words or any(w in lowered for w in words[:2]):
            hits += 1
    return hits * 2 >= len(stops)


def _split_days(text: str, days: list[dict]) -> dict[int, str]:
    """Cut the model's reply at each 'Day N:' header."""
    import re

    result = {}
    parts = re.split(r"(?=Day\s+\d+\s*:)", text.strip())

    for part in parts:
        match = re.match(r"Day\s+(\d+)\s*:\s*(.*)", part.strip(), re.DOTALL)
        if match:
            result[int(match.group(1))] = match.group(2).strip()

    # anything missing gets the template version
    for day in days:
        result.setdefault(day["day_number"], _template_description(day))

    return result


def _template_description(day: dict) -> str:
    """The no-LLM version. Plain, but still useful and never wrong."""
    travel = (day.get("travel_note") or "").strip()
    if not day["stops"] and day.get("travel_day"):
        return (travel + " Keep the rest of the day for settling in.").strip()
    if not day["stops"]:
        # build_day_plan borrows a spare stop from elsewhere in the trip so
        # this should only ever fire when literally no real places were
        # found anywhere near the destination on any day.
        return ("No verified places turned up for this day even after checking "
                "every source. Worth widening interests or picking a bigger "
                "nearby town as a base rather than leaving it unplanned.")

    names = [s["name"] for s in day["stops"]]
    if len(names) == 1:
        body = f"Today is built around {names[0]}."
    else:
        body = f"Start at {names[0]}, then work your way through {', '.join(names[1:])}."
    # (the travel note is shown as its own banner in the interface)
    evening = [s["name"] for s in day["stops"] if s.get("best_time") == "evening"]
    if evening:
        body += f" {evening[-1]} is saved for the evening, when it is at its best."

    body += f" That is about {day['total_km']}km of moving around and roughly {day['hours']} hours."

    if day.get("lunch"):
        body += f" For lunch, {day['lunch']['name']} is close to the stops."

    if day.get("is_wettest"):
        body += " This is the wettest day of the trip, so keep it flexible."

    return body


def _date_list(start_date: str, num_days: int) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    return [(start + timedelta(days=i)).isoformat() for i in range(num_days)]
