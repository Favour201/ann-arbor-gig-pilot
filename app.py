"""Washtenaw Gig Pilot Pro. Run: streamlit run app.py (Python 3.10+).

Optional operator setting (environment or Streamlit secrets):
EVENT_FEEDS_JSON: JSON array of {name, url, format: json|ics|jsonld}.
JSON events: {"events": [{"name", "venue", "start", "end" (optional), "url"}]}
Use local ISO dates for all-day/TBD events, offset-aware timestamps otherwise.
No mock data is ever substituted for a failed live lookup.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from html import escape
import json
import math
import os
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from icalendar import Calendar
import recurring_ical_events
import streamlit as st

MPG = 16.0
TZ = ZoneInfo("America/New_York")
BASE = "2100 Arbor Circle E"
USER_AGENT = "WashtenawGigPilot/2.0 (https://github.com/Favour201/ann-arbor-gig-pilot)"
COLD_F = 15  # Operational threshold, not an official NWS warning criterion.
GASBUDDY_ANN_ARBOR_URL = "https://www.gasbuddy.com/home?search=Ann%20Arbor%2C%20MI&fuel=1"
GASBUDDY_YPSILANTI_URL = "https://www.gasbuddy.com/home?search=Ypsilanti%2C%20MI&fuel=1"
POINTS = {"Ann Arbor": (42.2808, -83.7430), "Ypsilanti": (42.2411, -83.6130)}
TIERS = ("CRITICAL IMPACT", "YPSI GAME DAY", "INDOOR ARENA", "PERFORMING ARTS", "NEIGHBORHOOD / SCHOOL")


def setting(key, default=""):
    if key in os.environ:
        return os.environ[key]
    try:
        return str(st.secrets.get(key, default))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return default


def safe_url(value):
    parsed = urlparse(str(value))
    return str(value) if parsed.scheme == "https" and parsed.hostname and not parsed.username else ""


def get(url, *, params=None, token=""):
    if not safe_url(url):
        raise ValueError("Feed requires an HTTPS URL")
    # Each worker owns its Session; connection retries are bounded.
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=1, backoff_factor=0.3, status_forcelist=[429, 502, 503, 504],
            allowed_methods=["GET"], respect_retry_after_header=False)))
        headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json, text/html, text/calendar"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = session.get(url, params=params, headers=headers, timeout=(4, 10))
        response.raise_for_status()
        return response


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


KICKOFF_CLOCK = re.compile(
    r"(?<!\d)(?P<hour>1[0-2]|0?[1-9])(?:[:.](?P<minute>[0-5]\d))?\s*"
    r"(?P<meridiem>a\.?m\.?|p\.?m\.?)(?!\w)", re.I)


def resolve_published_start(date_value, published_time, tbd=False):
    """Only set a clock time if explicitly published; automatically accept later updates.

    Date-only or TBA schedule entries stay untimed, never silently become midnight
    kickoffs. A dated datetime with nonmidnight time is the secondary official source.
    """
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


def parse_sidearm(html, day, school, sport, source):
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
            if start.date() != day:
                continue
            opponent = deref(row["opponent"].get("title")) or "Opponent TBD"
            facility = row.get("facility")
            venue = deref(facility.get("title")) if isinstance(facility, dict) else None
            default_venues = {("Michigan", "football"): "Michigan Stadium", ("Michigan", "ice-hockey"): "Yost Ice Arena",
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


def fetch_umich(day):
    events = []
    for page in range(1, 11):
        rows = get("https://events.umich.edu/list/json", params={"v": 2, "range": f"{day}to{day}",
                   "max-results": 200, "page": page}).json()
        if not isinstance(rows, list):
            raise ValueError("Unexpected university calendar format")
        for row in rows:
            try:
                venue = row.get("building_name") or row.get("location_name") or ""
                if not in_corridor(venue) or "online" in venue.lower():
                    continue
                events.append(event_record(row["event_title"], venue, row["datetime_start"],
                    row.get("datetime_end"), "Happening @ Michigan", row.get("permalink", "")))
            except (KeyError, TypeError, ValueError):
                continue
        if len(rows) < 200:
            return events
    raise ValueError("Calendar pagination limit reached; coverage incomplete")


def parse_extra(text, fmt, day, name, source):
    if fmt == "ics":
        calendar = Calendar.from_ical(text)
        lower, upper = day_bounds(day)
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
            if on_day(event, day):
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


def date_from_match(match, chosen_day, month_format):
    """Parse published local clock time without inventing a year for a different selected date."""
    values = match.groupdict()
    year = int(values.get("year") or chosen_day.year)
    month = datetime.strptime(values["month"].title(), month_format).month
    hour = int(values["hour"]) % 12 + (12 if values["meridiem"].upper() == "PM" else 0)
    minute = int(values.get("minute") or 0)
    return local_dt(datetime(year, month, int(values["day"]), hour, minute))


def parse_marquee_events(html, day, source):
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
            continue  # Date-only future listings are ambiguous: never assume the selected year.
        if re.search(r"\b(cancelled|canceled|postponed)\b", context, re.I):
            continue
        try:
            start = date_from_match(match, day, "%B")
            if start.date() != day:
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


def parse_eventlink_events(html, day, school, source):
    """Read public athletic listing rows; never treat away games outside the corridor as local."""
    soup = BeautifulSoup(html, "html.parser")
    if "EVENTS" not in soup.get_text(" ", strip=True).upper():
        raise ValueError("Athletics listing markup unavailable")
    events, recognized = [], 0
    # Visible listing text is often more stable across Eventlink website themes than CSS classes.
    lines = [v.strip() for v in soup.stripped_strings if v.strip()]
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
                start = date_from_match(match, day, "%b")
            if start.date() != day:
                continue
            events.append(event_record(f"{school} • {sport} • {opponent}", venue,
                                       start, None, school + " Athletics", source, all_day=tbd))
        except ValueError:
            continue
    if not recognized:
        raise ValueError("No recognizable athletics dates")
    return events


def parse_school_calendar(html, day, school, source):
    """Read the school's rendered calendar, but only the month explicitly visible on the page."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [v.strip() for v in soup.stripped_strings if v.strip()]
    calendar_month = day.strftime("%B %Y")
    if not any(line == calendar_month for line in lines):
        raise ValueError("Selected month not visible in school calendar")
    current_day, events = None, []
    school_venue = f"{school} High School, Ann Arbor"
    recognized = False
    for i, line in enumerate(lines):
        match = SCHOOL_DATE.fullmatch(line)
        if match:
            try:
                current_day = date(day.year, datetime.strptime(match.group("month").title(), "%B").month, int(match.group("day")))
                recognized = True
            except ValueError:
                current_day = None
            continue
        if current_day != day or not any(x in line.lower() for x in SCHOOL_IMPACT_WORDS):
            continue
        # The school calendar sometimes lists the source category separately from its event title.
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
            start = local_dt(datetime.combine(day, time(hour, int(time_match.group("minute") or 0))))
            events.append(event_record(f"{school}: {title}", venue, start, None, school + " Calendar", source))
        except ValueError:
            continue
    if not recognized:
        raise ValueError("School calendar dates not readable")
    return events


