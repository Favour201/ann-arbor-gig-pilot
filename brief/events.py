"""Public event feeds that may affect demand or traffic.

Ported from the legacy app.py, with one structural change: every parser takes a
range of days, so each source is fetched ONCE per run and its events are then
bucketed by day (instead of ~17 fetches per day for 5 days).

Honesty rules carried over: no invented kickoff times (TBD stays TBD), away and
neutral-site games are dropped, and a source that fails or can't see a day is
reported as "not verified" for that day rather than as "no events".
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
import json
import re

import requests
from bs4 import BeautifulSoup

from .config import TZ
from .http import JSON, get, safe_url


# ---------------------------------------------------------------- time helpers
def local_dt(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def day_bounds(day):
    return local_dt(day), local_dt(day + timedelta(days=1))


def overlaps(start, end, day):
    lower, upper = day_bounds(day)
    return start < upper and end > lower


def horizon(start_day, count):
    """Consecutive days starting at start_day, as a tuple."""
    return tuple(start_day + timedelta(days=i) for i in range(count))


# ------------------------------------------------------------- event records
def classify(venue, name=""):
    venue = venue.lower()
    if "michigan stadium" in venue:
        return "CRITICAL IMPACT" if any(k in name.lower() for k in ("football", "kickoff")) else "NEIGHBORHOOD / SCHOOL"
    if any(x in venue for x in ("rynearson", "george gervin", "convocation", "bowen", "eastern michigan")):
        return "YPSI GAME DAY"
    if any(x in venue for x in ("crisler", "yost")):
        return "INDOOR ARENA"
    if any(x in venue for x in ("hill auditorium", "michigan theater", "michigan theatre", "state theatre", "state theater")):
        return "PERFORMING ARTS"
    return "NEIGHBORHOOD / SCHOOL"


def in_corridor(venue):
    return any(x in venue.lower() for x in (
        "ann arbor", "ypsilanti", "pittsfield", "michigan stadium", "crisler", "yost", "hill auditorium",
        "michigan theater", "michigan theatre", "rynearson", "george gervin", "bowen", "eastern michigan",
        "pioneer high", "huron high", "skyline high", "huron valley"))


def event_record(name, venue, start, end, source, url="", all_day=False, tier=None):
    return {"name": str(name), "venue": str(venue), "start": local_dt(start),
            "end": local_dt(end) if end else None, "source": source, "url": safe_url(url),
            "all_day": all_day, "tier": tier or classify(str(venue), str(name))}


def on_day(event, day):
    end = event["end"] or event["start"] + (timedelta(days=1) if event["all_day"] else timedelta(minutes=1))
    return overlaps(event["start"], end, day)


def on_any(event, days):
    return any(on_day(event, day) for day in days)


KICKOFF_CLOCK = re.compile(
    r"(?<!\d)(?P<hour>1[0-2]|0?[1-9])(?:[:.](?P<minute>[0-5]\d))?\s*"
    r"(?P<meridiem>a\.?m\.?|p\.?m\.?)(?!\w)", re.I)


def resolve_published_start(date_value, published_time, tbd=False):
    """Only set a clock time if explicitly published; date-only/TBA stays untimed."""
    published = local_dt(date_value)
    text = str(published_time or "").strip()
    flag = tbd is True or str(tbd).strip().lower() in ("true", "yes", "1", "tbd", "tba")
    if flag or re.search(r"\b(?:TBD|TBA|TBC|To Be Announced)\b", text, re.I):
        return local_dt(published.date()), True
    match = KICKOFF_CLOCK.search(text)
    if match:
        hour = int(match.group("hour")) % 12
        if match.group("meridiem")[0].lower() == "p":
            hour += 12
        minute = int(match.group("minute") or 0)
        return local_dt(datetime.combine(published.date(), time(hour, minute))), False
    if text.lower() in ("noon", "12 noon"):
        return local_dt(datetime.combine(published.date(), time(12))), False
    if published.time() != time.min:
        return published, False
    return local_dt(published.date()), True


def event_impact(event):
    """Driver-planning advisory, not a forecast of surge or paid trips."""
    tier = event["tier"]
    venue = event["venue"].lower()
    name = event["name"].lower()
    if tier == "CRITICAL IMPACT":
        return {
            "demand": "Potential rideshare and delivery activity around arrivals, breaks and departures; verify in apps.",
            "traffic": "Major stadium approaches and nearby delivery routes may slow significantly.",
            "action": "Check live closures and trip economics before entering Michigan Stadium approaches.",
        }
    if tier in ("YPSI GAME DAY", "INDOOR ARENA"):
        return {
            "demand": "Possible rideshare and delivery activity; crowd size and timing matter.",
            "traffic": "Venue arrivals and exits may slow nearby streets.",
            "action": "Use confirmed pickup zones and check road conditions before repositioning.",
        }
    if tier == "PERFORMING ARTS":
        return {
            "demand": "Possible ride requests near showtime and after performances; food-delivery effects uncertain.",
            "traffic": "Short arrival and dismissal congestion is possible.",
            "action": "Check downtown traffic and legal pickups before moving toward the venue.",
        }
    school_performance = any(x in name for x in
        ("theater", "theatre", "musical", "play", "concert", "choir", "orchestra", "band", "performance", "dance"))
    area = ("Pioneer campus and nearby school entrances" if "pioneer" in venue
            else "Huron campus and nearby school entrances" if "huron" in venue
            else "Skyline campus and nearby school entrances" if "skyline" in venue
            else "the event venue and its immediate approaches")
    return {
        "demand": "Small or uncertain direct rideshare and delivery effect; not a standalone earning signal.",
        "traffic": ("Performance arrival/departure congestion may affect " if school_performance
                    else "Localized event traffic may affect ") + area + ".",
        "action": "Consider routing around " + area + " during arrivals and departures; confirm live traffic.",
    }


# ------------------------------------------------------------------- parsers
def parse_sidearm(html, days, school, sport, source):
    """Parse the public SSR Nuxt schedule table; no private API dependency."""
    node = BeautifulSoup(html, "html.parser").select_one("#__NUXT_DATA__")
    if not node:
        raise ValueError("Schedule structure changed")
    table = json.loads(node.string)
    def deref(index):
        return table[index] if type(index) is int and 0 <= index < len(table) else None
    events, found = [], False
    for item in table:
        if not isinstance(item, dict) or not {"date", "opponent", "location_indicator"} <= item.keys():
            continue
        found = True
        try:
            row = {key: deref(value) for key, value in item.items()}
            if row["location_indicator"] != "H" or not in_corridor(row.get("location") or ""):
                continue  # Away and Detroit neutral-site games do not create local stadium crowds.
            if any(x in str(row.get("game_state_display", "")).lower() for x in ("cancel", "postpon")):
                continue
            start, tbd = resolve_published_start(row["date"], row.get("time"), row.get("tbd"))
            if start.date() not in days:
                continue
            opponent = deref(row["opponent"].get("title")) or "Opponent TBD"
            facility = row.get("facility")
            venue = deref(facility.get("title")) if isinstance(facility, dict) else None
            default_venues = {("Michigan", "football"): "Michigan Stadium", ("Michigan", "mens-ice-hockey"): "Yost Ice Arena",
                              ("Michigan", "mens-basketball"): "Crisler Center", ("Michigan", "womens-basketball"): "Crisler Center",
                              ("EMU", "football"): "Rynearson Stadium"}
            venue = venue or default_venues.get((school, sport), row["location"])
            title = f"{school} {sport.replace('-', ' ').title()} vs. {opponent}"
            events.append(event_record(title, venue, start, None, school + " Athletics", source, tbd,
                                       "YPSI GAME DAY" if school == "EMU" else None))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    if not found:
        raise ValueError("No recognizable schedule records")
    return events


UMICH_PAGE_SIZE = 200
UMICH_MAX_PAGES = 25


def fetch_umich(days):
    events = []
    for page in range(1, UMICH_MAX_PAGES + 1):
        rows = get("https://events.umich.edu/list/json", accept=JSON, params={
            "v": 2, "range": f"{days[0]}to{days[-1]}",
            "max-results": UMICH_PAGE_SIZE, "page": page}).json()
        if not isinstance(rows, list):
            raise ValueError("Unexpected university calendar format")
        for row in rows:
            try:
                venue = row.get("building_name") or row.get("location_name") or ""
                if not in_corridor(venue) or "online" in venue.lower():
                    continue
                event = event_record(row["event_title"], venue, row["datetime_start"],
                                     row.get("datetime_end"), "Happening @ Michigan", row.get("permalink", ""))
                if on_any(event, days):
                    events.append(event)
            except (KeyError, TypeError, ValueError):
                continue
        if len(rows) < UMICH_PAGE_SIZE:
            return events
    raise ValueError("Calendar pagination limit reached; coverage incomplete")


def parse_extra(text, fmt, days, name, source):
    if fmt == "ics":
        from icalendar import Calendar
        import recurring_ical_events
        calendar = Calendar.from_ical(text)
        lower, upper = day_bounds(days[0])[0], day_bounds(days[-1])[1]
        rows = []
        for item in recurring_ical_events.of(calendar).between(lower, upper):
            if str(item.get("STATUS", "")).upper() == "CANCELLED":
                continue
            start = item.decoded("DTSTART")
            rows.append({"name": str(item.get("SUMMARY", "Untitled event")), "venue": str(item.get("LOCATION", "")),
                         "start": start, "end": item.decoded("DTEND") if "DTEND" in item else None,
                         "all_day": not isinstance(start, datetime), "url": str(item.get("URL", source))})
    elif fmt == "json":
        payload = json.loads(text)
        rows = payload.get("events") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("Expected events array")
    elif fmt == "jsonld":
        rows = []
        def walk(obj):
            if isinstance(obj, list):
                for value in obj:
                    walk(value)
            elif isinstance(obj, dict):
                kinds = obj.get("@type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if any(str(k).endswith("Event") for k in kinds) and obj.get("startDate"):
                    if any(x in str(obj.get("eventStatus", "")) for x in ("EventCancelled", "EventPostponed")):
                        return
                    location = obj.get("location", {})
                    venue = location.get("name", "") if isinstance(location, dict) else str(location)
                    rows.append({"name": obj.get("name", "Event"), "venue": venue, "start": obj["startDate"],
                                 "end": obj.get("endDate"), "url": obj.get("url", source)})
                for value in obj.values():
                    if isinstance(value, (dict, list)):
                        walk(value)
        for script in BeautifulSoup(text, "html.parser").select('script[type="application/ld+json"]'):
            try:
                walk(json.loads(script.string or "null"))
            except ValueError:
                continue
        if not rows:
            raise ValueError("No structured event listings; configure JSON or iCalendar feed")
    else:
        raise ValueError("Unsupported calendar format")
    events = []
    for row in rows:
        try:
            if str(row.get("status", "")).lower() in ("cancelled", "canceled", "postponed"):
                continue
            venue = row["venue"]
            if not in_corridor(venue):
                continue
            all_day = row.get("all_day", isinstance(row["start"], str) and len(row["start"]) == 10)
            event = event_record(row["name"], venue, row["start"], row.get("end"), name, row.get("url", source), all_day)
            if on_any(event, days):
                events.append(event)
        except (KeyError, ValueError, TypeError):
            continue
    return events


MONTH_NAME = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
DATE_WORD = re.compile(rf"\b(?P<month>{MONTH_NAME})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,\s*(?P<year>20\d{{2}}))?\s+(?:at|from)\s+(?P<hour>\d{{1,2}})(?::(?P<minute>\d{{2}}))?\s*(?P<meridiem>AM|PM)\b", re.I)
MONTH_SHORT = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
SCHOOL_DATE = re.compile(rf"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*(?P<month>{MONTH_NAME})\s+(?P<day>\d{{1,2}})$", re.I)
SCHOOL_TIME = re.compile(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>AM|PM)\s*-\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM)\b", re.I)
EVENTLINK_DATE = re.compile(rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+(?P<month>{MONTH_SHORT})\.?\s+(?P<day>\d{{1,2}})\s+(?P<year>20\d{{2}})\s+(?:(?P<hour>\d{{1,2}}):(?P<minute>\d{{2}})\s*(?P<meridiem>AM|PM)|TBD)\b", re.I)
SCHOOL_IMPACT_WORDS = ("football", "basketball", "volleyball", "soccer", "concert", "dance", "theatre", "theater", "homecoming", "graduation", "orchestra", "choir", "band", "gymnastics", "hockey", "field hockey", "water polo", "wrestling", "play", "musical", "swimming", "track", "sports", "ceremony", "festival", "competition")


def date_from_match(match, fallback_day, month_format):
    """Parse a published local clock time; the caller decides whether a yearless date is usable."""
    values = match.groupdict()
    year = int(values.get("year") or fallback_day.year)
    month = datetime.strptime(values["month"].title(), month_format).month
    hour = int(values["hour"]) % 12 + (12 if values["meridiem"].upper() == "PM" else 0)
    minute = int(values.get("minute") or 0)
    return local_dt(datetime(year, month, int(values["day"]), hour, minute))


def parse_marquee_events(html, days, source):
    """Best-effort official theater's published, dated live-event cards; no made-up times."""
    soup = BeautifulSoup(html, "html.parser")
    events, recognized = [], 0
    for head in soup.select("h2, h3, h4"):
        name = head.get_text(" ", strip=True)
        if not 3 <= len(name) <= 160 or name.lower() in (
                "live events", "artistic excellence", "penny stamps speaker series",
                "michigan theater", "state theatre", "find us"):
            continue
        context = ""
        for parent in (head.parent, head.parent.parent if head.parent else None,
                       head.parent.parent.parent if head.parent and head.parent.parent else None):
            if parent is None:
                continue
            candidate = parent.get_text(" ", strip=True)
            if len(name) < len(candidate) < 1300 and DATE_WORD.search(candidate):
                context = candidate
                break
        if not context:
            continue
        match = DATE_WORD.search(context)
        if not match:
            continue
        recognized += 1
        if not match.group("year"):
            continue  # Date-only future listings are ambiguous: never assume a year.
        if re.search(r"\b(cancelled|canceled|postponed)\b", context, re.I):
            continue
        try:
            start = date_from_match(match, days[0], "%B")
            if start.date() not in days:
                continue
            href = head.find("a", href=True) or (head.parent.find("a", href=True) if head.parent else None)
            url = href.get("href", "") if href else source
            if url.startswith("/"):
                url = "https://marquee-arts.org" + url
            events.append(event_record(name, "Michigan Theater, Ann Arbor", start, None, "Marquee Arts", url))
        except (ValueError, OverflowError):
            continue
    if not recognized:
        raise ValueError("No recognizable dated theater cards")
    return events


