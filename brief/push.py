"""Compose the 3–5 line phone push for one day of the brief.

Same honesty rules as the page: weather UNKNOWN is said out loud, missing
event sources are flagged, no demand/surge/profit claims, no address.
"""
import re

from .config import OFFER_TARGET_DOLLARS_PER_MILE
from .text import day_label

MAX_LINES = 5
TIER_ORDER = ("CRITICAL IMPACT", "YPSI GAME DAY", "INDOOR ARENA", "PERFORMING ARTS",
              "NEIGHBORHOOD / SCHOOL")


def _short_time(window_time):
    """'6:00 PM – 8:30 PM' -> '6:00–8:30 PM'; leaves other labels alone."""
    window_time = window_time.replace(" (+1 day)", "")
    parts = window_time.split(" – ")
    if len(parts) == 2 and parts[0][-2:] == parts[1][-2:]:
        return f"{parts[0][:-3]}–{parts[1]}"
    return window_time.replace(" – ", "–")


GAME_PHASE_LABEL = {"pregame": "drive", "ingame": "deliveries", "rest": "rest",
                    "postgame": "post-game rides"}


def game_parts(day):
    parts = []
    for w in day["windows"]:
        if w["phase"] == "return":
            parts.append(f"optional return {w['time'].replace(' onward', '+')}")
        elif w["phase"] in GAME_PHASE_LABEL:
            parts.append(f"{GAME_PHASE_LABEL[w['phase']]} {_short_time(w['time'])}")
    return parts


def plan_line(day):
    windows = day["windows"]
    if not day["driving_day"]:
        return "Not a driving day."
    if day["game_day"]:
        kickoff = f" ({day['game']['kickoff']} kickoff)" if day.get("game") else ""
        line = f"Game day{kickoff}: " + " · ".join(game_parts(day))
        return line + (" (game end estimated)" if (day.get("game") or {}).get("night") else "")
    fixed = [w for w in windows if w["kind"] == "drive" and not w["optional"]]
    optional = any(w["optional"] for w in windows)
    text = "Delivery " + ", ".join(_short_time(w["time"]) for w in fixed)
    return text + (", optional after" if optional else "")


def _is_big_game(e):
    """Michigan home football (published or TBD) and EMU home football."""
    name = e["name"].lower()
    return e["tier"] == "CRITICAL IMPACT" or (e["tier"] == "YPSI GAME DAY" and "football" in name)


def _game_ahead(day):
    dow = day["date"].strftime("%a")
    if day.get("game"):
        return f"{dow} {day['game']['name']} {day['game']['kickoff']}: " + ", ".join(game_parts(day))
    games = [e for e in day["events"] if _is_big_game(e)]
    return "; ".join(f"{dow} {e['name']}, " + ("kickoff TBD (plan once it's published)"
                                               if e["time"] == "Kickoff TBD" else e["time"])
                     for e in games[:2])


def ahead_line(today, later_days):
    """Thursday: heads-up for Fri–Sun big games. Other driving days: tomorrow only."""
    if not today["driving_day"]:
        return None
    scan = later_days[:3] if today["date"].weekday() == 3 else later_days[:1]
    found = [text for d in scan if d["driving_day"] and (text := _game_ahead(d))]
    if not found:
        return None
    label = "This weekend" if today["date"].weekday() == 3 else "Tomorrow"
    return f"{label}: " + " | ".join(found)


def weather_line(day):
    wx = day["weather"]
    if wx is None:
        return None
    if wx["severity"] == "unknown":
        return "Weather: unknown for driving hours. Check a forecast before leaving."
    temp = f", {wx['temperature']}°F" if wx["temperature"] is not None else ""
    label = {"good": "Low impact", "caution": "Caution", "high": "HIGH IMPACT"}[wx["severity"]]
    return f"Weather: {wx['icon']} {label}, {wx['forecast'].split(' · ')[0]}{temp}"


HIGH_SCHOOLS = ("Pioneer", "Huron", "Skyline")
SCHOOL_EVENT = re.compile(r"^(?P<school>\w+) • (?P<sport>.+?) \((?P<group>[^)]*)\) • (?P<opp>.+)$")


def is_high_school(e):
    return e["name"].startswith(HIGH_SCHOOLS) or e["venue"].startswith(tuple(f"{s} High" for s in HIGH_SCHOOLS))


def short_name(name):
    """'Pioneer • Hockey (Girls V) • Academy of the Sacred Heart (H)' -> 'Pioneer girls hockey vs Academy of the Sacred Heart'."""
    match = SCHOOL_EVENT.match(name)
    if not match:
        return name
    group = match.group("group").lower()
    who = "girls " if "girl" in group else "boys " if "boy" in group else ""
    opponent = re.sub(r"\s*\((?:H|A|N)\)\s*$", "", match.group("opp"))
    return f"{match.group('school')} {who}{match.group('sport').lower()} vs {opponent}"


def short_venue(venue):
    inner = re.search(r"\(([^)]+)\)", venue)  # 'Pioneer High School (Yost Ice Arena)' -> 'Yost Ice Arena'
    venue = inner.group(1) if inner else venue.split(",")[0]
    return venue.replace(" Center", "").replace(" Ice Arena", "")


def event_rank(e):
    tier = TIER_ORDER.index(e["tier"]) if e["tier"] in TIER_ORDER else 99
    return (tier, is_high_school(e), e["start"])


def _event_short(e):
    return f"{short_name(e['name'])} {e['time']} ({short_venue(e['venue'])})"


def events_line(day):
    ranked = sorted(day["events"], key=event_rank)
    unverified = "" if day["events_complete"] else " (some sources unverified)"
    if not ranked:
        return "Events: none found" + unverified
    shown = "; ".join(_event_short(e) for e in ranked[:2])
    more = f" +{len(ranked) - 2} more" if len(ranked) > 2 else ""
    return f"Events: {shown}{more}{unverified}"


def compose_push(day, page_url="", later_days=()):
    """Return dict(title, message, priority, tags, click, date) for ntfy.

    Line order is priority order; the tail is cut first when over MAX_LINES.
    """
    lines = []
    if day["skip"]:
        lines.append("⚠ " + day["skip"])
    lines.append(plan_line(day))
    if (w := weather_line(day)):
        lines.append(w)
    if (a := ahead_line(day, list(later_days))):
        lines.append(a)
    lines.append(events_line(day))
    if day["driving_day"]:
        lines.append(f"Offer rule: ${OFFER_TARGET_DOLLARS_PER_MILE:g}+ per est. total mile")
    lines = lines[:MAX_LINES]
    high = bool(day["skip"])
    return {
        "title": f"{day_label(day['date'])} · Gig brief",
        "message": "\n".join(lines),
        "priority": 4 if high else 3,
        "tags": ["warning"] if high else ["car"],
        "click": page_url,
        "date": day["date"].isoformat(),
    }


OFFER_PREFIX = "Offer rule:"


def add_private_lines(push, extra):
    """Add push-only private lines (after the plan line) within MAX_LINES.

    The offer-rule reminder is the first line dropped to make room. Called
    right before sending; the result must never be logged or saved.
    """
    if not extra:
        return push
    lines = push["message"].split("\n")
    at = next((i + 1 for i, line in enumerate(lines) if not line.startswith("⚠")), len(lines))
    lines[at:at] = extra
    while len(lines) > MAX_LINES and any(line.startswith(OFFER_PREFIX) for line in lines):
        lines = [line for line in lines if not line.startswith(OFFER_PREFIX)]
    return {**push, "message": "\n".join(lines[:MAX_LINES])}