@st.cache_data(ttl=1800, max_entries=32, show_spinner=False)
def fetch_live_events(day, extra_json="[]"):
    jobs = [("U-M campus / Hill Auditorium", "umich", "https://events.umich.edu/", None)]
    for school, host, sports in [
        ("Michigan", "mgoblue.com", ("football", "mens-basketball", "womens-basketball", "ice-hockey", "womens-gymnastics")),
        ("EMU", "emueagles.com", ("football", "mens-basketball", "womens-basketball", "volleyball", "womens-soccer", "gymnastics", "baseball")),
    ]:
        for sport in sports:
            jobs.append((school + " " + sport, "sidearm", f"https://{host}/sports/{sport}/schedule", (school, sport)))
    jobs.extend([
        ("Michigan Theater (Marquee Arts)", "marquee", "https://marquee-arts.org/live-events/", None),
        ("Pioneer Athletics", "eventlink", "https://pioneerathletics.net/Events", "Pioneer"),
        ("Pioneer School Calendar", "school-calendar", "https://pioneer.a2schools.org/calendar", "Pioneer"),
        ("Huron School Calendar", "school-calendar", "https://huron.a2schools.org/hurondistrict-calendar", "Huron"),
        ("Skyline School Calendar", "school-calendar", "https://skyline.a2schools.org/calendar", "Skyline"),
    ])
    notes = []
    try:
        extra = json.loads(extra_json)
        if not isinstance(extra, list):
            raise ValueError()
        for feed in extra:
            if not isinstance(feed, dict) or not all(k in feed for k in ("name", "format", "url")) or not safe_url(feed["url"]):
                raise ValueError()
            jobs.append((feed["name"], feed["format"], feed["url"], None))
    except (ValueError, TypeError):
        notes.append("Additional calendar settings are invalid.")

    def run(job):
        name, fmt, url, detail = job
        try:
            if fmt == "umich":
                events = fetch_umich(day)
            elif fmt == "sidearm":
                events = parse_sidearm(get(url).text, day, *detail, url)
            elif fmt == "marquee":
                events = parse_marquee_events(get(url).text, day, url)
            elif fmt == "eventlink":
                events = parse_eventlink_events(get(url, params={"from": day.isoformat(), "to": day.isoformat()}).text, day, detail, url)
            elif fmt == "school-calendar":
                events = parse_school_calendar(get(url).text, day, detail, url)
            else:
                events = parse_extra(get(url).text, fmt, day, name, url)
            return events, {"Source": name, "Status": f"Checked • {len(events)} matches", "URL": url}
        except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError, IndexError):
            return [], {"Source": name, "Status": "Not verified (blocked / date not visible / format changed)", "URL": url}
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
            "coverage": coverage, "notes": notes, "checked": datetime.now(TZ)}


