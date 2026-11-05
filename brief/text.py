"""Plain-text rendering of a built brief (used by --mode dry-run)."""


def _clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def day_label(day):
    return f"{day.strftime('%a %b')} {day.day}"


def render_day(d):
    lines = [f"== {day_label(d['date'])}" + ("" if d["driving_day"] else " · not a driving day")]
    if d["skip"]:
        lines.append(f"  ⚠ {d['skip']}")
    if d["game_day"]:
        lines.append(f"  Game day: {d['game_day']}")
    for w in d["windows"]:
        tag = " (optional)" if w["optional"] else ""
        lines.append(f"  {w['time']:<22} {w['title']}{tag}")
    if d["weather"]:
        wx = d["weather"]
        temp = f", {wx['temperature']}°F" if wx["temperature"] is not None else ""
        lines.append(f"  Weather: {wx['icon']} {wx['pill']} — {wx['forecast']}{temp}")
        lines.append(f"           {wx['note']}")
    for e in d["kickoff_tbd"]:
        lines.append(f"  Kickoff TBD: {e['name']} — no split shift until a time is published.")
    for e in d["events"]:
        if e["time"] == "Kickoff TBD":
            continue
        lines.append(f"  Event {e['time']:>8}: {e['name']} · {e['venue']}")
        lines.append(f"                  Traffic: {e['traffic']}")
    if not d["events"]:
        lines.append("  Events: none found" + ("" if d["events_complete"] else
                     " (some sources not verified for this day — not an all-clear)"))
    elif not d["events_complete"]:
        lines.append("  Some event sources not verified for this day.")
    if d["offer_rule"]:
        lines.append(f"  {d['offer_rule']}")
    return "\n".join(lines)


def render_sources(sources):
    lines = ["== Sources"]
    for p in sources["weather"]:
        state = (f"OK (NWS updated {p['updated'] or 'unknown'})" if p["ok"]
                 else f"UNAVAILABLE ({p['reason'] or 'unknown reason'})")
        lines.append(f"  NWS hourly · {p['place']}: {state}")
    lines.append(f"  Weather checked {_clock(sources['weather_checked'])}")
    for s in sources["events"]:
        if not s["ok"]:
            state = f"NOT VERIFIED ({s['reason'] or 'unknown reason'})"
        elif s["missing_days"]:
            state = "OK, not visible for " + ", ".join(day_label(d) for d in s["missing_days"])
        else:
            state = "OK"
        lines.append(f"  {s['source']}: {state}")
    lines.append(f"  Events checked {_clock(sources['events_checked'])}")
    lines.extend(f"  Note: {n}" for n in sources["notes"])
    lines.extend(f"  Not covered: {n}" for n in sources.get("not_covered", ()))
    return "\n".join(lines)


def render_text(brief):
    parts = [f"Gig Pilot brief · generated {brief['generated']:%a %b %d %I:%M %p %Z}"]
    parts += [render_day(d) for d in brief["days"]]
    parts.append(render_sources(brief["sources"]))
    return "\n\n".join(parts)
