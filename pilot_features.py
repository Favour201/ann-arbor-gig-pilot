"""Optional personal driving tools: calendar export, shift history and evidence briefs.

Never store secrets here or fetch private records without owner authentication in app.py.
"""
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
import csv
import math
import statistics
from urllib.parse import urlencode
from uuid import uuid4, uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

TZ = ZoneInfo("America/New_York")
SHIFT_COLUMNS = (
    "id", "shift_date", "platform", "area", "window", "gross", "hours",
    "miles", "gas_price", "other_cost_per_mile", "notes", "created_at",
)
DRIVING_CATEGORIES = ("Delivery", "XL / Comfort")


def selected_calendar_entries(slots, day, *, selected=None, onward_hours=2,
                              include_break=False):
    """Use *actual* chronologically selected slots; no invented TBD kickoff time.

    Open-ended "onward" entries require the driver's chosen planning duration.
    """
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ValueError("Select a calendar date")
    if onward_hours not in (1, 2, 3, 4, 5):
        raise ValueError("Choose a 1-5 hour duration for open-ended shifts")
    selected = list(range(len(slots))) if selected is None else list(selected)
    result = []
    for index in selected:
        if not isinstance(index, int) or not 0 <= index < len(slots):
            raise ValueError("Unknown planning window selection")
        slot = slots[index]
        category = slot["app"]
        if category not in DRIVING_CATEGORIES and not (include_break and category == "Break / Reset"):
            continue
        start_minute, end_minute = slot.get("start_minute"), slot.get("end_minute")
        if start_minute is None:  # An unknown-time event must never become midnight.
            continue
        if end_minute is None:
            continue
        # The existing schedule uses 1440 for midnight as a bound. "Onward" is
        # NOT a confirmed end: override it with the driver-selected duration.
        if "onward" in slot["time"].lower():
            end_minute = start_minute + onward_hours * 60
        if end_minute <= start_minute:
            continue
        start = datetime.combine(day + timedelta(days=start_minute // 1440), time.min,
                                 tzinfo=TZ) + timedelta(minutes=start_minute % 1440)
        end = datetime.combine(day + timedelta(days=end_minute // 1440), time.min,
                               tzinfo=TZ) + timedelta(minutes=end_minute % 1440)
        if end <= start:
            continue
        title = slot.get("title") or (
            "Delivery shift" if category == "Delivery" else
            "Rideshare shift" if category == "XL / Comfort" else "Rest / reset")
        description = (slot.get("action", "") +
                       "\nDriver planning suggestion only. Confirm actual offers, "
                       "traffic and safety before starting. Changes do not sync automatically.")
        if "onward" in slot["time"].lower():
            description += f"\nCalendar duration chosen by driver: {onward_hours} hour(s), not a verified shift end."
        result.append({"title": str(title), "start": start, "end": end,
                       "description": description, "location": "Ann Arbor / Ypsilanti, MI"})
    return result


def calendar_google_link(entry):
    start = entry["start"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end = entry["end"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    values = {"action": "TEMPLATE", "text": entry["title"],
              "dates": start + "/" + end,
              "details": entry["description"], "location": entry["location"],
              "ctz": "America/New_York"}
    return "https://calendar.google.com/calendar/render?" + urlencode(values)


def calendar_ics(entries):
    """Portable RFC5545 calendar in UTC, compatible with Apple and Outlook."""
    calendar = Calendar()
    calendar.add("prodid", "-//Washtenaw Gig Pilot Pro//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    for item in entries:
        event = Event()
        event.add("summary", item["title"])
        event.add("dtstart", item["start"].astimezone(timezone.utc))
        event.add("dtend", item["end"].astimezone(timezone.utc))
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("uid", str(uuid5(NAMESPACE_URL, "|".join((
            str(item["title"]), item["start"].isoformat(), item["end"].isoformat()))))
                  + "@ogbuefi-gig-pilot")
        event.add("description", item["description"])
        event.add("location", item["location"])
        calendar.add_component(event)
    return calendar.to_ical()


def _safe_text(value, limit=240):
    text = str(value).replace("\x00", "").strip()[:limit]
    # Both Sheets and Excel must treat user-provided notes as text, not formulas.
    if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        text = "'" + text
    return text


def _amount(value, *, allow_zero=True):
    v = float(value)
    if not math.isfinite(v) or v < 0 or (not allow_zero and v <= 0):
        raise ValueError("Shift values must be finite, nonnegative numbers")
    return round(v, 4)


def new_shift(shift_date, platform, area, window, gross, hours, miles,
              gas_price, other_cost_per_mile=0.0, notes=""):
    if not isinstance(shift_date, date) or isinstance(shift_date, datetime):
        raise ValueError("Shift date is required")
    if not 0.5 <= _amount(gas_price) <= 15:
        raise ValueError("Use an actual checked regular gas price")
    return {
        "id": str(uuid4()),
        "shift_date": shift_date.isoformat(),
        "platform": _safe_text(platform, 80),
        "area": _safe_text(area, 100),
        "window": _safe_text(window, 100),
        "gross": _amount(gross),
        "hours": _amount(hours, allow_zero=False),
        "miles": _amount(miles, allow_zero=False),
        "gas_price": _amount(gas_price),
        "other_cost_per_mile": _amount(other_cost_per_mile),
        "notes": _safe_text(notes, 320),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def shift_metrics(row, mpg=16.0):
    gross = _amount(row["gross"])
    hours = _amount(row["hours"], allow_zero=False)
    miles = _amount(row["miles"])
    fuel = miles / mpg * _amount(row["gas_price"])
    other = miles * _amount(row.get("other_cost_per_mile", 0))
    return {"after_fuel": round(gross - fuel, 2),
            "after_extra_costs": round(gross - fuel - other, 2),
            "after_fuel_hour": round((gross - fuel) / hours, 2),
            "gross_hour": round(gross / hours, 2),
            "fuel_cost": round(fuel, 2), "other_cost": round(other, 2)}


def csv_backup(rows):
    out = StringIO()
    writer = csv.DictWriter(out, SHIFT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in SHIFT_COLUMNS})
    return out.getvalue().encode("utf-8-sig")


def csv_restore(upload_bytes):
    if len(upload_bytes) > 500_000:
        raise ValueError("CSV backup too large")
    decoded = upload_bytes.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(decoded))
    if tuple(reader.fieldnames or ()) != SHIFT_COLUMNS:
        raise ValueError("Not a Gig Pilot shift-history backup")
    rows = []
    for row in reader:
        if len(rows) >= 1500:
            raise ValueError("Too many history rows")
        date.fromisoformat(row["shift_date"])
        _amount(row["hours"], allow_zero=False)
        _amount(row["miles"], allow_zero=False)
        _amount(row["gross"])
        _amount(row["gas_price"])
        _amount(row["other_cost_per_mile"] or 0)
        rows.append({key: _safe_text(row[key], 320) if key in
                    ("platform", "area", "window", "notes") else row[key]
                     for key in SHIFT_COLUMNS})
    return rows


def merge_shift_rows(*groups):
    """Dedupe by stable IDs; CSV import never silently duplicates shifts."""
    rows = {}
    for group in groups:
        for row in group:
            if row.get("id"):
                rows[str(row["id"])] = row
    return sorted(rows.values(), key=lambda r: (str(r["shift_date"]), str(r["created_at"])),
                  reverse=True)


def daily_history_brief(rows, selected_date, min_sample=3):
    """Descriptive weekday comparison only; never call it a demand forecast."""
    cutoff = selected_date - timedelta(days=90)
    matched = []
    for row in rows:
        try:
            when = date.fromisoformat(str(row["shift_date"]))
            if cutoff <= when <= selected_date and when.weekday() == selected_date.weekday():
                matched.append(shift_metrics(row)["after_fuel_hour"])
        except (ValueError, KeyError, TypeError, ZeroDivisionError):
            continue
    if len(matched) < min_sample:
        return {"enough": False, "count": len(matched),
                "message": f"Only {len(matched)}/{min_sample} matching weekday shifts logged "
                           "in the past 90 days. Not enough history for a benchmark."}
    median_rate = round(statistics.median(matched), 2)
    return {"enough": True, "count": len(matched), "median": median_rate,
            "message": (f"Your median after-fuel rate across {len(matched)} recorded "
                        f"{selected_date.strftime('%A')} shifts: USD {median_rate:.2f}/online hour. "
                        "Historical observation, not a prediction.")}


def open_sheet(credentials, sheet_id):
    """Caller must authenticate the user before opening this private sheet."""
    import gspread
    client = gspread.service_account_from_dict(dict(credentials))
    return client.open_by_key(sheet_id)


def shifts_worksheet(credentials, sheet_id):
    import gspread
    spreadsheet = open_sheet(credentials, sheet_id)
    try:
        worksheet = spreadsheet.worksheet("Shifts")
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet("Shifts", rows=2000, cols=len(SHIFT_COLUMNS))
    headers = worksheet.row_values(1)
    if not headers:
        worksheet.append_row(list(SHIFT_COLUMNS), value_input_option="RAW")
    elif tuple(headers) != SHIFT_COLUMNS:
        raise ValueError("Shifts worksheet column headers are incompatible")
    return worksheet


def sheet_load(credentials, sheet_id):
    sheet = shifts_worksheet(credentials, sheet_id)
    return merge_shift_rows(sheet.get_all_records())


def sheet_append(credentials, sheet_id, row):
    sheet = shifts_worksheet(credentials, sheet_id)
    sheet.append_row([row.get(k, "") for k in SHIFT_COLUMNS], value_input_option="RAW")
