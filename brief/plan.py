"""Driving windows: usual dinner baselines, Michigan home-game split, event cues.

Copied from the legacy app.py (generate_schedule, game_day_blocks and helpers).
Timing is a planning suggestion, never measured demand, surge or profit.
"""
from datetime import datetime, time, timedelta

from .config import OFFER_TARGET_DOLLARS_PER_MILE
from .events import event_impact, local_dt

def offer_target_guidance():
    """A preference for live offer decisions; never inferred from event calendars."""
    return ("Personal goal: at least $" + f"{OFFER_TARGET_DOLLARS_PER_MILE:g} "
            "per estimated total mile, counting pickup and likely unpaid miles. "
            "If offers fall short, waiting or heading home is valid.")

def clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0")

def window(start, end):
    suffix = " (+1 day)" if end.date() > start.date() else ""
    return f"{clock(start)} – {clock(end)}{suffix}"

def event_time_label(event):
    """No fake kickoff time for unannounced games."""
    if event["all_day"]:
        if any(x in event["name"].lower() for x in ("football", "kickoff")) and "michigan stadium" in event["venue"].lower():
            return "Kickoff TBD"
        return "Time TBD / all day"
    return clock(event["start"])

def is_confirmed_michigan_home_game(event):
    """A published local football kickoff; never use inferred midnight for TBDs."""
    return (not event["all_day"] and event["tier"] == "CRITICAL IMPACT"
            and "michigan football" in event["name"].lower()
            and "michigan stadium" in event["venue"].lower())

def game_day_blocks(event, base_camp, *, stop_buffer_minutes=45, return_after_hours=2.0):
    """Advisory split shift, not a claim of surge or actual ride availability.

    Defaults for 3:30 PM: 12:30–2:45 drive, 2:45–5:30 rest, 5:30 onward return.
    """
    if not is_confirmed_michigan_home_game(event):
        raise ValueError("A confirmed Michigan home football kickoff is required")
    if stop_buffer_minutes not in (30, 45, 60) or not 1 <= return_after_hours <= 4:
        raise ValueError("Unsupported game-day planning preferences")
    kickoff = event["start"]
    pre_start = kickoff - timedelta(hours=3)
    pre_end = kickoff - timedelta(minutes=stop_buffer_minutes)
    return_at = max(kickoff + timedelta(hours=return_after_hours),
                    local_dt(datetime.combine(kickoff.date(), time(15))))
    midnight = local_dt(datetime.combine(kickoff.date() + timedelta(days=1), time.min))
    return_end = midnight  # "onward" stays relevant until the selected day ends.
    if pre_end <= pre_start:
        raise ValueError("Pregame window must be positive")
    def minute(dt):
        return ((dt.date() - kickoff.date()).days * 1440
                + dt.hour * 60 + dt.minute)
    signal = event_impact(event)
    blocks = [
        {"time": window(pre_start, pre_end), "app": "Delivery",
         "plan_phase": "pregame",
         "title": "Pregame · delivery first",
         "action": f"Suggested start from {base_camp}: drive roughly three hours before the "
                   f"{clock(kickoff)} Michigan kickoff. Focus on Uber Eats or DoorDash "
                   "deliveries; keep Uber Comfort/XL available only for worthwhile rides. "
                   f"Finish around {clock(pre_end)} and avoid stadium approaches as arrivals peak. "
                   f"Traffic: {signal['traffic']} " + offer_target_guidance() + " "
                   "Check real offers before leaving; event crowds do not guarantee orders.",
         "_minute": minute(pre_start), "_end_minute": minute(pre_end)},
        {"time": window(pre_end, return_at), "app": "Break / Reset",
         "plan_phase": "rest",
         "title": "Head home & recharge",
         "action": f"Suggested break: return toward {base_camp}, eat or rest, and "
                   "avoid stadium congestion. Check live conditions before going back out.",
         "_minute": minute(pre_end), "_end_minute": minute(return_at)},
    ]
    if return_at < midnight:
        blocks.append(
            {"time": f"{clock(return_at)} onward", "app": "Delivery",
              "plan_phase": "return",
             "title": "Optional return · delivery first",
             "action": f"Reassess around {clock(return_at)} after your break. "
                       "Prioritize Uber Eats / DoorDash deliveries; Uber Comfort/XL "
                       "rides are optional, not the reason to reposition. "
                       "Check game progress, actual offers, traffic and legal pickup areas. "
                       + offer_target_guidance() + " "
                       "Only restart or continue if the offers justify it; "
                       "no surge is guaranteed.",
             "_minute": minute(return_at), "_end_minute": minute(return_end)})
    return blocks

