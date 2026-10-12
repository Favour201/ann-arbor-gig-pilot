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
        return "CRITICAL IMPACT" if "football" in name.lower() else "INDOOR ARENA" if "arena" in venue else "NEIGHBORHOOD / SCHOOL"
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
            start = local_dt(row["date"])
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
            tbd = bool(row.get("tbd")) or not row.get("time") or bool(re.search(r"TBA|TBD", row["time"], re.I))
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


@st.cache_data(ttl=1800, max_entries=32, show_spinner=False)
def fetch_live_events(day, extra_json="[]"):
    jobs = [("U-M campus / Hill Auditorium", "umich", "https://events.umich.edu/", None)]
    for school, host, sports in [
        ("Michigan", "mgoblue.com", ("football", "mens-basketball", "womens-basketball", "ice-hockey", "womens-gymnastics")),
        ("EMU", "emueagles.com", ("football", "mens-basketball", "womens-basketball", "volleyball", "womens-soccer", "gymnastics", "baseball")),
    ]:
        for sport in sports:
            jobs.append((school + " " + sport, "sidearm", f"https://{host}/sports/{sport}/schedule", (school, sport)))
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
    if len(jobs) == 13:
        notes.append("Michigan Theater and neighborhood/high-school feeds are not connected. Add their JSON or iCalendar feeds in EVENT_FEEDS_JSON.")

    def run(job):
        name, fmt, url, detail = job
        try:
            if fmt == "umich":
                events = fetch_umich(day)
            elif fmt == "sidearm":
                events = parse_sidearm(get(url).text, day, *detail, url)
            else:
                events = parse_extra(get(url).text, fmt, day, name, url)
            return events, {"Source": name, "Status": f"Checked • {len(events)} matches", "URL": url}
        except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError, IndexError):
            return [], {"Source": name, "Status": "Unavailable / format changed", "URL": url}
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


def generate_schedule(events, base_camp, day_of_week):
    timeline = []
    if day_of_week == 3:
        timeline.extend([{"time": "6:00 PM – 8:30 PM", "app": "Delivery", "action": "Thursday dinner: consider compact Carpenter / Washtenaw restaurant loops; target $2.50 per mile."},
                         {"time": "8:30 PM onward", "app": "XL / Comfort", "action": "Check actual app demand around South U / Packard before repositioning."}])
    else:
        timeline.append({"time": "4:30 PM – 9:30 PM" if day_of_week == 6 else "5:00 PM – 8:00 PM", "app": "Delivery",
                         "action": f"Start from {base_camp}. Favor short Carpenter / Washtenaw loops and account for unpaid return miles."})
        if day_of_week in (4, 5):
            timeline.append({"time": "8:00 PM onward", "app": "XL / Comfort", "action": "Check downtown demand and legal pickup locations before moving; premiums are not guaranteed."})
    # All event footprints survive Thursday/weekend rules, not just the first football game.
    for event in events:
        start, end = event["start"], event["end"]
        if event["all_day"]:
            label = "Time TBD / all day"
        else:
            lead = timedelta(hours=3 if event["tier"] == "CRITICAL IMPACT" else 1)
            label = window(start - lead, start)
        actions = {
            "CRITICAL IMPACT": "Expect Michigan Stadium approach congestion. Consider the east-side hotel corridor; confirm traffic before using Stadium / Main. Do not queue at closed roads.",
            "YPSI GAME DAY": "Expect venue arrivals around EMU. Check Hewitt / Huron traffic and use signed pickup zones.",
            "INDOOR ARENA": "Expect concentrated arena arrivals. Check State / Stadium / Packard and avoid blocking venue entrances.",
            "PERFORMING ARTS": "Expect downtown curb demand near the performance. Use legal pickup zones and avoid idling on State / Liberty / North University.",
            "NEIGHBORHOOD / SCHOOL": "Allow for neighborhood and school traffic. Keep school entrances, bus lanes and pedestrian crossings clear.",
        }
        action = f"{event['name']} • {event['venue']}. {actions[event['tier']]}"
        if end and not event["all_day"]:
            action += f" Published end: {clock(end)}; recheck departure demand then."
        else:
            action += " End time unconfirmed; check the event before chasing departure demand."
        timeline.append({"time": label, "app": event["tier"], "action": action})
    return timeline


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


