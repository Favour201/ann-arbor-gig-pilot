"""NWS hourly work-window forecasts, separate from event/demand predictions."""
import math
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")


def work_weather_ranges(slots, day, now):
    """Two-hour preparation lead plus driving; omit rest and elapsed hours."""
    midnight = datetime.combine(day, time.min, tzinfo=TZ)
    local_now = now.astimezone(TZ)
    ranges = []
    for slot in slots:
        if slot.get("app") not in ("Delivery", "XL / Comfort"):
            continue
        start, end = slot.get("start_minute"), slot.get("end_minute")
        if start is None or end is None or end <= start:
            continue
        left = max(midnight, midnight + timedelta(minutes=start - 120), local_now)
        right = midnight + timedelta(minutes=end)
        if left < right:
            ranges.append((left, right))
    merged = []
    for left, right in sorted(ranges):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return merged


def _period_bounds(period):
    start = datetime.fromisoformat(period["startTime"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(period["endTime"].replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("Forecast needs valid timezone-aware bounds")
    return start.astimezone(TZ), end.astimezone(TZ)


def weather_for_work(weather, slots, day, now):
    """Scope hourly NWS data to preparation/driving, tracking coverage gaps."""
    ranges = work_weather_ranges(slots, day, now)
    labels = [f"{a.strftime('%a')} {a.strftime('%I:%M %p').lstrip('0')}–"
              f"{b.strftime('%I:%M %p').lstrip('0')}" for a, b in ranges]
    places = []
    for place in weather.get("places", []):
        matching, spans = [], []
        for period in place.get("periods", []):
            try:
                start, end = _period_bounds(period)
            except (KeyError, ValueError, TypeError):
                continue
            # Do not relabel a broad overnight forecast as precise hourly data.
            if end - start > timedelta(hours=1):
                continue
            if not any(start < right and end > left for left, right in ranges):
                continue
            label = f"{start.strftime('%a')} {start.strftime('%I:%M %p').lstrip('0')}"
            matching.append({**period, "name": label})
            if temperature_f(period) is not None and str(period.get("shortForecast") or "").strip():
                spans.append((start, end))
        complete = bool(ranges)
        for left, right in ranges:
            cursor = left
            for start, end in sorted(spans):
                if start > cursor:
                    break
                if end > cursor:
                    cursor = end
                if cursor >= right:
                    break
            complete = complete and cursor >= right
        hazards = [f"{p['name']}: {p.get('shortForecast', '')} "
                   f"({temperature_f(p)}°F)" for p in matching
                   if period_impact(p) != "good"]
        places.append({**place, "periods": matching, "hazards": hazards,
                       "coverage_complete": complete,
                       "status": "Available" if matching else "Work-hour forecast unavailable"})
    return {**weather, "places": places, "work_scoped": True,
            "hazards": sorted({h for p in places for h in p["hazards"]}),
            "window_label": " · ".join(labels) if ranges else "No remaining planned driving hours",
            "window_note": "Up to 2 hours before driving + planned driving hours; optional windows included."}

LABELS = {
    "good": ("☀️", "Favorable conditions", "LOW IMPACT"),
    "caution": ("🌦️", "Drive with caution", "CAUTION"),
    "high": ("⛈️", "Weather impact alert", "HIGH IMPACT"),
    "unknown": ("🌫️", "Weather outlook unavailable", "UNKNOWN"),
}
RANK = {"unknown": -1, "good": 0, "caution": 1, "high": 2}

# Very high-confidence hazardous terms go to red. Light snow and ordinary
# rain stay cautionary; freezing rain / ice are high even when temperatures
# elsewhere in the selected day's forecast are above freezing.
DANGEROUS = re.compile(
    r"\b(?:thunderstorms?|tornado(?:es)?|hail|blizzard|whiteout|"
    r"freezing rain|freezing drizzle|ice storm|icy roads?|"
    r"black ice|ice pellets|heavy snow|snow squall|"
    r"winter storm|heavy rain|torrential|downpours?|"
    r"flash flood|flood warning|damaging winds?|high wind warning)\b",
    re.IGNORECASE,
)
ICE = re.compile(
    r"\b(?:freezing rain|freezing drizzle|ice storm|icy|black ice|"
    r"ice pellets|sleet|glaze)\b",
    re.IGNORECASE,
)
SNOW = re.compile(r"\b(?:snow|snowing|flurries|wintry|winter|sleet)\b", re.IGNORECASE)
RAIN = re.compile(r"\b(?:rain|showers|drizzle)\b", re.IGNORECASE)
WIND = re.compile(r"\b(?:windy|breezy|gusty|wind gusts?|strong winds?)\b", re.IGNORECASE)
STORM = re.compile(r"\b(?:thunderstorms?|tornado(?:es)?|hail|lightning)\b", re.IGNORECASE)


def temperature_f(period):
    """Convert an NWS forecast period temperature to Fahrenheit, if valid."""
    value = period.get("temperature")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    unit = period.get("temperatureUnit", "F")
    if unit == "C":
        return value * 9 / 5 + 32
    return value if unit == "F" else None


def period_impact(period):
    text = " ".join(str(period.get(k) or "")
                    for k in ("shortForecast", "detailedForecast"))
    temp = temperature_f(period)
    wind = str(period.get("windSpeed") or "")
    speeds = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", wind)] if "mph" in wind.lower() else []
    max_wind = max(speeds, default=0)
    # NWS phrases can contain "a chance of thunderstorms", and should be
    # flagged as potential storm risk rather than presented as a certainty.
    if DANGEROUS.search(text) or (temp is not None and temp <= 15) or max_wind >= 40:
        return "high"
    if (SNOW.search(text) or RAIN.search(text) or WIND.search(text)
            or max_wind >= 20 or (temp is not None and (temp <= 32 or temp >= 95))):
        return "caution"
    return "good"


def forecast_note(severity, forecast_text):
    if severity == "unknown":
        return ("Conditions are unverified. Check an up-to-date forecast and "
                "local road conditions before driving.")
    if severity == "good":
        return ("No major weather issue found in available forecasts. "
                "Evaluate actual offers; demand isn't guaranteed.")
    if severity == "caution":
        if SNOW.search(forecast_text) or ICE.search(forecast_text):
            return ("Watch for slick roads, especially as temperatures change. "
                    "Allow extra time and verify road conditions.")
        if RAIN.search(forecast_text):
            return ("Wet roads can slow traffic. Rain may affect demand, "
                    "but doesn't guarantee profitable offers.")
        if WIND.search(forecast_text):
            return ("Be mindful of gusts and road conditions. "
                    "Only drive when actual offers justify it.")
        return ("Allow extra time and check road conditions. "
                "Only drive when actual offers justify it.")
    if ICE.search(forecast_text):
        return ("Ice or freezing precipitation may make roads dangerous. "
                "Consider delaying or skipping driving.")
    if SNOW.search(forecast_text):
        return ("Snow may reduce traction and visibility. "
                "Safety first; consider delaying or skipping driving.")
    if STORM.search(forecast_text) or RAIN.search(forecast_text):
        return ("Storms or heavy rain can reduce visibility and slow roads. "
                "Safety first; weather does not guarantee better offers.")
    return ("Potentially hazardous conditions. Check alerts and roads; "
            "consider delaying or skipping driving.")


def summarize_weather_outlook(weather):
    """Return one date-scoped conservative outlook, or unknown when incomplete.

    Important: callers must supply weather already scoped to the selected date
    using weather_for_day(); don't mix later days with today's temperatures.
    """
    places = weather.get("places") or []
    available = [p for p in places
                 if p.get("status") == "Available" and p.get("periods")]
    coverage_complete = (bool(places) and len(available) == len(places)
                         and all(p.get("coverage_complete", True) for p in available))
    if not available:
        return {
            "severity": "unknown", "title": LABELS["unknown"][1],
            "icon": LABELS["unknown"][0], "pill": LABELS["unknown"][2],
            "forecast": ("Hourly forecast unavailable for your work hours"
                         if weather.get("work_scoped") else
                         "Forecast not available for this selected date"),
            "note": forecast_note("unknown", ""),
            "temperature": None, "high": None, "low": None, "place": "",
            "period_name": "", "partial_coverage": False,
        }

    candidates = []
    for pidx, place in enumerate(available):
        for tidx, period in enumerate(place["periods"]):
            impact = period_impact(period)
            candidates.append((RANK[impact], -pidx, -tidx, place, period))
    candidates.sort(reverse=True, key=lambda p: p[:3])
    rank, _, _, place, representative = candidates[0]
    severity = next(k for k, v in RANK.items() if v == rank)
    if not coverage_complete and severity == "good":
        severity = "unknown"  # One unavailable city is never marked all-clear.

    all_periods = place["periods"]
    temps = [t for period in all_periods
             if (t := temperature_f(period)) is not None]
    # Only call them "high" and "low" when the selected day's NWS periods
    # actually include at least two temperature readings.
    high, low = (round(max(temps)), round(min(temps))) if len(temps) >= 2 else (None, None)

    short = str(representative.get("shortForecast") or "").strip()
    detailed = str(representative.get("detailedForecast") or "").strip()
    period_name = str(representative.get("name") or "").strip()
    description = short or detailed or "Check the NWS forecast for details"
    if representative.get("windSpeed"):
        description += f" · Wind {representative['windSpeed']}"
    hidden_risk = DANGEROUS.search(detailed)
    if hidden_risk and not DANGEROUS.search(short):
        description += f" · Forecast also mentions {hidden_risk.group(0).lower()}"
    temperature = temperature_f(representative)
    note = forecast_note(severity, short + " " + detailed)
    if not coverage_complete:
        note += " Some local forecast coverage is unavailable."

    if severity == "unknown":
        note = forecast_note("unknown", "")
    icon, title, pill = LABELS[severity]
    if severity == "high":
        risk_text = (short + " " + detailed)
        if (SNOW.search(risk_text) or ICE.search(risk_text)):
            icon = "🌨️"
        elif (STORM.search(risk_text)):
            icon = "⛈️"
        else:
            icon = "⚠️"
    elif severity == "caution" and (SNOW.search(short) or ICE.search(short)):
        icon = "🌨️"

    # Avoid presenting a single 12-hour NWS forecast temperature as a live
    # current observation. It belongs to the selected forecast period.
    return {
        "severity": severity, "icon": icon, "title": title, "pill": pill,
        "forecast": description, "note": note,
        "temperature": round(temperature) if temperature is not None else None,
        "high": high, "low": low, "place": str(place.get("place") or ""),
        "period_name": period_name,
        "partial_coverage": not coverage_complete,
    }