def parse_eventlink_events(html, days, school, source):
    """Read public athletic listing rows; never treat away games outside the corridor as local."""
    soup = BeautifulSoup(html, "html.parser")
    if "EVENTS" not in soup.get_text(" ", strip=True).upper():
        raise ValueError("Athletics listing markup unavailable")
    events, recognized = [], 0
    # Current markup is a table: one cell each for sport, opponent "(H)/(A)",
    # date + time (two <p>s) and venue. Joining each cell's text keeps the
    # date and time together. Older markup had one string per field.
    cells = soup.select("tr td")
    lines = ([c.get_text(" ", strip=True) for c in cells] if cells
             else [v.strip() for v in soup.stripped_strings])
    lines = [line for line in lines if line]
    for i, line in enumerate(lines):
        match = EVENTLINK_DATE.search(line)
        if not match:
            continue
        recognized += 1
        opponent = lines[i - 1] if i > 0 else ""
        venue = lines[i + 1] if i + 1 < len(lines) else ""
        sport = "School athletics"
        for previous in reversed(lines[max(0, i - 9):i]):
            if re.search(r"\b(Football|Soccer|Basketball|Volleyball|Hockey|Field Hockey|Water Polo|Wrestling|Lacrosse|Swimming|Gymnastics|Track|Softball|Baseball|Tennis)\b", previous, re.I):
                sport = previous[:75]
                break
        if not in_corridor(venue) or re.search(r"\b(cancelled|canceled|postponed)\b", opponent, re.I):
            continue
        try:
            tbd = not match.group("hour")
            if tbd:
                start = local_dt(date(int(match.group("year")), datetime.strptime(match.group("month").title(), "%b").month, int(match.group("day"))))
            else:
                start = date_from_match(match, days[0], "%b")
            if start.date() not in days:
                continue
            events.append(event_record(f"{school} • {sport} • {opponent}", venue,
                                       start, None, school + " Athletics", source, all_day=tbd))
        except ValueError:
            continue
    if not recognized:
        raise ValueError("No recognizable athletics dates")
    return events


