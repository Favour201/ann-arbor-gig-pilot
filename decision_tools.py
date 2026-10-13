"""Optional driving decision check and private plan-outcome journal.

This module is not a mileage or tax ledger; Gridwise remains the operator's source
of truth for completed earnings/mileage. All estimates require driver-provided inputs.
"""
from datetime import date, datetime, timezone
from io import StringIO
import csv
import math
from uuid import uuid4

from pilot_features import _safe_text, _amount

DECISION_COLUMNS = (
    "id", "planned_date", "window_time", "window_title", "choice", "reason",
    "event_context", "weather_context", "positioning_miles_one_way",
    "gas_price", "positioning_fuel_cost", "result", "takeaway", "created_at",
)
CHOICES = ("Drive", "Monitor", "Skip")
OUTCOMES = ("Unreviewed", "Worth it", "Mixed", "Not worth it", "Skipped")


def position_fuel_cost(one_way_miles, gas_price, mpg=16.0):
    """Round-trip fuel ONLY for a driver-estimated one-way repositioning distance.

    Assumes return to the starting area; actual deadhead can be different.
    """
    miles = _amount(one_way_miles)
    price = _amount(gas_price)
    if not (mpg > 0 and math.isfinite(mpg)):
        raise ValueError("Vehicle MPG must be finite and positive")
    if price > 15:
        raise ValueError("Checked gas price is outside supported range")
    return round(2 * miles / mpg * price, 2)


def offer_check(payout=None, total_miles=None, total_minutes=None,
                gas_price=None, min_after_fuel_hour=20.0, mpg=16.0):
    """Estimate ONLY from a specific offer/scenario entered by the driver.

    All miles must include deadhead/repositioning; not Gridwise earnings reporting.
    """
    if any(v is None for v in (payout, total_miles, total_minutes, gas_price)):
        return {"status": "NO OFFER PROVIDED",
                "message": "Check a real offer in your driving app before moving for demand."}
    pay = _amount(payout)
    miles = _amount(total_miles, allow_zero=False)
    minutes = _amount(total_minutes, allow_zero=False)
    price = _amount(gas_price)
    floor = _amount(min_after_fuel_hour)
    if price < 0.5:
        return {"status": "GAS PRICE UNKNOWN",
                "message": "Enter a checked gas price before calculating after-fuel value."}
    fuel = miles / mpg * price
    hourly = (pay - fuel) / (minutes / 60)
    status = "MEETS YOUR TARGET" if hourly >= floor else "BELOW YOUR TARGET"
    return {"status": status, "fuel": round(fuel, 2),
            "after_fuel": round(pay - fuel, 2),
            "after_fuel_hour": round(hourly, 2), "target": floor,
            "message": ("Based on your entered payout, all miles and time only; "
                        "not a live quote, income prediction or profit after wear/taxes.")}


def drive_advice(day, today, weather, events, one_way_miles=0, gas_price=None,
                 max_position_fuel=3.0, work_hours=3.0):
    """Decision support, not a pretend real-time demand signal."""
    max_cost = _amount(max_position_fuel)
    hours = _amount(work_hours, allow_zero=False)
    cost = None if gas_price is None or gas_price < 0.5 else position_fuel_cost(
        one_way_miles, gas_price)
    caution = list(weather.get("hazards", []))
    forecast_unknown = not weather.get("places") or any(
        place.get("status") != "Available" for place in weather["places"])
    major_count = sum(1 for event in events
                      if event.get("tier") != "NEIGHBORHOOD / SCHOOL")
    if day < today:
        status = "REVIEW DAY"
        reason = "This is a past date; use it for planning review, not a live departure."
    elif day > today:
        status = "PLAN AHEAD"
        reason = "Future schedule only. Check offers and conditions on the driving day."
    elif caution:
        status = "CHECK CONDITIONS FIRST"
        reason = "The forecast has driving cautions. Safety outranks potential demand."
    elif cost is not None and cost > max_cost:
        status = "AVOID SPECULATIVE DEADHEAD"
        reason = ("Your estimated round-trip positioning fuel exceeds your own limit. "
                  "Look for a confirmed offer or nearer area instead.")
    elif hours < 1:
        status = "SHORT WINDOW"
        reason = "Available driving time is limited; check whether a nearby actual offer fits."
    else:
        status = "CHECK LIVE OFFERS"
        reason = ("Published events and suggested windows don't prove demand. "
                  "Only reposition after checking your actual driving apps.")
    return {"status": status, "reason": reason, "fuel_cost": cost,
            "max_fuel": max_cost, "major_events": major_count,
            "weather_unknown": forecast_unknown,
            "has_weather_caution": bool(caution),
            "available_hours": hours}


