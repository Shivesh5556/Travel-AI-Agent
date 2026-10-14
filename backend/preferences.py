"""
Turning the traveller's free-text notes into rules the planner actually follows.

v23 had a box labelled "Anything the planner should know" with the placeholder
"No early flights, we are vegetarian, one of us cannot walk far" - and then
only pasted that text into the LLM's prose prompt. The ranking, the day
planner, the transport choice and the lunch picks never saw it. That is a
broken promise on the very first screen.

Now the notes become a small structured object:

    {"diet": "vegetarian", "max_stops_per_day": 3, "avoid_kinds": ["peak"],
     "avoid_words": ["trek"], "no_flights_before": 8.0, "avoid_modes": [],
     "extra_interests": ["Spiritual"], "must_include": ["Nahargarh Fort"],
     "understood": ["Vegetarian food", "Gentle pace: at most 3 stops a day", ...]}

Two layers, same idea as the critic:
  1. rules (always on, deterministic, unit-tested)
  2. the LLM (when a key is set) reads the notes and returns the SAME schema as
     JSON. It is validated, clipped to known values and merged with the rules -
     so the model can only ever add understanding, never invent a new kind of
     instruction the code does not know how to obey.

`understood` is shown to the traveller as chips ("Got it: vegetarian food,
gentle pace...") so they can see their notes were read.
"""

import re

import config
import llm
from schemas import INTEREST_CHOICES

KNOWN_KINDS = {"place of worship", "marketplace", "mall", "bar", "pub", "nightclub", "peak",
               "viewpoint", "museum", "beach", "waterfall", "nature reserve", "park",
               "garden", "fort", "palace", "zoo", "spa", "event", "lake", "ruins", "monument"}
KNOWN_MODES = {"flight", "train", "bus"}
DIETS = {"vegetarian", "jain", "vegan"}

NEGATION = r"(?:no|not|avoid|skip|without|don'?t|do not|hate|dislike|never|nothing)"

# (pattern on the negated phrase, what to avoid, chip text)
AVOID_RULES = [
    (r"temples?|religious|mandirs?|pilgrimage|places? of worship",
     {"kinds": ["place of worship"]}, "Skipping temples and religious sites"),
    (r"shopping|markets?|malls?|bazaars?",
     {"kinds": ["marketplace", "mall"]}, "Skipping markets and malls"),
    (r"night ?life|clubs?|bars?|pubs?|drinking|alcohol|partying",
     {"kinds": ["bar", "pub", "nightclub"]}, "No nightlife"),
    (r"treks?|trekking|hikes?|hiking|climb(?:ing)?|steep",
     {"words": ["trek", "trail", "hike", "climb"], "kinds": ["peak"]}, "No treks or steep climbs"),
    (r"museums?", {"kinds": ["museum"]}, "Skipping museums"),
    (r"beach(?:es)?", {"kinds": ["beach"]}, "Skipping beaches"),
    (r"crowds?|crowded", {}, "Avoiding the most crowded times where we can"),
]

POSITIVE_INTEREST_RULES = [
    (r"temples?|spiritual|pilgrimage|aarti|ghats?", "Spiritual"),
    (r"history|historical|heritage|forts?|palaces?|architecture", "Culture & Heritage"),
    (r"nature|waterfalls?|lakes?|hills?|mountains?|greenery", "Nature"),
    (r"beach(?:es)?|relax(?:ing|ation)?|chill", "Relaxation"),
    (r"treks?|trekking|hiking|adventure|rafting|paragliding", "Adventure"),
    (r"shopping|markets?|bazaars?|handicrafts?", "Shopping"),
    (r"night ?life|party|parties|clubs?|pubs?", "Nightlife"),
    (r"photo(?:graphy|s)?|sunsets?|sunrises?|viewpoints?", "Photography"),
    (r"street food|local food|foodie|cuisine|food", "Food"),
]