def parse_school_calendar(html, days, school, source):
    """Read the school's rendered calendar, only for months explicitly visible on the page.

    Returns (events, covered_days): days in a month the page doesn't show are not
    covered, so the brief reports them as unverified instead of "no events".
    """
    soup = BeautifulSoup(html, "html.parser")
    lines = [v.strip() for v in soup.stripped_strings if v.strip()]
    visible = set(lines)
    covered = tuple(d for d in days if d.strftime("%B %Y") in visible)
    if not covered:
        raise ValueError("Selected month not visible in school calendar")
    year_for_month = {d.month: d.year for d in covered}
    current_day, events = None, []
    school_venue = f"{school} High School, Ann Arbor"
    recognized = False
    for i, line in enumerate(lines):
        match = SCHOOL_DATE.fullmatch(line)
        if match:
            try:
                month = datetime.strptime(match.group("month").title(), "%B").month
                current_day = (date(year_for_month[month], month, int(match.group("day")))
                               if month in year_for_month else None)
                recognized = True
            except ValueError:
                current_day = None
            continue
        if current_day not in covered or not any(x in line.lower() for x in SCHOOL_IMPACT_WORDS):
            continue
        if line.lower().strip() in ("huron athletics", "pioneer events", "skyline public calendar"):
            continue
        title = line
        nearby = lines[i + 1:i + 6]
        time_match = next((SCHOOL_TIME.search(v) for v in nearby if SCHOOL_TIME.search(v)), None)
        if not time_match:
            continue  # No usable event time; do not fabricate one.
        venue = next((v for v in nearby if in_corridor(v)), "")
        if " @ " in title and not venue:
            continue  # Away game with no confirmed local venue.
        if not venue:
            venue = school_venue
        try:
            hour = int(time_match.group("hour")) % 12 + (12 if time_match.group("meridiem").upper() == "PM" else 0)
            start = local_dt(datetime.combine(current_day, time(hour, int(time_match.group("minute") or 0))))
            events.append(event_record(f"{school}: {title}", venue, start, None, school + " Calendar", source))
        except ValueError:
            continue
    if not recognized:
        raise ValueError("School calendar dates not readable")
    return events, covered