def main():
    st.set_page_config(page_title="Washtenaw Gig Pilot Pro", page_icon="🛞", layout="centered")
    st.markdown('''<style>
    .block-container{max-width:850px;padding-top:1.6rem;padding-bottom:3rem}
    .weather-premium{background:#fff1c2;color:#593600;border:2px solid #d58a00;border-radius:12px;padding:16px;margin:12px 0;line-height:1.6}
    button{min-height:44px} [data-testid="stMetricValue"]{font-size:1.7rem}
    @media(max-width:600px){.block-container{padding-left:1rem;padding-right:1rem}
    [data-testid="stHorizontalBlock"]{flex-wrap:wrap}h1{font-size:1.85rem!important}}
    </style>''', unsafe_allow_html=True)
    st.title("🛞 Washtenaw Gig Pilot Pro")
    st.caption("Ann Arbor ↔ Ypsilanti • Kia Telluride • 16 MPG")
    with st.sidebar:
        st.header("Pilot settings")
        base = st.text_input("Base camp", BASE)
        st.caption("Changing base camp updates the roadmap; the monitored corridor remains Ann Arbor / Ypsilanti.")
        if st.button("Refresh live sources", use_container_width=True):
            fetch_live_events.clear()
            fetch_weather.clear()
        st.caption("Weather: 15-minute cache. Events: 30-minute cache. Refresh applies to events and forecasts.")
    st.subheader("⛽ Find nearby regular gas")
    st.caption("Open GasBuddy to inspect reported prices. These links search Ann Arbor and Ypsilanti; GasBuddy's Find Gas feature can use your device location when enabled.")
    first, second = st.columns(2)
    with first:
        st.link_button("GasBuddy • Ann Arbor", GASBUDDY_ANN_ARBOR_URL, use_container_width=True)
    with second:
        st.link_button("GasBuddy • Ypsilanti", GASBUDDY_YPSILANTI_URL, use_container_width=True)
    st.caption("Check the report time, regular unleaded grade, station, and any membership or cash-only conditions. These are external lookups, not an automatic price feed.")
    live_gas_input = st.number_input(
        "Gas price you checked ($/gal)",
        min_value=0.0, max_value=15.0, value=0.0, step=0.01, key="checked_gas_price",
        help="Type the regular gas price from GasBuddy or the pump. Enter 0 to mark it unknown. Saved only for this dashboard session.")
    live_gas = live_gas_input if live_gas_input >= 0.5 else None
    if live_gas is None:
        st.info("Enter a checked regular gas price above to enable the Weekly Profit Grader. No price is assumed.")
    else:
        st.caption(f"Using your entered price of ${live_gas:.2f}/gal for the 16 MPG fuel calculation. Not automatically verified.")
    tab1, tab2 = st.tabs(["🗓️ Calendar & routing", "📊 Weekly profit"])
    with tab1:
        selected_date = st.date_input("Plan your driving date", datetime.now(TZ).date())
        with st.spinner("Checking events and weather for your date…"):
            events_data = fetch_live_events(selected_date, setting("EVENT_FEEDS_JSON", "[]"))
            weather = fetch_weather(selected_date)
        st.subheader("Regional event footprint")
        st.caption("Known published events, not a guarantee of complete street activity. Impact labels are planning estimates.")
        for note in events_data["notes"]:
            st.warning(note)
        events = events_data["events"]
        if not events:
            st.info("No matching events returned by available feeds. This does not confirm normal traffic.")
        for event in events:
            with st.container(border=True):
                st.caption(event["tier"])
                st.write(f"**{event['name']}**")
                st.write(f"{event['venue']} · {'Time TBD / all day' if event['all_day'] else clock(event['start'])}")
                if event["url"]:
                    st.link_button("Event source", event["url"])
        with st.expander("Event source coverage"):
            st.dataframe(events_data["coverage"], hide_index=True, use_container_width=True)
            st.caption(f"Checked {events_data['checked'].strftime('%b %d %I:%M %p %Z')}. Athletics pages cover their published season; missing seasons or feeds are not evidence of no events.")
        st.subheader("Position & Routing Roadmap")
        st.caption(selected_date.strftime("%A, %B %d, %Y"))
        render_weather(weather)
        for slot in generate_schedule(events, base, selected_date.weekday()):
            with st.expander(f"{slot['time']} · {slot['app']}", expanded=True):
                st.write(slot["action"])
    with tab2:
        st.subheader("Weekly Profit Grader")
        st.caption("Fuel-only operating contribution. Taxes, maintenance, insurance and depreciation are not deducted.")
        if live_gas is not None:
            st.info(f"Fuel rate: ${live_gas:.2f}/gal — entered above, not automatically fetched.")
        else:
            st.warning("The grader needs a gas price. Check GasBuddy and enter it in the gas section above.")
        gas_price = live_gas
        with st.form("grader_form"):
            gross = st.number_input("Gross earnings ($)", min_value=0.0, value=340.90)
            hours = st.number_input("Total online hours", min_value=0.0, value=10.1)
            miles = st.number_input("All driven miles, including deadhead", min_value=0.0, value=98.6)
            submitted = st.form_submit_button("Grade my week", use_container_width=True, disabled=gas_price is None)
        if submitted:
            st.session_state["last_shift"] = (gross, hours, miles)
        if "last_shift" in st.session_state:
            metrics = calculate_metrics(*st.session_state["last_shift"], gas_price)
            if metrics:
                st.metric("Efficiency grade", metrics["grade"])
                for label, key, suffix in [("Gross hourly rate", "hourly_rate", "/hr"), ("Gross per mile", "mile_rate", "/mi"), ("Fuel expense", "fuel_cost", "")]:
                    st.metric(label, f"${metrics[key]:,.2f}{suffix}")
                st.success(f"After-fuel earnings: ${metrics['net_profit']:,.2f}")
                st.caption(f"Last submitted shift, recalculated at ${gas_price:.3f}/gal and {MPG:g} MPG.")
            else:
                st.warning("Enter positive hours and miles and provide an available fuel price.")


if __name__ == "__main__":
    main()