def weather_for_day(periods, day):
    matching, hazards = [], set()
    for period in periods:
        try:
            if not overlaps(local_dt(period["startTime"]), local_dt(period["endTime"]), day):
                continue
            matching.append(period)
            forecast = (str(period.get("shortForecast", "")) + " " + str(period.get("detailedForecast", ""))).lower()
            if re.search(r"\b(rain|showers|drizzle)\b", forecast):
                hazards.add("Rain")
            if re.search(r"\b(thunderstorms?|storms?|tornado|hail)\b", forecast):
                hazards.add("Storms")
            if re.search(r"\b(snow|flurries|blizzard|sleet|freezing rain|ice pellets)\b", forecast):
                hazards.add("Snow / ice")
            temperature = period.get("temperature")
            if isinstance(temperature, (int, float)):
                unit = period.get("temperatureUnit")
                temp_f = temperature * 9 / 5 + 32 if unit == "C" else temperature if unit == "F" else None
                if temp_f is not None and temp_f <= COLD_F:
                    hazards.add(f"Extreme cold (≤{COLD_F}°F)")
        except (ValueError, KeyError, TypeError):
            continue
    return matching, sorted(hazards)


@st.cache_data(ttl=900, max_entries=32, show_spinner=False)
def fetch_weather(day):
    def run(point):
        name, (lat, lon) = point
        try:
            properties = get(f"https://api.weather.gov/points/{lat},{lon}").json()["properties"]
            forecast_url = properties["forecast"]
            if urlparse(forecast_url).hostname != "api.weather.gov":
                raise ValueError("Unexpected forecast host")
            payload = get(forecast_url).json()["properties"]
            periods, hazards = weather_for_day(payload["periods"], day)
            return {"place": name, "periods": periods, "hazards": hazards, "url": forecast_url,
                    "updated": payload.get("updated", ""),
                    "status": "Available" if periods else "Selected date outside available forecast"}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return {"place": name, "periods": [], "hazards": [], "status": "NWS unavailable", "url": ""}
    with ThreadPoolExecutor(max_workers=2) as pool:
        places = list(pool.map(run, POINTS.items()))
    return {"places": places, "hazards": sorted({h for p in places for h in p["hazards"]}), "checked": datetime.now(TZ)}