# ----------------------------------------------------------------- the fetch
# Known gaps, listed on the brief so "no events" is never read as full coverage.
# The A2 school calendars (Finalsite) load their events with JavaScript after the
# page arrives, and refuse non-browser clients (HTTP 406); parse_school_calendar is
# kept for a future iCal feed.
NOT_COVERED = (
    "A2 school calendars (Pioneer / Huron / Skyline concerts, plays, ceremonies): "
    "events load only in a browser, so the brief can't read them.",
)


def source_jobs(extra_json="[]"):
    """(name, format, url, detail) for every source, plus validation notes."""
    jobs = [("U-M campus / Hill Auditorium", "umich", "https://events.umich.edu/", None)]
    for school, host, sports in [
        ("Michigan", "mgoblue.com", ("football", "mens-basketball", "womens-basketball", "mens-ice-hockey", "womens-gymnastics")),
        ("EMU", "emueagles.com", ("football", "mens-basketball", "womens-basketball", "womens-volleyball", "womens-soccer", "womens-gymnastics", "baseball")),
    ]:
        for sport in sports:
            jobs.append((school + " " + sport, "sidearm", f"https://{host}/sports/{sport}/schedule", (school, sport)))
    jobs.extend([
        ("Michigan Theater (Marquee Arts)", "marquee", "https://marquee-arts.org/live-events/", None),
        ("Pioneer Athletics", "eventlink", "https://pioneerathletics.net/Events", "Pioneer"),
    ])
    notes = []
    try:
        extra = json.loads(extra_json or "[]")
        if not isinstance(extra, list):
            raise ValueError()
        for feed in extra:
            if not isinstance(feed, dict) or not all(k in feed for k in ("name", "format", "url")) or not safe_url(feed["url"]):
                raise ValueError()
            jobs.append((feed["name"], feed["format"], feed["url"], None))
    except (ValueError, TypeError):
        notes.append("Additional calendar settings are invalid.")
    return jobs, notes


