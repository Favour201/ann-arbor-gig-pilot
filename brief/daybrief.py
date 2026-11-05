"""Assemble one day's brief from already-fetched events and forecast data.

Pure functions (no network), so every honesty rule here is unit-tested:
- unknown weather stays unknown; a missing city never yields an all-clear;
- kickoff TBD stays TBD; no split shift without a published kickoff;
- the skip flag is a suggestion, and only fires on red weather (on-call in M2).
"""
from . import weather_outlook
from .config import BASE_LABEL, DRIVING_WEEKDAYS, HORIZON_DAYS, OFFER_TARGET_DOLLARS_PER_MILE
from .events import event_impact, events_for_day, horizon
from .plan import event_time_label, generate_schedule, roadmap_title
from .weather import weather_for_day

DRIVING_APPS = ("Delivery", "XL / Comfort")
OPTIONAL_PHASES = ("continuation", "return")

OFFER_RULE = (f"Offer rule: ${OFFER_TARGET_DOLLARS_PER_MILE:g}+ per estimated total mile "
              "(pickup + likely unpaid miles count).")


def _event_view(event):
    impact = event_impact(event)
    return {"name": event["name"], "venue": event["venue"], "time": event_time_label(event),
            "tier": event["tier"], "traffic": impact["traffic"], "action": impact["action"],
            "url": event["url"], "start": event["start"], "all_day": event["all_day"]}


def _windows(slots):
    windows = []
    for slot in slots:
        if slot["app"] not in DRIVING_APPS and slot["app"] != "Break / Reset":
            continue
        windows.append({
            "time": slot["time"],
            "title": roadmap_title(slot),
            "kind": "rest" if slot["app"] == "Break / Reset" else "drive",
            "optional": slot.get("plan_phase") in OPTIONAL_PHASES,
            "phase": slot.get("plan_phase", ""),
            "basis": slot.get("plan_basis", ""),
            "detail": slot["action"],
        })
    return windows


def build_day(day, now, events_data, forecast):
    day_events = events_for_day(events_data, day)
    driving = day.weekday() in DRIVING_WEEKDAYS
    events = sorted((_event_view(e) for e in day_events["events"]),
                    key=lambda e: (e["all_day"], e["start"], e["name"]))
    result = {
        "date": day, "driving_day": driving, "events": events,
        "event_coverage": day_events["coverage"],
        "events_complete": all(c["verified"] for c in day_events["coverage"]),
        "windows": [], "weather": None, "skip": None, "game_day": None,
        "kickoff_tbd": [e for e in events if e["time"] == "Kickoff TBD"],
        "offer_rule": OFFER_RULE if driving else None,
    }
    if not driving:
        return result

    slots = generate_schedule(day_events["events"], BASE_LABEL, day.weekday())
    result["windows"] = _windows(slots)
    pregame = next((w for w in result["windows"] if w["phase"] == "pregame"), None)
    if pregame:
        result["game_day"] = pregame["basis"]

    scoped = weather_outlook.weather_for_work(weather_for_day(forecast, day), slots, day, now)
    outlook = weather_outlook.summarize_weather_outlook(scoped)
    outlook["window_label"] = scoped["window_label"]
    result["weather"] = outlook
    if outlook["severity"] == "high":
        result["skip"] = ("Skip tonight? Red weather during driving hours. "
                          "A suggestion, not a forecast.")
    return result


def build_brief(now, events_data, forecast, *, days=HORIZON_DAYS):
    """Today plus the next days, and a sources section with checked-at stamps."""
    span = horizon(now.date(), days)
    return {
        "generated": now,
        "days": [build_day(day, now, events_data, forecast) for day in span],
        "sources": {
            "events": [{"source": c["source"], "url": c["url"], "ok": c["ok"],
                        "checked": c["checked"], "reason": c.get("reason", ""),
                        "missing_days": [d for d in span if d not in c["covered"]] if c["ok"] else list(span)}
                       for c in events_data["coverage"]],
            "weather": [{"place": p["place"], "ok": p["ok"], "updated": p["updated"],
                         "url": p["url"], "reason": p.get("reason", "")} for p in forecast["places"]],
            "weather_checked": forecast["checked"],
            "events_checked": events_data["checked"],
            "notes": events_data["notes"],
            "not_covered": events_data.get("not_covered", ()),
        },
    }
