"""Compose the 3–5 line phone push for one day of the brief.

Same honesty rules as the page: weather UNKNOWN is said out loud, missing
event sources are flagged, no demand/surge/profit claims, no address.
"""
from .config import OFFER_TARGET_DOLLARS_PER_MILE
from .text import day_label

MAX_LINES = 5
TIER_ORDER = ("CRITICAL IMPACT", "YPSI GAME DAY", "INDOOR ARENA", "PERFORMING ARTS",
              "NEIGHBORHOOD / SCHOOL")


def _short_time(window_time):
    """'6:00 PM – 8:30 PM' -> '6:00–8:30 PM'; leaves other labels alone."""
    parts = window_time.split(" – ")
    if len(parts) == 2 and parts[0][-2:] == parts[1][-2:]:
        return f"{parts[0][:-3]}–{parts[1]}"
    return window_time.replace(" – ", "–")


def plan_line(day):
    windows = day["windows"]
    if not day["driving_day"]:
        return "Not a driving day."
    if day["game_day"]:
        parts = []
        for w in windows:
            if w["kind"] == "rest":
                parts.append(f"rest {_short_time(w['time'])}")
            elif w["phase"] == "pregame":
                parts.append(f"drive {_short_time(w['time'])}")
            else:
                parts.append(f"optional return {w['time'].replace(' onward', '+')}")
        return "Game day: " + " · ".join(parts)
    fixed = [w for w in windows if w["kind"] == "drive" and not w["optional"]]
    optional = any(w["optional"] for w in windows)
    text = "Delivery " + ", ".join(_short_time(w["time"]) for w in fixed)
    return text + (", optional after" if optional else "")


def weather_line(day):
    wx = day["weather"]
    if wx is None:
        return None
    if wx["severity"] == "unknown":
        return "Weather: unknown for driving hours. Check a forecast before leaving."
    temp = f", {wx['temperature']}°F" if wx["temperature"] is not None else ""
    label = {"good": "Low impact", "caution": "Caution", "high": "HIGH IMPACT"}[wx["severity"]]
    return f"Weather: {wx['icon']} {label}, {wx['forecast'].split(' · ')[0]}{temp}"


def _event_short(e):
    venue = e["venue"].split(",")[0].replace(" Center", "").replace(" Ice Arena", "")
    return f"{e['name']} {e['time']} ({venue})"


def events_line(day):
    ranked = sorted(day["events"], key=lambda e: (TIER_ORDER.index(e["tier"])
                                                  if e["tier"] in TIER_ORDER else 99, e["start"]))
    unverified = "" if day["events_complete"] else " (some sources unverified)"
    if not ranked:
        return "Events: none found" + unverified
    shown = "; ".join(_event_short(e) for e in ranked[:2])
    more = f" +{len(ranked) - 2} more" if len(ranked) > 2 else ""
    return f"Events: {shown}{more}{unverified}"


def compose_push(day, page_url=""):
    """Return dict(title, message, priority, tags, click) for ntfy."""
    lines = []
    if day["skip"]:
        lines.append("⚠ " + day["skip"])
    lines.append(plan_line(day))
    if (w := weather_line(day)):
        lines.append(w)
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
    }