MUST_RE = re.compile(
    r"(?i:must[- ](?:see|visit|do|include)|definitely (?:see|visit)|want to (?:see|visit)|"
    r"don'?t miss|include|wish to see|would love to see)\s+(?i:the\s+)?"
    r"([A-Z][\w'’\-]*(?:\s+(?:[A-Z][\w'’\-]*|ki|ka|ke|of|the|de))*)")


def empty() -> dict:
    return {"diet": None, "max_stops_per_day": None, "pace": None, "avoid_kinds": [],
            "avoid_words": [], "avoid_modes": [], "no_flights_before": None,
            "extra_interests": [], "must_include": [], "with_kids": False,
            "limited_mobility": False, "understood": []}


def _add(lst: list, items) -> None:
    for item in items:
        if item and item not in lst:
            lst.append(item)


def parse_rules(notes: str, children: int = 0) -> dict:
    """Deterministic reading of the notes. Never raises."""
    prefs = empty()
    text = (notes or "").strip()
    low = text.lower()
    chips = prefs["understood"]

    # ---- diet ----
    if re.search(r"\bjain\b", low):
        prefs["diet"] = "jain"
    elif re.search(r"\bvegan\b", low):
        prefs["diet"] = "vegan"
    elif re.search(r"\b(pure veg|vegetarian|veg only|only veg|no non[- ]?veg|don'?t eat meat|"
                   r"no meat|we are veg|i am veg|i'm veg)\b", low) or \
            (re.search(r"\bveg\b", low) and not re.search(r"\bnon[- ]?veg\b", low)):
        prefs["diet"] = "vegetarian"
    if prefs["diet"]:
        chips.append({"vegetarian": "Vegetarian food", "jain": "Jain food",
                      "vegan": "Vegan food"}[prefs["diet"]] + " for the lunch picks")

    # ---- who is travelling ----
    group_kids = re.search(r"\b(\d+)\s+child", low)
    if children > 0 or group_kids or re.search(r"\b(kids?|children|toddlers?|baby|infant)\b", low):
        prefs["with_kids"] = True
        _add(prefs["avoid_kinds"], ["bar", "pub", "nightclub"])
        chips.append("Travelling with children: family-friendly stops, no nightlife")

    if re.search(r"\b(can'?t|cannot|unable to|not able to)\s+walk|walk(?:ing)? (?:is )?(?:difficult|hard)|"
                 r"wheel ?chair|bad knees?|knee|elderly|senior citizens?|old parents|"
                 r"grand ?(?:parents|mother|father)|my parents|mobility", low):
        prefs["limited_mobility"] = True
        prefs["max_stops_per_day"] = 3
        prefs["pace"] = "relaxed"
        _add(prefs["avoid_kinds"], ["peak"])
        _add(prefs["avoid_words"], ["trek", "trail", "hike", "climb"])
        chips.append("Easy on the legs: at most 3 stops a day, no treks or steep climbs")

    # ---- pace ----
    if prefs["pace"] is None:
        if re.search(r"\b(relax(?:ed|ing)?|slow|easy|laid[- ]back|not (?:too )?(?:hectic|rushed|packed)|"
                     r"take it easy|leisurely)\b", low) and not re.search(r"\bno relax", low):
            prefs["pace"] = "relaxed"
            prefs["max_stops_per_day"] = 3
            chips.append("Gentle pace: at most 3 stops a day")
        elif re.search(r"\b(packed|as much as possible|cover everything|maximum|max out|"
                       r"see everything|fast[- ]paced)\b", low):
            prefs["pace"] = "packed"
            prefs["max_stops_per_day"] = 6
            chips.append("Packed days: up to 6 stops a day")

    # ---- things to avoid (negated phrases) ----
    negated_spans = []
    for pattern, effect, chip in AVOID_RULES:
        m = re.search(rf"\b{NEGATION}\s+(?:\w+\s+){{0,2}}?({pattern})\b", low)
        if m:
            negated_spans.append(m.span())
            _add(prefs["avoid_kinds"], effect.get("kinds", []))
            _add(prefs["avoid_words"], effect.get("words", []))
            if chip not in chips:
                chips.append(chip)

    # ---- transport ----
    early = re.search(r"\bno\s+(?:early|morning)\s+(?:morning\s+)?flights?\b|"
                      r"\bno flights? before\s+(\d{1,2})", low)
    if early:
        hour = int(early.group(1)) if early.group(1) else 8
        prefs["no_flights_before"] = float(hour)
        chips.append(f"No flights leaving before {hour}:00")
    if re.search(r"\b(no flights?|don'?t (?:want to )?fly|afraid of flying|fear of flying|"
                 r"avoid flying|not fly)\b", low) and not early:
        _add(prefs["avoid_modes"], ["flight"])
        chips.append("No flying")
    if re.search(rf"\b{NEGATION}\s+(?:\w+\s+)?bus(?:es)?\b", low):
        _add(prefs["avoid_modes"], ["bus"])
        chips.append("No long bus rides")

    # ---- positive interests (outside the negated phrases) ----
    positive_text = low
    for a, b in sorted(negated_spans, reverse=True):
        positive_text = positive_text[:a] + " " * (b - a) + positive_text[b:]
    for pattern, interest in POSITIVE_INTEREST_RULES:
        if re.search(rf"\b(?:love|like|enjoy|into|interested in|want|keen on|fan of|for)\b[^.]*?\b(?:{pattern})\b",
                     positive_text):
            _add(prefs["extra_interests"], [interest])

    # ---- named places ----
    for m in MUST_RE.finditer(text):
        name = m.group(1).strip(" .,'")
        if 3 <= len(name) <= 60:
            _add(prefs["must_include"], [name])
    if prefs["must_include"]:
        chips.append("Making sure to include: " + ", ".join(prefs["must_include"][:3]))
    if prefs["extra_interests"]:
        chips.append("Also looking for: " + ", ".join(prefs["extra_interests"]))
    return prefs