def calculate_metrics(gross, hours, miles, gas_price):
    values = (gross, hours, miles, gas_price)
    if any(v is None or not math.isfinite(v) or v < 0 for v in values) or hours <= 0 or miles <= 0 or gas_price <= 0:
        return None
    fuel_cost = miles / MPG * gas_price
    mile_rate = gross / miles
    grade = "A (Highly Efficient)" if mile_rate >= 2.5 else "B (Moderate Efficiency)" if mile_rate >= 1.5 else "C (High Burn / Low Return)"
    return {"hourly_rate": round(gross / hours, 2), "mile_rate": round(mile_rate, 2),
            "fuel_cost": round(fuel_cost, 2), "net_profit": round(gross - fuel_cost, 2), "grade": grade}


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
    return_end = min(return_at + timedelta(hours=3), midnight)
    if pre_end <= pre_start:
        raise ValueError("Pregame window must be positive")
    def minute(dt):
        return ((dt.date() - kickoff.date()).days * 1440
                + dt.hour * 60 + dt.minute)
    signal = event_impact(event)
    blocks = [
        {"time": window(pre_start, pre_end), "app": "Delivery",
         "title": "Recommended pregame drive",
         "action": f"Suggested start from {base_camp}: drive roughly three hours before the "
                   f"{clock(kickoff)} Michigan kickoff. Finish around {clock(pre_end)} "
                   "and avoid stadium approaches as arrivals peak. "
                   f"Traffic: {signal['traffic']} "
                   "Use actual delivery/rideshare offers to decide whether to drive.",
         "_minute": minute(pre_start), "_end_minute": minute(pre_end)},
        {"time": window(pre_end, return_at), "app": "Break / Reset",
         "title": "Head home & recharge",
         "action": f"Suggested break: return toward {base_camp}, eat or rest, and "
                   "avoid stadium congestion. Check live conditions before going back out.",
         "_minute": minute(pre_end), "_end_minute": minute(return_at)},
    ]
    if return_at < midnight:
        blocks.append(
            {"time": f"{clock(return_at)} onward", "app": "XL / Comfort",
             "title": "Recommended return shift",
             "action": f"Consider restarting around {clock(return_at)} for dinner delivery and "
                       "potential rideshare requests. Monitor game progress, traffic, "
                       "actual trip offers and legal pickup areas. "
                       "Continue only while trips remain worthwhile; no surge is guaranteed.",
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
             "action": "Thursday dinner: consider compact Carpenter / Washtenaw restaurant loops; target $2.50 per mile.",
             "_minute": 18 * 60, "_end_minute": 20 * 60 + 30},
            {"time": "8:30 PM onward", "app": "XL / Comfort",
             "action": "Check actual app demand around South U / Packard before repositioning.",
             "_minute": 20 * 60 + 30, "_end_minute": 24 * 60},
        ])
    else:
        is_sunday = day_of_week == 6
        timeline.append({
            "time": "4:30 PM – 9:30 PM" if is_sunday else "5:00 PM – 8:00 PM",
            "app": "Delivery",
            "action": f"Start from {base_camp}. Favor short Carpenter / Washtenaw loops and account for unpaid return miles.",
            "_minute": 16 * 60 + 30 if is_sunday else 17 * 60,
            "_end_minute": 21 * 60 + 30 if is_sunday else 20 * 60,
        })
        if day_of_week in (4, 5):
            timeline.append({
                "time": "8:00 PM onward", "app": "XL / Comfort",
                "action": "Check downtown demand and legal pickup locations before moving; premiums are not guaranteed.",
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
    timeline.sort(key=lambda item: (item["_minute"] is None,
                                   item["_minute"] if item["_minute"] is not None else 10**9,
                                   item["app"]))
    return [{**{key: value for key, value in item.items()
                if key not in ("_minute", "_end_minute")},
             "start_minute": item["_minute"], "end_minute": item["_end_minute"]}
            for item in timeline]


def render_weather(weather):
    if weather["hazards"]:
        st.markdown('<div class="weather-premium" role="status"><strong>⚠ Weather Premium Surge Warning</strong><br>'
                    + escape(" • ".join(weather["hazards"]))
                    + '<br><small>Demand may increase. Confirm pricing in your driver app; drive only when conditions permit.</small></div>', unsafe_allow_html=True)
    for place in weather["places"]:
        if place["status"] != "Available":
            st.caption(f"{place['place']}: {place['status']}. This does not indicate clear weather.")
    with st.expander("Weather forecast details"):
        st.caption(f"Checked {weather['checked'].strftime('%b %d, %I:%M %p %Z')} • Cold threshold: {COLD_F}°F")
        for place in weather["places"]:
            st.write(place["place"])
            for period in place["periods"]:
                st.write(f"{period.get('name', '')}: {period.get('temperature', '—')}°{period.get('temperatureUnit', '')} · {period.get('shortForecast', '')}")
            if place["url"]:
                st.link_button("NWS source", place["url"])




def greeting_for_hour(hour):
    """Time-appropriate personalized greeting for the local driver."""
    if not isinstance(hour, int) or not 0 <= hour < 24:
        raise ValueError("hour must be an integer from 0 to 23")
    part = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    return f"{part}, Ogbuefi"


ROADMAP_VIEWS = ("All", "Driving", "Traffic & events")


def filter_roadmap(slots, view="All", *, selected_date=None, now=None, remaining=False):
    """Filter without changing chronology; unknown-time events remain unscheduled."""
    if view not in ROADMAP_VIEWS:
        raise ValueError("Unknown roadmap filter")
    if view == "Driving":
        filtered = [slot for slot in slots if slot["app"] in ("Delivery", "XL / Comfort")]
    elif view == "Traffic & events":
        filtered = [slot for slot in slots if slot["app"] not in ("Delivery", "XL / Comfort")]
    else:
        filtered = list(slots)
    if not remaining:
        return filtered
    if selected_date is None or now is None:
        raise ValueError("Date and current time are required")
    today = now.astimezone(TZ).date()
    if selected_date < today:
        return []
    if selected_date > today:
        return filtered
    minute = now.astimezone(TZ).hour * 60 + now.astimezone(TZ).minute
    return [slot for slot in filtered
            if slot["end_minute"] is None or slot["end_minute"] >= minute]


def next_planning_window(slots, selected_date, now):
    """First active or forthcoming timed window; never assigns times to TBDs."""
    remaining = filter_roadmap(slots, selected_date=selected_date,
                               now=now, remaining=True)
    return next((slot for slot in remaining if slot["start_minute"] is not None), None)


def roadmap_title(slot):
    """Compact headline, leaving full routing guidance in an expander."""
    if slot["app"] == "Delivery":
        return "Delivery window"
    if slot["app"] == "XL / Comfort":
        return "Check rideshare demand"
    return slot["action"].split(" • ", 1)[0]


MIDNIGHT_CSS = """
<style>
:root {color-scheme:dark;}
html,body,[data-testid="stAppViewContainer"],.stApp {background:#101113 !important;color:#F1F0ED !important;}
[data-testid="stHeader"] {background:rgba(17,27,42,.94) !important;}
[data-testid="stSidebar"] {background:#17181A !important;}
.block-container {max-width:960px !important;padding-top:1.25rem !important;padding-bottom:3rem !important;}
h1,h2,h3 {color:#FAF9F6 !important;letter-spacing:-.02em;}
h2 {font-size:1.33rem !important;}
[data-testid="stCaptionContainer"],small {color:#BCBAB5 !important;}
[data-testid="stVerticalBlockBorderWrapper"] {
  background:#202124;border:1px solid #3A3B3F !important;border-radius:15px !important;
}
[data-testid="stMetricLabel"] p {color:#C3C1BC !important;font-size:.81rem;}
[data-testid="stMetricValue"] {font-size:1.55rem !important;color:#F7F6F2 !important;}
[data-testid="stTabs"] [data-baseweb="tab-list"] {gap:.6rem;border-bottom:1px solid #37383C;}
[data-testid="stTabs"] button[data-baseweb="tab"] {
  min-height:48px !important;color:#C2C0BA !important;font-weight:650 !important;
}
[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] {
  color:#E4DFD5 !important;background:#2B2B2E !important;
}
[data-testid="stExpander"] {
  border:1px solid #3E3E41 !important;border-radius:12px !important;background:#1C1D1F;
}
[data-testid="stExpander"] summary {min-height:46px;font-weight:560;color:#ECEAE4;}
.stButton button,.stLinkButton a,[data-testid="stFormSubmitButton"] button {
  min-height:46px !important;border-radius:10px !important;font-weight:650 !important;
}
button:focus-visible,a:focus-visible,input:focus-visible,
[data-testid="stExpander"] summary:focus-visible {
  outline:3px solid #DED8CC !important;outline-offset:3px !important;
}
[data-testid="stAlert"] {border-radius:11px;}
.pilot-hero {
  border:1px solid #49494B;border-radius:20px;
  background:linear-gradient(120deg,#303034 0%,#1B1C1F 60%,#262729 100%);
  padding:22px 24px 20px;margin-bottom:.75rem;
}
.pilot-eyebrow {color:#D4D0C7;font-size:.74rem;font-weight:750;letter-spacing:.13em;text-transform:uppercase;}
.pilot-greeting {color:#FDFCF9;font-size:clamp(1.55rem,3.8vw,2.18rem);font-weight:790;line-height:1.2;margin:.42rem 0;}
.pilot-subtitle {color:#C7C5BF;font-size:.86rem;line-height:1.5;}
.pilot-line {width:72px;height:2px;border-radius:9px;background:#B7B1A6;margin-top:16px;}
.pilot-kicker {color:#D5D0C6;font-size:.75rem;font-weight:770;letter-spacing:.1em;text-transform:uppercase;}
.pilot-timeline-time {color:#E1DCD1;font-size:.93rem;font-weight:750;}
.pilot-next {border-left:3px solid #B7B1A6;padding-left:10px;}
.weather-premium {
  background:#4A3718 !important;color:#FFECC2 !important;
  border:1px solid #C99B3D !important;border-radius:12px;
  padding:14px 16px;margin:12px 0;line-height:1.58;
}
.weather-premium strong,.weather-premium small {color:#FFE9B3 !important;}
@media(max-width:600px){
  .block-container {padding:.85rem .85rem 2.5rem !important;}
  .pilot-hero {padding:18px 16px;border-radius:16px;}
  .pilot-subtitle {font-size:.8rem;}
  [data-testid="stMetricValue"] {font-size:1.3rem !important;}
  [data-testid="stHorizontalBlock"] {gap:.45rem !important;}
  [data-testid="stSegmentedControl"] [role="radiogroup"] {flex-wrap:wrap;}
}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after {transition-duration:0s !important;animation-duration:0s !important;}
}
</style>
"""

def main():
    st.set_page_config(page_title="Midnight Pilot • Washtenaw Gig Pro",
                       page_icon="🛞", layout="centered", initial_sidebar_state="collapsed")
    st.markdown(MIDNIGHT_CSS, unsafe_allow_html=True)
    now = datetime.now(TZ)
    st.markdown(
        '<section class="pilot-hero" aria-label="Driver dashboard">'
        f'<div class="pilot-eyebrow">{escape(now.strftime("%A · %B %d, %Y"))} · PILOT CONTROL</div>'
        f'<div class="pilot-greeting">{escape(greeting_for_hour(now.hour))}</div>'
        '<div class="pilot-subtitle">WASHTENAW GIG PILOT PRO<br>'
        'Ann Arbor ↔ Ypsilanti · Kia Telluride · 16 MPG</div>'
        '<div class="pilot-line" aria-hidden="true"></div></section>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("⚙️ Pilot settings")
        base = st.text_input("Base camp", BASE)
        st.caption("Changing base camp updates routing advice; event coverage stays focused on Ann Arbor / Ypsilanti.")
        st.caption("Weather: 15-minute cache · Events: 30-minute cache.")

    date_col, refresh_col = st.columns([3, 1], gap="small", vertical_alignment="bottom")
    with date_col:
        selected_date = st.date_input("Plan your driving date", now.date())
    with refresh_col:
        if st.button("↻ Refresh", use_container_width=True,
                     help="Recheck event sources and the NWS weather forecast."):
            fetch_live_events.clear()
            fetch_weather.clear()

    with st.spinner("Checking published events and local forecasts…"):
        events_data = fetch_live_events(selected_date, setting("EVENT_FEEDS_JSON", "[]"))
        weather = fetch_weather(selected_date)
    events = events_data["events"]
    major_events = [event for event in events if event["tier"] != "NEIGHBORHOOD / SCHOOL"]
    local_events = [event for event in events if event["tier"] == "NEIGHBORHOOD / SCHOOL"]
    hazards = weather["hazards"]
    weather_available = any(place["status"] == "Available" for place in weather["places"])
    feed_unknown = sum(1 for source in events_data["coverage"]
                       if not source["Status"].startswith("Checked"))
    saved_price = st.session_state.get("checked_gas_price", 0.0)
    current_price = saved_price if isinstance(saved_price, (int, float)) and saved_price >= 0.5 else None

    st.markdown('<div class="pilot-kicker">At a glance · selected date</div>', unsafe_allow_html=True)
    fuel_tile, events_tile, weather_tile = st.columns(3, gap="small")
    with fuel_tile:
        with st.container(border=True):
            st.metric("⛽ Gas price", f"${current_price:.2f}" if current_price is not None else "—")
            st.caption("Your checked price" if current_price is not None else "Not entered")
    with events_tile:
        with st.container(border=True):
            st.metric("📅 Events reported", str(len(events)))
            st.caption(f"{len(major_events)} major · {len(local_events)} local" +
                       (" · partial feeds" if feed_unknown else ""))
    with weather_tile:
        with st.container(border=True):
            st.metric("🌦️ Weather triggers", str(len(hazards)) if hazards else
                      ("0" if weather_available else "—"))
            st.caption(" • ".join(hazards[:2]) if hazards else
                       ("No premium triggers" if weather_available else "Forecast unavailable"))

    with st.expander("⛽ Fuel quick check · GasBuddy", expanded=False):
        st.caption("Use a recent regular-gas price from GasBuddy or the pump. No automatic price scraping.")
        first, second = st.columns(2, gap="small")
        with first:
            st.link_button("GasBuddy · Ann Arbor ↗", GASBUDDY_ANN_ARBOR_URL, use_container_width=True)
        with second:
            st.link_button("GasBuddy · Ypsilanti ↗", GASBUDDY_YPSILANTI_URL, use_container_width=True)
        st.number_input("Gas price you checked ($/gal)",
                        min_value=0.0, max_value=15.0, value=0.0, step=0.01, key="checked_gas_price",
                        help="Stored only during this Streamlit session. Enter 0 if unavailable.")
        st.caption("Check report age, membership, fuel grade and cash-only pricing.")
    live_gas = st.session_state.get("checked_gas_price", 0.0)
    live_gas = live_gas if isinstance(live_gas, (int, float)) and live_gas >= 0.5 else None

    tab1, tab2 = st.tabs(["🗺️ Roadmap & events", "📊 Weekly profit"])
    with tab1:
        st.subheader("Position & Routing Roadmap")
        st.caption(selected_date.strftime("%A, %B %d, %Y") +
                   " · Chronological windows · Filters are optional")
        render_weather(weather)
        with st.expander("⚙️ Roadmap filters", expanded=False):
            include_events = st.checkbox("Include major-event demand + traffic cues", value=True,
                                         help="Events do not guarantee profitable rides or delivery requests.")
            include_local = st.checkbox("Show school / neighborhood traffic advisories", value=True,
                                        help="Traffic avoidance cues independent of major-event targeting.")

        schedule = generate_schedule(events, base, selected_date.weekday(),
                                     include_event_cues=include_events, include_local=include_local)
        upcoming = next_planning_window(schedule, selected_date, now)
        with st.container(border=True):
            st.caption("CURRENT / NEXT LISTED WINDOW" if selected_date == now.date()
                       else "FIRST PLANNING WINDOW" if selected_date > now.date()
                       else "HISTORICAL DATE")
            if upcoming:
                st.markdown(f"**{roadmap_title(upcoming)}** · {upcoming['time']}")
                st.caption("Potential planning window, not an obligation to drive.")
            else:
                st.caption("No remaining timed windows on this date; any TBD events are listed below."
                           if selected_date >= now.date() else
                           "Choose today or a future date for an upcoming window.")

        st.segmented_control("Roadmap focus", ROADMAP_VIEWS,
                             default="All", key="roadmap_view",
                             help="View regular driving windows, traffic/event cues, or both.")
        remaining_only = st.toggle("Show remaining windows only", value=False,
                                   help="On today's date, hide windows that have already ended. TBD events remain unassigned.")
        displayed_schedule = filter_roadmap(
            schedule, st.session_state["roadmap_view"],
            selected_date=selected_date, now=now, remaining=remaining_only)
        st.caption(f"{len(displayed_schedule)} windows shown · Chronological order preserved")
        for slot in displayed_schedule:
            with st.container(border=True):
                left, right = st.columns([1.15, 2.7], gap="small", vertical_alignment="top")
                with left:
                    st.markdown(f'<span class="pilot-timeline-time">{escape(slot["time"])}</span>',
                                unsafe_allow_html=True)
                with right:
                    st.markdown(f"**{roadmap_title(slot)}**")
                    st.caption(slot["app"])
                with st.expander("Routing details", expanded=False):
                    st.write(slot["action"])
        if not displayed_schedule:
            st.info("No planning windows match this view or date. Try All or turn off Remaining only.")

        st.subheader("Regional event footprint")
        st.caption("Published schedules identify possible crowds and congestion, not guaranteed surge.")
        for note in events_data["notes"]:
            st.warning(note)
        if not events:
            st.info("No matching events from available sources. This does not confirm normal traffic.")

        with st.expander(f"🎟️ Major events ({len(major_events)})", expanded=False):
            for event in major_events:
                with st.container(border=True):
                    st.caption(event["tier"] + " · Demand and traffic")
                    st.write(f"**{event['name']}**")
                    st.write(f"{event['venue']} · {event_time_label(event)}")
                    st.caption("Traffic: " + event_impact(event)["traffic"])
                    if event["url"]:
                        st.link_button("Official event source ↗", event["url"])
            if not major_events:
                st.caption("No matching major event returned from available sources.")
        with st.expander(f"🚦 School / neighborhood alerts ({len(local_events)})", expanded=False):
            st.caption("Traffic avoidance context for school performances and other local events.")
            for event in local_events:
                with st.container(border=True):
                    st.write(f"**{event['name']}**")
                    st.write(f"{event['venue']} · {event_time_label(event)}")
                    st.caption("Traffic: " + event_impact(event)["traffic"])
                    if event["url"]:
                        st.link_button("Calendar source ↗", event["url"])
            if not local_events:
                st.caption("No matching local events returned; coverage can be incomplete.")
        with st.expander("Source coverage & timestamps", expanded=False):
            st.dataframe(events_data["coverage"], hide_index=True, use_container_width=True)
            st.caption(f"Checked {events_data['checked'].strftime('%b %d %I:%M %p %Z')}. "
                       "Official Michigan kickoff announcements update on successful refresh. "
                       "Blocked or limited feeds do not prove quiet streets.")

    with tab2:
        st.subheader("Weekly Profit Grader")
        st.caption("Fuel-only estimate: taxes, maintenance, insurance and depreciation are excluded.")
        if live_gas is not None:
            st.info(f"Using your checked regular gas price: ${live_gas:.2f}/gal · 16 MPG.")
        else:
            st.info("Open Fuel quick check above and enter a checked price to enable grading.")
        with st.form("grader_form"):
            gross = st.number_input("Gross earnings ($)", min_value=0.0, value=340.90)
            hours = st.number_input("Total online hours", min_value=0.0, value=10.1)
            miles = st.number_input("All driven miles, including deadhead", min_value=0.0, value=98.6)
            submitted = st.form_submit_button("Grade my week", use_container_width=True,
                                              disabled=live_gas is None)
        if submitted:
            st.session_state["last_shift"] = (gross, hours, miles)
        if "last_shift" in st.session_state:
            metrics = calculate_metrics(*st.session_state["last_shift"], live_gas)
            if metrics:
                st.metric("Efficiency grade", metrics["grade"])
                first, second = st.columns(2, gap="small")
                with first:
                    with st.container(border=True):
                        st.metric("Gross hourly", f"${metrics['hourly_rate']:,.2f}/hr")
                with second:
                    with st.container(border=True):
                        st.metric("Gross per mile", f"${metrics['mile_rate']:,.2f}/mi")
                st.metric("Fuel expense", f"${metrics['fuel_cost']:,.2f}")
                st.success(f"After-fuel earnings: ${metrics['net_profit']:,.2f}")
                st.caption(f"Last submitted shift, recalculated at ${live_gas:.2f}/gal and {MPG:g} MPG.")
            else:
                st.warning("Enter positive hours and miles and provide an available fuel price.")


if __name__ == "__main__":
    main()