def _run_job(job, days):
    name, fmt, url, detail = job
    covered = days
    if fmt == "umich":
        events = fetch_umich(days)
    elif fmt == "sidearm":
        events = parse_sidearm(get(url).text, days, *detail, url)
    elif fmt == "marquee":
        events = parse_marquee_events(get(url).text, days, url)
    elif fmt == "eventlink":
        events = parse_eventlink_events(
            get(url, params={"from": days[0].isoformat(), "to": days[-1].isoformat()}).text, days, detail, url)
    elif fmt == "school-calendar":
        events, covered = parse_school_calendar(get(url).text, days, detail, url)
    else:
        events = parse_extra(get(url).text, fmt, days, name, url)
    return events, covered


def failure_reason(exc, limit=160):
    """Short, public-safe reason a source failed (shown in logs and on the page)."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, requests.Timeout):
        return "Timed out"
    if isinstance(exc, requests.ConnectionError):
        return "Connection failed"
    text = " ".join(str(exc).split())
    return (f"{type(exc).__name__}: {text}" if text else type(exc).__name__)[:limit]


def fetch_events(days, extra_json="[]"):
    """Fetch every source once for the whole range. Never raises for a bad source."""
    jobs, notes = source_jobs(extra_json)

    def run(job):
        name, _, url, _ = job
        try:
            events, covered = _run_job(job, days)
            events = [{**e, "feed": name} for e in events]
            return events, {"source": name, "url": url, "ok": True, "covered": tuple(covered),
                            "checked": datetime.now(TZ), "reason": ""}
        except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError,
                IndexError, ImportError) as exc:
            return [], {"source": name, "url": url, "ok": False, "covered": (),
                        "checked": datetime.now(TZ), "reason": failure_reason(exc)}

    events, coverage = [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for found, status in pool.map(run, jobs):
            events.extend(found)
            coverage.append(status)
    unique = {}
    for event in events:
        key = (event["name"].casefold().strip(), event["venue"].casefold().strip(), event["start"])
        unique.setdefault(key, event)
    return {"events": sorted(unique.values(), key=lambda e: (e["start"], e["name"])),
            "coverage": coverage, "notes": notes, "not_covered": NOT_COVERED,
            "checked": datetime.now(TZ), "days": tuple(days)}


def events_for_day(data, day):
    """One day's slice: its events plus per-source status for that day only."""
    events = [e for e in data["events"] if on_day(e, day)]
    coverage = []
    for status in data["coverage"]:
        if not status["ok"]:
            label = "Not verified (blocked / format changed)"
        elif day not in status["covered"]:
            label = "Not verified (date not visible on source)"
        else:
            count = sum(1 for e in events if e.get("feed") == status["source"])
            label = f"Checked • {count} matches"
        coverage.append({**status, "status": label, "verified": status["ok"] and day in status["covered"]})
    return {"events": events, "coverage": coverage, "notes": data["notes"], "checked": data["checked"]}