LLM_PROMPT = """Read a traveller's notes for an India trip planner and return ONLY a JSON
object with exactly these keys (use null / [] / false when the notes say nothing):

{"diet": null | "vegetarian" | "jain" | "vegan",
 "max_stops_per_day": null | 2 | 3 | 4 | 5 | 6,
 "pace": null | "relaxed" | "packed",
 "avoid_kinds": [subset of: "place of worship","marketplace","mall","bar","pub","nightclub",
                 "peak","viewpoint","museum","beach","waterfall","nature reserve","park",
                 "garden","fort","palace","zoo","spa","event","lake","ruins","monument"],
 "avoid_words": [short lowercase words that must not appear in a stop's name, e.g. "trek"],
 "avoid_modes": [subset of: "flight","train","bus"],
 "no_flights_before": null | hour as a number 0-23,
 "extra_interests": [subset of: %s],
 "must_include": [exact place names the traveller explicitly asked to visit],
 "with_kids": true | false,
 "limited_mobility": true | false}

Only extract what the notes actually say. Never guess."""


def _clean_llm(raw: dict) -> dict:
    """Clip whatever the model returned to values the code knows how to obey."""
    out = empty()
    if not isinstance(raw, dict):
        return out
    diet = raw.get("diet")
    out["diet"] = diet if diet in DIETS else None
    try:
        stops = int(raw.get("max_stops_per_day")) if raw.get("max_stops_per_day") else None
        out["max_stops_per_day"] = stops if stops and 2 <= stops <= 6 else None
    except (TypeError, ValueError):
        pass
    out["pace"] = raw.get("pace") if raw.get("pace") in ("relaxed", "packed") else None
    out["avoid_kinds"] = [k for k in (raw.get("avoid_kinds") or []) if k in KNOWN_KINDS]
    out["avoid_words"] = [w.lower() for w in (raw.get("avoid_words") or [])
                          if isinstance(w, str) and re.fullmatch(r"[a-zA-Z]{3,20}", w)][:6]
    out["avoid_modes"] = [m for m in (raw.get("avoid_modes") or []) if m in KNOWN_MODES]
    try:
        h = raw.get("no_flights_before")
        out["no_flights_before"] = float(h) if h is not None and 0 <= float(h) <= 23 else None
    except (TypeError, ValueError):
        pass
    out["extra_interests"] = [i for i in (raw.get("extra_interests") or []) if i in INTEREST_CHOICES]
    out["must_include"] = [str(n).strip() for n in (raw.get("must_include") or [])
                           if isinstance(n, str) and 3 <= len(n.strip()) <= 60][:5]
    out["with_kids"] = bool(raw.get("with_kids"))
    out["limited_mobility"] = bool(raw.get("limited_mobility"))
    return out


