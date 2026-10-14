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
import hmac
import statistics
import pilot_features as pilot
import decision_tools as decision
import gridwise_import as gridwise
import weather_outlook as weather_ui
import session_security as security

MPG = 16.0
OFFER_TARGET_DOLLARS_PER_MILE = 3.0  # Driver preference, not a predicted rate.


def offer_target_guidance():
    """A preference for live offer decisions; never inferred from event calendars."""
    return ("Personal goal: at least $" + f"{OFFER_TARGET_DOLLARS_PER_MILE:g} "
            "per estimated total mile, counting pickup and likely unpaid miles. "
            "If offers fall short, waiting or heading home is valid.")


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


def render_weather(weather, *, details=True):
    """Compact graphite NWS outlook for the selected date; never forecast surge."""
    outlook = weather_ui.summarize_weather_outlook(weather)
    severity = outlook["severity"]
    temp = outlook["temperature"]
    if temp is None:
        temp_html = '<span class="pilot-weather-no-temp">Temperature unavailable</span>'
    else:
        values = f"Forecast {temp}°F"
        if outlook["high"] is not None and outlook["low"] is not None:
            values += f'<small>Forecast high {outlook["high"]}° · low {outlook["low"]}°</small>'
        else:
            values += '<small>Selected forecast period</small>'
        temp_html = '<span class="pilot-weather-temp">' + escape(values.split("<small>")[0])
        if "<small>" in values:
            temp_html += "<small>" + escape(values.split("<small>", 1)[1].split("</small>", 1)[0]) + "</small>"
        temp_html += "</span>"

    period_title = ((outlook["period_name"] + " · ") if outlook["period_name"] else "")
    place = (outlook["place"] + " · ") if outlook["place"] else ""
    st.markdown(
        '<div class="pilot-weather-card pilot-weather-' + escape(severity) + '" role="status">'
        '<div class="pilot-weather-kicker">WEATHER DRIVING OUTLOOK</div>'
        '<div class="pilot-weather-head">'
        '<div><div class="pilot-weather-label">' + escape(outlook["icon"]) + " "
        + escape(outlook["title"]) + '</div>'
        '<span class="pilot-weather-pill">' + escape(outlook["pill"]) + '</span></div>'
        + temp_html + '</div>'
        '<div class="pilot-weather-forecast">' + escape(period_title + outlook["forecast"]) + '</div>'
        '<div class="pilot-weather-note">' + escape(outlook["note"]) + '</div>'
        '<div class="pilot-weather-source">' + escape(place + "NWS forecast · selected driving date")
        + '</div></div>',
        unsafe_allow_html=True,
    )
    if details:
        with st.expander("Weather forecast details"):
            st.caption(f"Checked {weather['checked'].strftime('%b %d, %I:%M %p %Z')} • Cold threshold: {COLD_F}°F")
            for place in weather["places"]:
                st.write(place["place"])
                if place["status"] != "Available":
                    st.caption(place["status"] + " · This does not indicate clear weather.")
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
        filtered = [slot for slot in slots if slot["app"] in ("Delivery", "XL / Comfort", "Break / Reset")]
    elif view == "Traffic & events":
        filtered = [slot for slot in slots if slot["app"] not in ("Delivery", "XL / Comfort", "Break / Reset")]
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
            if slot["end_minute"] is None or slot["end_minute"] > minute]


def next_planning_window(slots, selected_date, now):
    """First active or forthcoming timed window; never assigns times to TBDs."""
    remaining = filter_roadmap(slots, selected_date=selected_date,
                               now=now, remaining=True)
    return next((slot for slot in remaining if slot["start_minute"] is not None), None)


def planning_headline(slots, selected_date, now):
    """Describe the plan's current phase without implying verified demand."""
    local_now = now.astimezone(TZ)
    if selected_date < local_now.date():
        return {"title": "Past plan · review only", "slot": None,
                "basis": "", "note": "Choose today or a future date to plan driving."}
    slot = next_planning_window(filter_roadmap(slots, "Driving"), selected_date, now)
    if slot is None:
        return {"title": "No more planned windows today" if selected_date == local_now.date()
                else "No timed windows planned", "slot": None, "basis": "",
                "note": "The schedule does not tell us whether offers are available."}
    start = datetime.combine(selected_date, time.min, tzinfo=TZ) + timedelta(
        minutes=slot["start_minute"])
    active = selected_date == local_now.date() and start <= local_now
    phase = slot.get("plan_phase")
    if slot["app"] == "Break / Reset":
        title = "Rest now" if active else f"Rest starts at {clock(start)}"
        note = "Take a break. Reassess offers, traffic and weather before any optional return."
    elif phase in ("return", "continuation"):
        label = "Optional return" if phase == "return" else "Optional continuation"
        title = label if active else f"{label} at {clock(start)}"
        note = "Continue only if actual offers and conditions justify it; heading home is fine."
    else:
        label = "Pregame window" if phase == "pregame" else (
            "Dinner window" if phase == "dinner" else "Driving window")
        title = f"{label} is open" if active else f"{label} starts at {clock(start)}"
        note = "Check actual offers, traffic and road conditions before leaving."
    return {"title": title, "slot": slot,
            "basis": slot.get("plan_basis", "Suggested schedule, not measured demand."),
            "note": note}


def roadmap_title(slot):
    """Compact headline, leaving full routing guidance in an expander."""
    if slot.get("title"):
        return slot["title"]
    if slot["app"] == "Delivery":
        return "Delivery window"
    if slot["app"] == "XL / Comfort":
        return "Check rideshare demand"
    return slot["action"].split(" • ", 1)[0]