def new_decision(day, slot, choice, reason, context, one_way_miles=0,
                 gas_price=None):
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ValueError("Choose a driving date")
    if choice not in CHOICES:
        raise ValueError("Unknown choice")
    if slot.get("start_minute") is None:
        raise ValueError("Only clocked, current-plan windows can be journaled")
    cost = "" if gas_price is None or gas_price < 0.5 else position_fuel_cost(
        one_way_miles, gas_price)
    return {"id": str(uuid4()), "planned_date": day.isoformat(),
            "window_time": _safe_text(slot["time"], 90),
            "window_title": _safe_text(slot.get("title") or slot["app"], 120),
            "choice": choice, "reason": _safe_text(reason, 260),
            "event_context": _safe_text(context.get("events", ""), 280),
            "weather_context": _safe_text(context.get("weather", ""), 280),
            "positioning_miles_one_way": _amount(one_way_miles),
            "gas_price": "" if gas_price is None or gas_price < 0.5 else _amount(gas_price),
            "positioning_fuel_cost": cost, "result": "Unreviewed",
            "takeaway": "", "created_at": datetime.now(timezone.utc).isoformat()}


def decision_summary(rows, count_min=3):
    """Only descriptive self-reported outcomes; no causal improvement claim."""
    reviewed = [row for row in rows if row.get("result") in
                ("Worth it", "Mixed", "Not worth it", "Skipped")]
    worthwhile = sum(r["result"] == "Worth it" for r in reviewed)
    return {"reviewed": len(reviewed), "worthwhile": worthwhile,
            "sufficient": len(reviewed) >= count_min,
            "message": (f"{worthwhile}/{len(reviewed)} reviewed plans marked 'Worth it' "
                        "(self-reported, not an earnings prediction)."
                        if len(reviewed) >= count_min else
                        f"{len(reviewed)}/{count_min} plan outcomes reviewed; "
                        "log more before drawing conclusions.")}


def decision_csv_backup(rows):
    out = StringIO()
    writer = csv.DictWriter(out, DECISION_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in DECISION_COLUMNS})
    return out.getvalue().encode("utf-8-sig")


def decisions_worksheet(credentials, sheet_id):
    import gspread
    from pilot_features import open_sheet
    book = open_sheet(credentials, sheet_id)
    try:
        ws = book.worksheet("Decisions")
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet("Decisions", rows=1500, cols=len(DECISION_COLUMNS))
    headers = ws.row_values(1)
    if not headers:
        ws.append_row(list(DECISION_COLUMNS), value_input_option="RAW")
    elif tuple(headers) != DECISION_COLUMNS:
        raise ValueError("Decisions worksheet headers are incompatible")
    return ws


def decision_sheet_load(credentials, sheet_id):
    ws = decisions_worksheet(credentials, sheet_id)
    return sorted(ws.get_all_records(), key=lambda x: str(x.get("created_at", "")),
                  reverse=True)


def decision_sheet_append(credentials, sheet_id, row):
    ws = decisions_worksheet(credentials, sheet_id)
    ws.append_row([row.get(k, "") for k in DECISION_COLUMNS],
                  value_input_option="RAW")


def decision_sheet_review(credentials, sheet_id, record_id, outcome, note=""):
    if outcome not in OUTCOMES or outcome == "Unreviewed":
        raise ValueError("Choose an outcome")
    ws = decisions_worksheet(credentials, sheet_id)
    ids = ws.col_values(1)
    matches = [idx for idx, value in enumerate(ids, 1)
               if value == record_id and idx > 1]
    if len(matches) != 1:
        raise ValueError("Decision record not found or ambiguous")
    index = matches[0]
    # Only result/takeaway cells; preserve the original snapshot.
    ws.update(range_name=f"L{index}:M{index}",
              values=[[outcome, _safe_text(note, 320)]],
              raw=True)
