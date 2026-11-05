"""NWS hourly forecast: fetched once per run, sliced per day.

The hourly forecast covers roughly the next 6 days, so one fetch per city
serves the whole 5-day page. Days past the forecast horizon come back as
"outside available forecast", which weather_outlook turns into UNKNOWN, never
an all-clear.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import re
from urllib.parse import urlparse

import requests

from .config import POINTS, TZ
from .events import local_dt, overlaps
from .http import get

COLD_F = 15  # Operational threshold, not an official NWS warning criterion.


def fetch_forecast():
    """One hourly forecast per city. Never raises; failures are recorded per city."""
    def run(point):
        name, (lat, lon) = point
        try:
            properties = get(f"https://api.weather.gov/points/{lat},{lon}").json()["properties"]
            forecast_url = properties["forecastHourly"]
            if urlparse(forecast_url).hostname != "api.weather.gov":
                raise ValueError("Unexpected forecast host")
            payload = get(forecast_url).json()["properties"]
            periods = payload["periods"]
            if not isinstance(periods, list):
                raise ValueError("Unexpected forecast format")
            return {"place": name, "ok": True, "periods": periods, "url": forecast_url,
                    "updated": payload.get("updated", "")}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return {"place": name, "ok": False, "periods": [], "url": "", "updated": ""}
    with ThreadPoolExecutor(max_workers=2) as pool:
        places = list(pool.map(run, POINTS.items()))
    return {"places": places, "checked": datetime.now(TZ)}


def periods_for_day(periods, day):
    """Periods overlapping the given local day, plus coarse hazard labels (legacy weather_for_day)."""
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


def weather_for_day(forecast, day):
    """Same shape the legacy fetch_weather(day) returned, so weather_outlook works unchanged."""
    places = []
    for place in forecast["places"]:
        if not place["ok"]:
            places.append({"place": place["place"], "periods": [], "hazards": [],
                           "status": "NWS unavailable", "url": ""})
            continue
        periods, hazards = periods_for_day(place["periods"], day)
        places.append({"place": place["place"], "periods": periods, "hazards": hazards,
                       "url": place["url"], "updated": place["updated"],
                       "status": "Available" if periods else "Selected date outside available forecast"})
    return {"places": places, "hazards": sorted({h for p in places for h in p["hazards"]}),
            "checked": forecast["checked"]}