MIDNIGHT_CSS = """
<style>
:root {color-scheme:dark;}
html,body,[data-testid="stAppViewContainer"],.stApp {background:#101113 !important;color:#F1F0ED !important;}
[data-testid="stHeader"] {background:transparent !important;box-shadow:none !important;}
/* Keep the sidebar toggle accessible. Only remove the optional top-right toolbar. */
[data-testid="stToolbar"] {display:none !important;}
[data-testid="stSidebar"] {background:#17181A !important;}
.block-container {max-width:960px !important;padding-top:3.25rem !important;padding-bottom:3rem !important;}
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
.pilot-mini-note {color:#C3C1BC;font-size:.83rem;line-height:1.45;margin:.25rem 0 .9rem;}
.pilot-section-head {color:#D9D5CF;font-size:.73rem;font-weight:750;letter-spacing:.11em;text-transform:uppercase;margin:.65rem 0;}
[data-testid="stSegmentedControl"] {margin-bottom: .65rem;}
/* Use native Streamlit focus/keyboard semantics; style the selected page clearly. */
[data-testid="stSegmentedControl"] button[aria-pressed="true"] {
  background:#484744 !important;color:#FFFDF7 !important;
}
.pilot-weather-card {
  background:#202124;border:1px solid #414348;border-left:4px solid #8d939c;
  color:#f1f0ed;border-radius:13px;padding:14px 15px;
  margin:12px 0 16px;
}
.pilot-weather-kicker {
  color:#aaa8a4;font-size:.69rem;font-weight:790;letter-spacing:.10em;
  margin-bottom:10px;
}
.pilot-weather-head {
  display:flex;justify-content:space-between;align-items:flex-start;gap:10px;
}
.pilot-weather-label {font-size:.96rem;font-weight:750;line-height:1.35;color:#f1f0ed;}
.pilot-weather-pill {
  display:inline-block;margin-top:8px;font-size:.65rem;font-weight:800;
  border-radius:12px;padding:4px 8px;letter-spacing:.04em;
}
.pilot-weather-temp {
  flex-shrink:0;font-size:1.08rem;line-height:1.2;font-weight:740;text-align:right;
  font-variant-numeric:tabular-nums;color:#f3f2ef;
}
.pilot-weather-temp small {
  display:block;color:#bebbb7;font-size:.69rem;font-weight:450;
  margin-top:5px;white-space:normal;
}
.pilot-weather-no-temp {font-size:.72rem;text-align:right;color:#aaa8a4;}
.pilot-weather-forecast {font-size:.85rem;color:#e4e1dc;margin-top:11px;line-height:1.5;}
.pilot-weather-note {
  border-top:1px solid #3a3b3e;margin-top:12px;padding-top:10px;
  font-size:.78rem;line-height:1.5;color:#d2d0cb;
}
.pilot-weather-source {font-size:.69rem;color:#aaa8a4;margin-top:9px;}
.pilot-weather-good {border-left-color:#6cae8a;}
.pilot-weather-good .pilot-weather-pill {color:#b9e7c9;background:#2b4234;}
.pilot-weather-caution {border-left-color:#e3b35c;}
.pilot-weather-caution .pilot-weather-pill {color:#ffe0a0;background:#4a3822;}
.pilot-weather-high {
  border-left-color:#f17078;background:linear-gradient(105deg,#2e2227,#202124 77%);
  border-top-color:#644047;border-bottom-color:#644047;
}
.pilot-weather-high .pilot-weather-pill {color:#ffc0c5;background:#542c34;}
.pilot-weather-unknown {border-left-color:#92979e;}
.pilot-weather-unknown .pilot-weather-pill {color:#d7d9dd;background:#383b40;}
@media(max-width:600px){
  .block-container {padding:3.45rem .85rem 2.5rem !important;}
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

WORKSPACES = ("Drive", "Events & Traffic", "Results & Learning")


def remember_driving_date():
    """Widget keys disappear when a workspace is hidden; keep a nonwidget value."""
    st.session_state["planned_date"] = st.session_state["selected_day"]


def remember_gas_price():
    """Preserve a manually checked price across workspace changes in this session."""
    st.session_state["checked_gas_price"] = st.session_state["checked_gas_price_input"]


def partition_events(events):
    """Separate large venues from school/neighborhood congestion cues."""
    major = [ev for ev in events if ev["tier"] != "NEIGHBORHOOD / SCHOOL"]
    local = [ev for ev in events if ev["tier"] == "NEIGHBORHOOD / SCHOOL"]
    return major, local


def render_calendar_export(drive_plan, day):
    """Explicitly selected real shift windows: Google links and portable ICS."""
    with st.expander("📅 Add planned shifts to calendar", expanded=False):
        st.caption("These are personal plans, not calendar sync or confirmed paid jobs. "
                   "Calendar entries are added only after you approve them.")
        rest = st.checkbox("Include rest / reset window", value=False,
                           key="calendar_include_rest")
        indices = [i for i, slot in enumerate(drive_plan)
                   if slot.get("start_minute") is not None
                   and (slot["app"] in pilot.DRIVING_CATEGORIES or
                        (rest and slot["app"] == "Break / Reset"))]
        if not indices:
            st.info("No dated driving windows to export. An unknown kickoff does not generate a calendar time.")
            return
        picked = st.multiselect("Choose the planned windows to export",
                                options=indices, default=[],
                                format_func=lambda i: f"{roadmap_title(drive_plan[i])} · {drive_plan[i]['time']}",
                                key=f"calendar_selected_{day.isoformat()}")
        if not picked:
            st.caption("Select the shifts you actually intend to drive. Rest breaks are optional.")
            return
        if any("onward" in drive_plan[i]["time"].lower() for i in picked):
            onward_hours = st.select_slider(
                "Calendar duration for 'onward' shifts",
                options=[1, 2, 3, 4, 5], value=2,
                help="This is your selected reservation length, NOT a known end time.")
        else:
            onward_hours = 2
        entries = pilot.selected_calendar_entries(drive_plan, day,
                                                   selected=picked,
                                                   onward_hours=onward_hours,
                                                   include_break=rest)
        if not entries:
            st.warning("No confirmed clock windows selected.")
            return
        st.download_button(
            "Download selected shifts (.ics) · Apple / Outlook",
            data=pilot.calendar_ics(entries),
            file_name=f"ogbuefi-driving-{day.isoformat()}.ics",
            mime="text/calendar", use_container_width=True,
        )
        st.caption("Google Calendar opens a prefilled event for each shift; save it there "
                   "to add it. Existing events will not update automatically.")
        for entry in entries:
            st.link_button(f"Google Calendar · {entry['title']} "
                           f"({entry['start'].strftime('%I:%M %p').lstrip('0')}) ↗",
                           pilot.calendar_google_link(entry), use_container_width=True)


def render_predeparture_check(selected_date, now, weather, events_data):
    """Assist a live go/no-go decision without predicting platform demand."""
    if st.toggle("Before you leave · decision check", value=False,
                 key="show_decision_check"):
        st.caption("Use this before driving to a busier area without an order. This is a gas-cost check, not a live demand forecast.")
        a, b = st.columns(2, gap="small")
        with a:
            miles = st.number_input("Miles to the area (one way)",
                                    min_value=0.0, max_value=150.0, step=1.0,
                                    value=0.0, key="decision_miles",
                                    help="How far would you drive just to check for orders? We'll estimate gas for going there and coming back.")
            gas = st.number_input("Gas price you checked ($/gal)", min_value=0.0,
                                  max_value=15.0, step=0.05,
                                  value=float(st.session_state.get("checked_gas_price", 0.0)),
                                  key="decision_checked_gas",
                                  help="Enter a price you actually checked. Zero means unknown.")
        with b:
            max_fuel = st.number_input("Max gas I'll waste chasing demand ($)", min_value=0.0,
                                       max_value=100.0, value=3.0, step=0.5,
                                       key="decision_budget",
                                       help="Your gas budget for going there AND returning if you get no work. Not your total shift gas budget.")
            hours = st.number_input("Hours you can drive", min_value=0.25,
                                    max_value=16.0, step=0.5, value=3.0,
                                    key="decision_hours")
        assessment = decision.drive_advice(
            selected_date, now.date(), weather, events_data.get("events", []),
            one_way_miles=miles, gas_price=gas if gas >= 0.5 else None,
            max_position_fuel=max_fuel, work_hours=hours)
        st.markdown("**" + assessment["status"] + "**")
        st.write(assessment["reason"])
        if assessment["fuel_cost"] is not None:
            st.caption(f"Gas to drive there and back with zero orders: "
                       f"${assessment['fuel_cost']:.2f} (estimated at {MPG:g} MPG). "
                       f"Your limit: ${max_fuel:.2f}. This is not your full-shift fuel cost.")
        else:
            st.caption("Add a gas price you've checked to estimate the round-trip cost.")
        if assessment["weather_unknown"]:
            st.caption("Some forecast coverage is unknown. Check current road conditions.")
        st.caption("Crowd events do not guarantee rides or bonuses. This isn't a live surge scanner.")
        with st.expander("Optional · sanity-check an actual app offer"):
            st.caption("Use details visible in your driving app. These inputs are not "
                       "saved as an earnings ledger and won't sync to Gridwise.")
            payout = st.number_input("Actual offer payout ($)", min_value=0.0,
                                      value=0.0, step=1.0, key="offer_pay")
            total_miles = st.number_input("All expected miles, including unpaid miles",
                                           min_value=0.1, max_value=500.0,
                                           value=10.0, step=0.5, key="offer_miles")
            minutes = st.number_input("Total expected driving and waiting minutes",
                                       min_value=1.0, max_value=1440.0,
                                       value=45.0, step=5.0, key="offer_minutes")
            target = st.number_input("Your minimum after-fuel rate ($/hour)",
                                     min_value=0.0, max_value=200.0,
                                     value=20.0, step=1.0, key="offer_min_rate")
            if payout > 0:
                result = decision.offer_check(payout, total_miles, minutes,
                                              gas if gas >= 0.5 else None, target)
                st.markdown("**" + result["status"] + "**")
                if "after_fuel_hour" in result:
                    st.metric("Estimated after-fuel hourly",
                              f"${result['after_fuel_hour']:.2f}/hr")
                st.caption(result["message"])
            else:
                st.caption("Enter a real offer to compare against your own hourly target.")


def load_private_decisions():
    """Return private plan snapshots only to authenticated Streamlit sessions."""
    if not private_session_active():
        return []
    if "journal_sheet_records" not in st.session_state:
        sheet_id, credentials = private_sheet_config()
        if sheet_id and credentials:
            try:
                st.session_state["journal_sheet_records"] = decision.decision_sheet_load(
                    credentials, sheet_id)
                st.session_state.pop("journal_sheet_error", None)
            except Exception:
                st.session_state["journal_sheet_records"] = []
                st.session_state["journal_sheet_error"] = (
                    "Decision journal could not be read from Google Sheets. "
                    "Back up any temporary entries with CSV.")
        else:
            st.session_state["journal_sheet_records"] = []
    combined = {}
    for row in (st.session_state["journal_sheet_records"] +
                st.session_state.get("journal_pending_records", [])):
        if row.get("id"):
            combined[str(row["id"])] = row
    return sorted(combined.values(), key=lambda r: str(r.get("created_at", "")),
                  reverse=True)


def render_decision_journal(selected_date, drive_plan, daily_brief):
    """Plan / outcome loop; never duplicate Gridwise earnings or mileage logs."""
    with st.expander("Private decision journal · learn from your plans", expanded=False):
        st.caption("Record what you chose and later whether it was worth it. "
                   "Gridwise still owns earnings, mileage and taxes.")
        if not private_history_unlocked(show_form=False):
            return
        sheet_id, credentials = private_sheet_config()
        connected = bool(sheet_id and credentials)
        rows = load_private_decisions()
        if st.session_state.get("journal_sheet_error"):
            st.warning(st.session_state["journal_sheet_error"])
        valid = [i for i, slot in enumerate(drive_plan)
                 if slot["app"] in decisionable_apps() and
                 slot.get("start_minute") is not None]
        if valid:
            with st.form("plan_decision_form"):
                chosen = st.selectbox("Which planned driving window?", valid,
                                      format_func=lambda i: (
                                          f"{roadmap_title(drive_plan[i])} · "
                                          f"{drive_plan[i]['time']}"))
                choice = st.radio("What did you decide?", decision.CHOICES,
                                  horizontal=True, index=1)
                reason = st.text_input("Reason / observation (optional)",
                                        max_chars=240)
                submitted = st.form_submit_button("Record my decision",
                                                   use_container_width=True)
            if submitted:
                record = decision.new_decision(selected_date, drive_plan[chosen],
                                               choice, reason, daily_brief,
                                               one_way_miles=st.session_state.get(
                                                   "decision_miles", 0.0),
                                               gas_price=st.session_state.get(
                                                   "decision_checked_gas", 0.0))
                if connected:
                    try:
                        decision.decision_sheet_append(credentials, sheet_id, record)
                    except Exception:
                        st.session_state.setdefault("journal_pending_records", []).append(record)
                        st.warning("Sheet write failed. This plan is only in your session; "
                                   "download the CSV backup below.")
                    else:
                        st.session_state.setdefault("journal_sheet_records", []).insert(0, record)
                        st.success("Decision saved to the private Decisions worksheet.")
                else:
                    st.session_state.setdefault("journal_pending_records", []).append(record)
                    st.warning("Sheet not connected. This plan is temporary; "
                               "download a CSV backup.")
                rows = load_private_decisions()
        else:
            st.caption("No timed driving windows to record for this date.")
        if rows:
            summary = decision.decision_summary(rows)
            st.caption(summary["message"])
            pending = [r for r in rows if r.get("result") == "Unreviewed"]
            if pending:
                with st.form("review_plan_outcome"):
                    ids = [r["id"] for r in pending]
                    selected_id = st.selectbox(
                        "Review an earlier decision", options=ids,
                        format_func=lambda id_: next(
                            f"{r['planned_date']} · {r['window_time']} · {r['choice']}"
                            for r in pending if r["id"] == id_))
                    result = st.selectbox("How did that decision work out?",
                                           decision.OUTCOMES[1:])
                    takeaway = st.text_input("What did you learn? (optional)",
                                              max_chars=300)
                    review_submitted = st.form_submit_button("Save outcome")
                if review_submitted:
                    record = next(r for r in rows if r["id"] == selected_id)
                    in_sheet = any(r["id"] == selected_id for r in
                                   st.session_state.get("journal_sheet_records", []))
                    if connected and in_sheet:
                        try:
                            decision.decision_sheet_review(
                                credentials, sheet_id, selected_id, result, takeaway)
                        except Exception:
                            st.warning("Outcome was not saved to the Sheet. Try again.")
                        else:
                            record["result"], record["takeaway"] = result, takeaway
                            st.success("Outcome saved.")
                    elif not in_sheet:
                        record["result"], record["takeaway"] = result, takeaway
                        st.warning("Outcome updated in this session. Download a CSV backup.")
                    else:
                        st.warning("Sheet disconnected; could not persist this outcome.")
            with st.expander("View past decisions", expanded=False):
                st.dataframe(rows[:100], hide_index=True,
                             use_container_width=True)
        else:
            st.caption("No decisions recorded. Start by saving what you actually chose.")
        st.download_button("Back up private decision journal (CSV)",
                           data=decision.decision_csv_backup(rows),
                           file_name="ogbuefi-decisions.csv",
                           mime="text/csv", use_container_width=True)


def decisionable_apps():
    return ("Delivery", "XL / Comfort")


def render_driving_card(slot, step):
    """Compact timeline row; all routing instructions remain available on tap."""
    with st.container(border=True):
        left, right = st.columns([1.1, 2.65], gap="small", vertical_alignment="top")
        with left:
            st.markdown(f'<span class="pilot-timeline-time">{escape(slot["time"])}</span>',
                        unsafe_allow_html=True)
        with right:
            st.markdown(f"**{roadmap_title(slot)}**")
            st.caption(slot["app"])
        with st.expander(f"Step {step} · Routing details", expanded=False):
            st.write(slot["action"])


def render_drive_workspace(selected_date, now, events_data, weather, base):
    """One-glance driving decision; no offer-entry tools or earnings forms."""
    events = events_data["events"]
    major, local = partition_events(events)
    st.subheader("Your driving plan")
    st.caption(selected_date.strftime("%A, %B %d, %Y") +
               " · Optional opportunities, not guaranteed demand")
    st.caption("Delivery-first · Uber Eats / DoorDash. Comfort / XL rides are "
               "opportunistic. Your offer goal: $" +
               f"{OFFER_TARGET_DOLLARS_PER_MILE:g}/mile using estimated total "
               "miles, including pickup/unpaid miles.")
    render_weather(weather, details=False)

    home_game = next((ev for ev in events if selected_date.weekday() in (5, 6)
                      and is_confirmed_michigan_home_game(ev)), None)
    with st.expander("Adjust driving plan", expanded=False):
        include_events = st.checkbox("Include major-event demand + traffic cues", value=True,
                                     help="Published events affect the brief; not guaranteed earnings.")
        include_local = st.checkbox("Include school-area traffic cues", value=True)
        smart_game_day = home_game is not None
        stop_buffer = 45
        return_delay = 2.0
        if home_game is not None:
            smart_game_day = st.checkbox("Use Michigan game-day split shift", value=True)
            if smart_game_day:
                stop_buffer = st.slider("Stop before kickoff (minutes)", 30, 60, 45, 15)
                return_delay = st.slider("Return after kickoff (hours)", 1.0, 4.0, 2.0, 0.5)

    schedule = generate_schedule(
        events, base, selected_date.weekday(),
        include_event_cues=include_events, include_local=include_local,
        smart_game_day=smart_game_day, stop_buffer_minutes=stop_buffer,
        return_after_hours=return_delay)
    drive_plan = filter_roadmap(schedule, "Driving")
    headline = planning_headline(drive_plan, selected_date, now)
    next_step = headline["slot"]
    context_events = [event for event in events if
                      (event["tier"] != "NEIGHBORHOOD / SCHOOL" and include_events) or
                      (event["tier"] == "NEIGHBORHOOD / SCHOOL" and include_local)]
    decision_context = pilot.daily_decision_brief(
        selected_date, {"events": context_events, "coverage": events_data.get("coverage", [])},
        weather)
    # A private plan snapshot lets Results & Learning record outcomes without
    # fetching event/weather feeds on that workspace.
    st.session_state["review_plan"] = drive_plan
    st.session_state["review_date"] = selected_date
    st.session_state["review_context"] = decision_context

    with st.container(border=True):
        st.caption("YOUR PLAN")
        st.markdown(f"**{headline['title']}**")
        if next_step:
            st.markdown(
                f'<span class="pilot-timeline-time">{escape(next_step["time"])}</span>',
                unsafe_allow_html=True)
            st.caption(headline["basis"])
        st.caption(headline["note"])

    if home_game and smart_game_day:
        st.caption(f"Michigan kickoff: {clock(home_game['start'])}. "
                   "Drive, rest and return windows follow the official time.")

    if major or local:
        st.caption(f"Published local context: {len(major)} major event(s), "
                   f"{len(local)} school/neighborhood event(s). "
                   "Potential congestion, not verified surge.")

    st.markdown('<div class="pilot-section-head">Planned windows</div>',
                unsafe_allow_html=True)
    remaining = st.toggle("Hide finished windows", value=False, key="drive_remaining",
                          help="Hide completed windows for today.")
    shown = filter_roadmap(drive_plan, selected_date=selected_date, now=now,
                           remaining=remaining)
    for i, slot in enumerate(shown, 1):
        render_driving_card(slot, i)
    if not shown:
        st.info("No windows match. Turn off Hide finished windows to see the full plan.")

    render_calendar_export(drive_plan, selected_date)
    st.button("Explore events & traffic →", use_container_width=True,
              on_click=lambda: st.session_state.update(pilot_workspace="Events & Traffic"))


def render_events_workspace(selected_date, events_data, weather):
    """Crowds, school traffic and optional advanced troubleshooting."""
    major, local = partition_events(events_data["events"])
    st.subheader("Events & Traffic")
    st.caption(selected_date.strftime("%A, %B %d, %Y") +
               " · Published congestion clues, not guaranteed surge")
    a, b = st.columns(2, gap="small")
    with a:
        with st.container(border=True):
            st.metric("Major venues", len(major))
    with b:
        with st.container(border=True):
            st.metric("Local / schools", len(local))
    for note in events_data["notes"]:
        st.warning(note)
    if not major and not local:
        st.info("No matching events returned. This does not confirm normal traffic.")

    with st.expander(f"🎟️ Major events ({len(major)})", expanded=True):
        for ev in major:
            with st.container(border=True):
                st.markdown(f"**{ev['name']}**")
                st.caption(f"{event_time_label(ev)} · {ev['venue']}")
                impact = event_impact(ev)
                st.write("**Traffic:** " + impact["traffic"])
                st.caption("Demand: " + impact["demand"])
                st.caption("Route: " + impact["action"])
                if ev["url"]:
                    st.link_button("Official listing ↗", ev["url"])
        if not major:
            st.caption("No major events returned for this date.")

    with st.expander(f"🚦 Schools & neighborhoods ({len(local)})", expanded=False):
        st.caption("Traffic avoidance near performances and school venues.")
        for ev in local:
            with st.container(border=True):
                st.markdown(f"**{ev['name']}**")
                st.caption(f"{event_time_label(ev)} · {ev['venue']}")
                st.write("**Traffic:** " + event_impact(ev)["traffic"])
                if ev["url"]:
                    st.link_button("Calendar source ↗", ev["url"])
        if not local:
            st.caption("No matching local events; school calendar coverage may be incomplete.")

    render_weather(weather, details=True)
    with st.expander("Advanced · Feed diagnostics", expanded=False):
        st.caption("Troubleshooting only. Not needed for normal driving decisions.")
        st.dataframe(events_data["coverage"], hide_index=True, use_container_width=True)
        st.caption(f"Checked {events_data['checked'].strftime('%b %d %I:%M %p %Z')}. "
                   "TBD kickoff times update on a successful feed refresh.")


def private_sheet_config():
    """No credentials are shipped in GitHub or shown in the dashboard."""
    sheet_id = setting("SHIFT_SHEET_ID", "").strip()
    credentials = None
    try:
        credentials = st.secrets.get("gcp_service_account")
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        pass
    if credentials is None:
        encoded = setting("GCP_SERVICE_ACCOUNT_JSON", "")
        if encoded:
            try:
                credentials = json.loads(encoded)
            except (ValueError, TypeError):
                credentials = None
    return sheet_id, dict(credentials) if credentials is not None else None


def load_private_history():
    """Only an authenticated session may read the optional private Google Sheet."""
    if not private_session_active():
        return []
    if "sheet_shift_rows" not in st.session_state:
        sheet_id, credentials = private_sheet_config()
        if sheet_id and credentials:
            try:
                st.session_state["sheet_shift_rows"] = pilot.sheet_load(credentials, sheet_id)
                st.session_state.pop("sheet_history_error", None)
            except Exception:
                st.session_state["sheet_shift_rows"] = []
                st.session_state["sheet_history_error"] = (
                    "Google Sheets could not be read. Shift entries can be backed up "
                    "as CSV until the configuration is repaired.")
        else:
            st.session_state["sheet_shift_rows"] = []
    return pilot.merge_shift_rows(st.session_state["sheet_shift_rows"],
                                   st.session_state.get("unsynced_shift_rows", []))


def private_session_active():
    """Only a valid, unexpired session can read any private Google Sheet data."""
    password = setting("DRIVER_ACCESS_PASSWORD", "")
    if security.valid(st.session_state, password):
        return True
    if (security.PRIVATE_SESSION_KEY in st.session_state or
            st.session_state.get("driver_authenticated")):
        security.lock(st.session_state)
    return False


def lock_private_session():
    """Logout action: revoke access AND clear cached private records/uploads."""
    security.lock(st.session_state)
    st.session_state.pop("driver_login_failures", None)


def private_history_unlocked(*, show_form=True):
    """One login form on Results; never duplicate password widgets in expanders."""
    configured = setting("DRIVER_ACCESS_PASSWORD", "")
    if not security.password_is_configured(configured):
        if show_form:
            st.info("Private records are disabled until DRIVER_ACCESS_PASSWORD "
                    "(12+ characters) is configured in Streamlit Secrets.")
        return False
    if private_session_active():
        return True
    if not show_form:
        return False
    attempts = st.session_state.get("driver_login_failures", 0)
    if attempts >= 5:
        st.error("This session reached the maximum login attempts. "
                 "Private Community Cloud access is the primary protection "
                 "against unauthorized visitors.")
        return False
    with st.form("driver_unlock", clear_on_submit=True):
        password = st.text_input("Private data passphrase", type="password",
                                 help="Only the dashboard owner should unlock private records.")
        submitted = st.form_submit_button("Unlock private results")
    if submitted:
        if hmac.compare_digest(password.encode("utf-8"), configured.encode("utf-8")):
            security.authorize(st.session_state, configured)
            st.session_state.pop("driver_login_failures", None)
            st.rerun()
        else:
            st.session_state["driver_login_failures"] = attempts + 1
            st.error("Passphrase not recognized.")
    return False


def render_shift_history():
    """Private history stays inside Earnings & Fuel and requires configured access."""
    with st.expander("🔒 Shift history & insights", expanded=False):
        if not private_history_unlocked(show_form=False):
            return
        sheet_id, credentials = private_sheet_config()
        connected = bool(sheet_id and credentials)
        rows = load_private_history()
        if st.session_state.get("sheet_history_error"):
            st.warning(st.session_state["sheet_history_error"])
        if connected:
            st.caption("Private Google Sheets storage configured. "
                       "Refresh the page to reload changes made outside this app.")
        else:
            st.warning("Google Sheets is not connected yet. Any shifts logged here "
                       "are TEMPORARY until you download the CSV backup. "
                       "See README for the one-time service-account setup.")

        st.markdown("**Log a completed shift**")
        with st.form("log_completed_shift"):
            logged_date = st.date_input("Date worked", datetime.now(TZ).date(), key="log_date")
            platform = st.selectbox("Driving platform", ["Multi-app", "Uber", "Lyft",
                                                         "DoorDash", "Grubhub", "Other"])
            area = st.selectbox("Main driving area", ["Ann Arbor", "Ypsilanti",
                                                      "Washtenaw corridor", "Other"])
            window = st.selectbox("Time window", ["Dinner", "Game pregame", "Game evening",
                                                   "Afternoon", "Late night", "Lunch",
                                                   "Morning", "Mixed"])
            gross = st.number_input("Shift gross earnings ($)", min_value=0.0,
                                    value=0.0, step=5.0)
            hours = st.number_input("Online hours, including idle time", min_value=0.25,
                                    value=2.0, step=0.25)
            miles = st.number_input("Total driven miles, including unpaid miles",
                                    min_value=0.1, value=25.0, step=1.0)
            gas_rate = st.number_input("Regular gas price for this shift ($/gal)",
                                       min_value=0.0, max_value=15.0,
                                       value=float(st.session_state.get("checked_gas_price", 0.0)),
                                       step=0.05)
            optional_cost = st.number_input(
                "Optional other variable vehicle cost ($/mile)", min_value=0.0,
                max_value=10.0, value=0.0, step=0.01,
                help="Leave zero if unknown. This is not a full tax or depreciation calculation.")
            note = st.text_input("Notes (optional)", max_chars=300)
            submitted = st.form_submit_button("Save completed shift", use_container_width=True)
        if submitted:
            try:
                shift = pilot.new_shift(logged_date, platform, area, window, gross,
                                        hours, miles, gas_rate, optional_cost, note)
                if connected:
                    try:
                        pilot.sheet_append(credentials, sheet_id, shift)
                    except Exception:
                        st.session_state.setdefault("unsynced_shift_rows", []).append(shift)
                        st.warning("Could not save to Google Sheets. The shift is held in this "
                                   "session only. Download your CSV backup below.")
                    else:
                        st.session_state.setdefault("sheet_shift_rows", []).append(shift)
                        st.success("Shift saved to your private Google Sheet.")
                else:
                    st.session_state.setdefault("unsynced_shift_rows", []).append(shift)
                    st.warning("Shift recorded temporarily. Download your CSV backup "
                               "before closing the session.")
                rows = load_private_history()
            except (ValueError, TypeError):
                st.error("Check your gas price and shift values; valid earnings, hours "
                         "and all driven miles are required.")

        if rows:
            st.markdown("**Historical performance**")
            valid = []
            for row in rows:
                try:
                    valid.append(pilot.shift_metrics(row))
                except (ValueError, KeyError, TypeError, ZeroDivisionError):
                    pass
            st.caption(f"{len(rows)} saved / session-only records · "
                       "Benchmarks compare actual recorded shifts, not surge predictions.")
            if valid:
                left, right = st.columns(2, gap="small")
                with left:
                    st.metric("Recorded after-fuel earnings",
                              "${:,.2f}".format(sum(m["after_fuel"] for m in valid)))
                with right:
                    st.metric("Median after fuel / online hour",
                              "${:,.2f}".format(
                                  statistics.median(m["after_fuel_hour"] for m in valid)))
            with st.expander("Review shift rows"):
                st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.caption("No shifts logged yet. Three or more matching weekdays "
                       "within 90 days unlock a descriptive historical benchmark.")

        st.download_button("Download private shift-history backup (CSV)",
                           data=pilot.csv_backup(rows), file_name="ogbuefi-shifts.csv",
                           mime="text/csv", use_container_width=True)
        file = st.file_uploader("Restore shift-history CSV (session preview)",
                                type=["csv"], key="restore_csv",
                                help="Restoring does not silently upload rows to Google Sheets.")
        if file is not None and st.button("Restore CSV to this session"):
            try:
                recovered = pilot.csv_restore(file.getvalue())
                existing_ids = {str(x.get("id")) for x in rows}
                missing = [x for x in recovered if str(x.get("id")) not in existing_ids]
                st.session_state.setdefault("unsynced_shift_rows", []).extend(missing)
                st.success(f"Restored {len(missing)} new rows to temporary session history. "
                           "Download an updated backup or sync explicitly.")
            except (UnicodeError, ValueError, KeyError, TypeError):
                st.error("Not a valid Gig Pilot history CSV.")
        unsynced = st.session_state.get("unsynced_shift_rows", [])
        if connected and unsynced and st.button("Sync unsaved CSV/session shifts to Google Sheets"):
            synced = []
            already_saved = {str(x["id"]) for x in
                             st.session_state.get("sheet_shift_rows", [])}
            for row in unsynced:
                if str(row.get("id")) in already_saved:
                    synced.append(row["id"])
                    continue
                try:
                    pilot.sheet_append(credentials, sheet_id, row)
                except Exception:
                    break
                else:
                    synced.append(row["id"])
                    st.session_state.setdefault("sheet_shift_rows", []).append(row)
            st.session_state["unsynced_shift_rows"] = [
                row for row in unsynced if row["id"] not in synced]
            if st.session_state["unsynced_shift_rows"]:
                st.warning("Some entries could not sync. Keep a CSV backup.")
            else:
                st.success("All unsaved entries synced to the private Sheet.")


def render_earnings_workspace():
    """Fuel entry and weekly grading, without loading event/weather feeds."""
    st.subheader("Earnings & Fuel")
    st.caption("Your fuel cost and weekly earnings at 16 MPG.")
    with st.container(border=True):
        st.markdown("**⛽ Regular gas quick check**")
        a, b = st.columns(2, gap="small")
        with a:
            st.link_button("GasBuddy · Ann Arbor ↗", GASBUDDY_ANN_ARBOR_URL, use_container_width=True)
        with b:
            st.link_button("GasBuddy · Ypsilanti ↗", GASBUDDY_YPSILANTI_URL, use_container_width=True)
        st.number_input("Gas price you checked ($/gal)", min_value=0.0, max_value=15.0,
                        value=st.session_state.get("checked_gas_price", 0.0),
                        step=0.01, key="checked_gas_price_input",
                        on_change=remember_gas_price,
                        help="Enter a checked regular-gas price. 0 means unavailable; stored in this session.")
        st.caption("Community-reported prices may be wrong. Check the time, grade and membership conditions.")

    live_gas = st.session_state.get("checked_gas_price", 0.0)
    live_gas = live_gas if isinstance(live_gas, (int, float)) and live_gas >= 0.5 else None
    st.subheader("Optional weekly fuel-only grader")
    if live_gas is None:
        st.info("Enter a checked regular-gas price to enable grading.")
    else:
        st.caption(f"Using ${live_gas:.2f}/gal · {MPG:g} MPG")
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
            a, b = st.columns(2, gap="small")
            with a:
                with st.container(border=True):
                    st.metric("Gross hourly", f"${metrics['hourly_rate']:,.2f}/hr")
            with b:
                with st.container(border=True):
                    st.metric("Gross per mile", f"${metrics['mile_rate']:,.2f}/mi")
            st.metric("Fuel expense", f"${metrics['fuel_cost']:,.2f}")
            st.success(f"After-fuel earnings: ${metrics['net_profit']:,.2f}")
            st.caption("Fuel-only contribution; taxes, maintenance, depreciation and insurance excluded.")
        else:
            st.warning("Enter positive hours and miles and a checked fuel price.")

    render_shift_history()


def render_gridwise_import():
    """Owner-only archive; parse in memory, write ONLY on explicit confirmation."""
    with st.expander("Import Gridwise reports · PDF or CSV", expanded=False):
        st.caption("Use Gridwise as the source of truth. This importer accepts "
                   "Gridwise earnings and mileage reports; it never logs into Gridwise.")
        if not private_history_unlocked(show_form=False):
            return
        uploaded = st.file_uploader(
            "Choose Gridwise reports (PDF or CSV)", type=["pdf", "csv"],
            accept_multiple_files=True, key="gridwise_archive_files")
        if not uploaded:
            st.caption("Upload a report when you have one. Older-state or "
                       "older-vehicle records stay ARCHIVED and do not influence 2026 advice.")
            return
        parsed = []
        failures = []
        for file in uploaded:
            try:
                parsed.append(gridwise.parse_gridwise_upload(file.name, file.getvalue()))
            except ValueError as exc:
                failures.append(f"{file.name}: {str(exc)}")
        for message in failures:
            st.warning(message)
        if not parsed:
            return
        all_rows = []
        for report in parsed:
            info = gridwise.report_summary(report)
            all_rows.extend(report["rows"])
            total_label = ("gross earnings" if report["type"] == "earnings"
                           else "tracked miles")
            st.caption(
                f"{report['type'].title()} · {report['format'].upper()} · "
                f"{info['rows']} entries / {info['dates']} dates / "
                f"{', '.join(map(str, info['years']))} · "
                f"{info['total']:,.2f} {total_label}")
        # Dedupe repeated rows across uploaded files before optional Sheet write.
        unique = {row["row_key"]: row for row in all_rows}
        archive_rows = list(unique.values())
        comparison = gridwise.join_by_date(archive_rows)
        matched = sum(row["earnings"] is not None and row["miles"] is not None
                      for row in comparison)
        st.caption(f"{matched} date(s) with both earnings and mileage information. "
                   "Unmatched dates remain incomplete; no hour/mile values are invented.")
        st.dataframe(
            [{k: row[k] for k in ("date", "report_type", "platform", "trips",
                                 "earnings", "hours", "miles")}
             for row in archive_rows[:25]],
            hide_index=True, use_container_width=True)
        st.caption("Preview first 25 rows. Historical reports may reflect another "
                   "state, vehicle and gas prices. No tax-deduction or historic fuel "
                   "estimate is used for current decisions.")
        sheet_id, credentials = private_sheet_config()
        if not sheet_id or not credentials:
            st.warning("Private Google Sheet is not configured; nothing is saved.")
            return
        if failures:
            st.warning("Some uploads failed validation. Review them before saving.")
        confirm = st.checkbox(
            "I reviewed the preview; archive these historical entries in my private Sheet",
            value=False, key="confirm_gridwise_archive")
        if st.button("Save reviewed imports", disabled=not confirm,
                     use_container_width=True):
            try:
                saved, duplicates = gridwise.archive_append_new(
                    credentials, sheet_id, archive_rows)
            except Exception:
                st.error("Could not save to the private archive. No changes to Shifts "
                         "or Decisions were requested. Check Sheet permissions.")
            else:
                st.success(f"{saved} row(s) saved to Gridwise_Archive; "
                           f"{duplicates} duplicate row(s) skipped.")
                st.session_state.pop("cached_gridwise_archive", None)


def render_results_workspace():
    """Gridwise complements this planning tool. Advanced forms remain accessible."""
    st.subheader("Results & Learning")
    st.caption("Gridwise Plus owns earnings, mileage and tax tracking. "
               "This page is for reviewing Gig Pilot decisions, not duplicating trips.")
    with st.expander("🔒 Private access", expanded=False):
        if private_session_active():
            st.caption("Private records unlocked · automatically locks after "
                       "30 minutes without interaction or 8 hours total.")
            st.button("Lock private data now", key="driver_explicit_logout",
                      on_click=lock_private_session, use_container_width=True)
        else:
            private_history_unlocked()

    st.markdown("**Keep driving-app decisions fast.** Use Gig Pilot before a shift "
                "and Gridwise for the actual results. There's no need to enter "
                "every offer or every mile here.")
    render_gridwise_import()

    if st.toggle("Advanced tools · optional", value=False,
                 key="results_advanced_tools"):
        st.caption("Optional manual tools. None are needed while reviewing "
                   "30–60-second live offers.")
        if st.session_state.get("review_plan") is not None:
            st.markdown("**Departure planning calculator**")
            # Uses only already-loaded Drive context; no external feeds here.
            snapshot = st.session_state.get("review_context", {})
            history_weather = {"places": [{"status": "Unknown"}],
                               "hazards": []}
            history_events = {"events": []}
            render_predeparture_check(
                st.session_state["review_date"], datetime.now(TZ),
                history_weather, history_events)
            render_decision_journal(
                st.session_state["review_date"], st.session_state["review_plan"],
                {"events": snapshot.get("events", ""),
                 "weather": snapshot.get("weather", "")})
        else:
            st.caption("Visit Drive first to load a plan before reviewing "
                       "or journaling a specific recommendation.")
        if st.toggle("Legacy fuel and earnings tools", value=False,
                     key="legacy_fuel_tools"):
            render_earnings_workspace()


def main():
    st.set_page_config(page_title="Graphite Pilot • Washtenaw Gig Pro",
                       page_icon="🛞", layout="centered", initial_sidebar_state="collapsed")
    st.markdown(MIDNIGHT_CSS, unsafe_allow_html=True)
    now = datetime.now(TZ)
    st.markdown(
        '<section class="pilot-hero" aria-label="Driver dashboard">'
        f'<div class="pilot-eyebrow">{escape(now.strftime("%A · %B %d, %Y"))} · PILOT CONTROL</div>'
        f'<div class="pilot-greeting">{escape(greeting_for_hour(now.hour))}</div>'
        '<div class="pilot-subtitle">WASHTENAW GIG PILOT PRO · '
        'Ann Arbor ↔ Ypsilanti · Kia Telluride · 16 MPG</div>'
        '<div class="pilot-line" aria-hidden="true"></div></section>',
        unsafe_allow_html=True,
    )
    with st.sidebar:
        st.header("⚙️ Pilot settings")
        base = st.text_input("Base camp", BASE)
        st.caption("Base camp affects routing guidance, not which calendars are scanned.")
        st.caption("Events: 30-minute cache · Weather: 15-minute cache.")

    view = st.segmented_control("Workspace", WORKSPACES, default="Drive",
                                key="pilot_workspace",
                                help="Choose one workspace. Your date and fuel entry are retained this session.")
    if view == "Results & Learning":
        render_results_workspace()
        return

    date_col, refresh_col = st.columns([3, 1], gap="small", vertical_alignment="bottom")
    with date_col:
        selected_date = st.date_input(
            "Driving date", st.session_state.get("planned_date", now.date()),
            key="selected_day", on_change=remember_driving_date)
    with refresh_col:
        if st.button("↻ Refresh", use_container_width=True, help="Recheck event and weather feeds"):
            fetch_live_events.clear()
            fetch_weather.clear()
    with st.spinner("Checking local events and forecast…"):
        events_data = fetch_live_events(selected_date, setting("EVENT_FEEDS_JSON", "[]"))
        weather = fetch_weather(selected_date)
    if view == "Drive":
        render_drive_workspace(selected_date, now, events_data, weather, base)
    elif view == "Events & Traffic":
        render_events_workspace(selected_date, events_data, weather)


if __name__ == "__main__":
    main()