def generate_schedule(events, base_camp, day_of_week, *, include_event_cues=True, include_local=True,
                      smart_game_day=True, stop_buffer_minutes=45, return_after_hours=2.0):
    """Chronological driving windows, with local traffic context on by default."""
    timeline = []
    featured_game = next((event for event in events
                          if day_of_week in (5, 6) and is_confirmed_michigan_home_game(event)),
                         None) if smart_game_day else None
    if featured_game is not None:
        timeline.extend(game_day_blocks(featured_game, base_camp,
                                        stop_buffer_minutes=stop_buffer_minutes,
                                        return_after_hours=return_after_hours))
    elif day_of_week == 3:
        timeline.extend([
            {"time": "6:00 PM – 8:30 PM", "app": "Delivery",
              "plan_phase": "dinner",
             "action": "Thursday dinner: consider compact Carpenter / Washtenaw delivery loops. "
                       "Uber Eats / DoorDash are the primary options; "
                       "keep Uber Comfort/XL available for worthwhile requests. "
                       + offer_target_guidance(),
             "_minute": 18 * 60, "_end_minute": 20 * 60 + 30},
            {"time": "8:30 PM onward", "app": "Delivery",
              "plan_phase": "continuation",
             "title": "Optional continuation · delivery first",
             "action": "8:30 PM is a checkpoint, not a required switch to rideshare. "
                       "Continue worthwhile Uber Eats / DoorDash deliveries if available; "
                       "Comfort/XL rides are opportunistic. Check actual app offers "
                       "before driving toward South U / Packard. "
                       + offer_target_guidance(),
             "_minute": 20 * 60 + 30, "_end_minute": 24 * 60},
        ])
    else:
        is_sunday = day_of_week == 6
        timeline.append({
            "time": "4:30 PM – 9:30 PM" if is_sunday else "5:00 PM – 8:00 PM",
            "app": "Delivery",
            "plan_phase": "dinner",
            "action": f"Start from {base_camp}. Prioritize Uber Eats / DoorDash deliveries "
                      "on short Carpenter / Washtenaw loops. Uber Comfort/XL can stay on "
                      "for an especially worthwhile offer. " + offer_target_guidance(),
            "_minute": 16 * 60 + 30 if is_sunday else 17 * 60,
            "_end_minute": 21 * 60 + 30 if is_sunday else 20 * 60,
        })
        if day_of_week in (4, 5):
            timeline.append({
                "time": "8:00 PM onward", "app": "Delivery",
                "plan_phase": "continuation",
                "title": "Optional continuation · delivery first",
                "action": "8 PM is a decision point, not a required shift or automatic "
                          "switch to rideshare. Keep taking worthwhile deliveries on "
                          "Uber Eats / DoorDash; consider Comfort/XL only for good actual "
                          "offers. Check downtown traffic and legal pickup locations "
                          "before repositioning. " + offer_target_guidance() + " "
                          "Otherwise head home; no premium or demand is guaranteed.",
                "_minute": 20 * 60, "_end_minute": 24 * 60,
            })

    for event in events:
        if event is featured_game:
            continue  # Game already creates a pregame/return plan; don't duplicate its window.
        local = event["tier"] == "NEIGHBORHOOD / SCHOOL"
        if local and not include_local or not local and not include_event_cues:
            continue
        start, end = event["start"], event["end"]
        if event["all_day"]:
            label = "Kickoff TBD • timing unconfirmed" if event_time_label(event) == "Kickoff TBD" else "Time TBD • timing unconfirmed"
            sort_at = None
            end_at = None
        else:
            lead = timedelta(hours=3 if event["tier"] == "CRITICAL IMPACT" else 1)
            slot_start = start - lead
            label = window(slot_start, start)
            sort_at = slot_start.hour * 60 + slot_start.minute
            if slot_start.date() < start.date():
                sort_at -= 24 * 60
            end_at = sort_at + int(lead.total_seconds() / 60)
        signal = event_impact(event)
        action = (f"{event['name']} • {event['venue']}. "
                  f"Traffic: {signal['traffic']} "
                  f"Demand: {signal['demand']} "
                  f"Plan: {signal['action']}")
        if end and not event["all_day"]:
            action += f" Published end: {clock(end)}; recheck the area then."
        else:
            action += " Departure time unconfirmed; do not assume a fixed exit window."
        tag = ("NEIGHBORHOOD / SCHOOL · traffic advisory" if local
               else f"{event['tier']} · demand + traffic advisory")
        timeline.append({"time": label, "app": tag, "action": action,
                         "_minute": sort_at, "_end_minute": end_at})
    for slot in timeline:
        if "plan_phase" in slot:
            slot["plan_basis"] = (
                f"Adjusted for confirmed Michigan kickoff ({clock(featured_game['start'])}). "
                "Suggested timing, not measured demand."
                if featured_game is not None else
                "Usual dinner plan. Scheduled timing, not measured demand.")
    timeline.sort(key=lambda item: (item["_minute"] is None,
                                   item["_minute"] if item["_minute"] is not None else 10**9,
                                   item["app"]))
    return [{**{key: value for key, value in item.items()
                if key not in ("_minute", "_end_minute")},
             "start_minute": item["_minute"], "end_minute": item["_end_minute"]}
            for item in timeline]

def roadmap_title(slot):
    """Compact headline, leaving full routing guidance in an expander."""
    if slot.get("title"):
        return slot["title"]
    if slot["app"] == "Delivery":
        return "Delivery window"
    if slot["app"] == "XL / Comfort":
        return "Check rideshare demand"
    return slot["action"].split(" • ", 1)[0]