def merge(rules: dict, model: dict) -> dict:
    """Rules win on conflicts; the model can only ADD understanding."""
    out = {**rules}
    for key in ("avoid_kinds", "avoid_words", "avoid_modes", "extra_interests", "must_include"):
        out[key] = list(rules[key])
        _add(out[key], model.get(key, []))
    for key in ("diet", "pace", "no_flights_before"):
        if out.get(key) is None and model.get(key) is not None:
            out[key] = model[key]
    if out.get("max_stops_per_day") is None:
        out["max_stops_per_day"] = model.get("max_stops_per_day")
    out["with_kids"] = rules["with_kids"] or model.get("with_kids", False)
    out["limited_mobility"] = rules["limited_mobility"] or model.get("limited_mobility", False)
    if out["limited_mobility"] and not out.get("max_stops_per_day"):
        out["max_stops_per_day"] = 3
    if out["with_kids"]:
        _add(out["avoid_kinds"], ["bar", "pub", "nightclub"])

    chips = list(rules["understood"])
    extras = []
    if model.get("diet") and not rules.get("diet"):
        extras.append(f"{model['diet'].title()} food for the lunch picks")
    if model.get("max_stops_per_day") and not rules.get("max_stops_per_day"):
        extras.append(f"At most {model['max_stops_per_day']} stops a day")
    new_avoid = [k for k in model.get("avoid_kinds", []) if k not in rules["avoid_kinds"]]
    if new_avoid:
        extras.append("Skipping: " + ", ".join(new_avoid))
    new_must = [n for n in model.get("must_include", []) if n not in rules["must_include"]]
    if new_must:
        extras.append("Making sure to include: " + ", ".join(new_must))
    new_modes = [m for m in model.get("avoid_modes", []) if m not in rules["avoid_modes"]]
    if new_modes:
        extras.append("Avoiding: " + ", ".join(new_modes))
    if model.get("no_flights_before") is not None and rules.get("no_flights_before") is None:
        extras.append(f"No flights leaving before {int(model['no_flights_before'])}:00")
    out["understood"] = chips + [c for c in extras if c not in chips]
    return out


async def understand(notes: str, children: int = 0, trip_id: str | None = None) -> dict:
    """Rules always; the model on top when it is configured and there is text to read."""
    rules = parse_rules(notes, children)
    text = re.sub(r"Group: [^.]*\.", "", notes or "").strip()
    if not config.LLM_ENABLED or len(text) < 6:
        return rules
    try:
        raw = await llm.ask_json(LLM_PROMPT % ", ".join(f'"{i}"' for i in INTEREST_CHOICES),
                                 f"Notes: {text}", temperature=0.0,
                                 label="llm_preferences", trip_id=trip_id)
    except Exception:
        return rules
    return merge(rules, _clean_llm(raw))


def filter_transport(options: list[dict], prefs: dict) -> tuple[list[dict], str]:
    """Drop modes / departure times the traveller ruled out. If that would leave
    nothing, keep everything and say so honestly."""
    if not options:
        return options, ""
    kept = [o for o in options if o.get("mode") not in set(prefs.get("avoid_modes") or [])]
    before = prefs.get("no_flights_before")
    if before is not None:
        def ok(o):
            if o.get("mode") != "flight":
                return True
            try:
                h, m = str(o.get("depart") or "").split(":")[:2]
                return int(h) + int(m) / 60.0 >= before
            except ValueError:
                return True
        kept = [o for o in kept if ok(o)]
    if not kept:
        return options, ("Nothing matched your travel preferences, so every option was "
                         "considered.")
    return kept, ""
