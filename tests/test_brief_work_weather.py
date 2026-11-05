"""Ported from tests/test_work_weather.py: weather scoped to prep + driving hours."""
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from brief import weather_outlook as outlook
from brief.config import BASE_LABEL, TZ
from brief.events import event_record
from brief.plan import generate_schedule


FRIDAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 14, 10, tzinfo=TZ)


def hour(day, h, text="Clear", temp=60, **extra):
    start = datetime.combine(day, datetime.min.time(), tzinfo=TZ) + timedelta(hours=h)
    return {"startTime": start.isoformat(), "endTime": (start + timedelta(hours=1)).isoformat(),
            "name": "", "shortForecast": text, "temperature": temp, "temperatureUnit": "F", **extra}


def forecast(periods, second=None):
    return {"places": [{"place": "Ann Arbor", "status": "Available", "periods": periods, "url": ""},
                       {"place": "Ypsilanti", "status": "Available",
                        "periods": periods if second is None else second, "url": ""}],
            "hazards": ["Old overnight storm"], "checked": NOW}


def scope(periods, second=None, now=NOW):
    return outlook.weather_for_work(forecast(periods, second),
                                    generate_schedule([], BASE_LABEL, 4), FRIDAY, now)


def test_friday_drops_thursday_night_and_early_friday_storms():
    periods = [hour(FRIDAY, h, "Thunderstorms" if h < 15 else "Clear",
                    45 if h < 15 else 60) for h in range(24)]
    original = deepcopy(periods)
    result = scope(periods)
    card = outlook.summarize_weather_outlook(result)
    assert card["severity"] == "good"
    assert card["period_name"] == "Fri 3:00 PM"
    assert card["high"] == card["low"] == 60
    assert result["window_label"] == "Fri 3:00 PM–12:00 AM"
    assert result["hazards"] == []
    assert periods == original


def test_evening_risk_is_highlighted_with_its_actual_hour_and_temperature():
    periods = [hour(FRIDAY, h, "Chance of Thunderstorms" if h == 19 else "Clear",
                    51 if h == 19 else 60) for h in range(15, 24)]
    result = outlook.summarize_weather_outlook(scope(periods))
    assert result["severity"] == "high"
    assert result["period_name"] == "Fri 7:00 PM"
    assert result["temperature"] == 51
    assert (result["low"], result["high"]) == (51, 60)


@pytest.mark.parametrize("second", [[], [hour(FRIDAY, h) for h in range(15, 23)],
                                    [hour(FRIDAY, h) for h in range(15, 24) if h != 18]])
def test_missing_city_horizon_or_middle_hour_never_claims_all_clear(second):
    result = outlook.summarize_weather_outlook(scope([hour(FRIDAY, h) for h in range(15, 24)], second))
    assert result["severity"] == "unknown"
    assert result["partial_coverage"]


def test_partial_coverage_preserves_known_hazard_and_discloses_gap():
    card = outlook.summarize_weather_outlook(scope([hour(FRIDAY, 19, "Freezing Rain", 31)], []))
    assert card["severity"] == "high" and card["partial_coverage"]
    assert "unavailable" in card["note"]


def test_overnight_broad_period_and_invalid_hour_are_not_hourly_fallbacks():
    broad = hour(date(2026, 10, 15), 18, "Thunderstorms")
    broad["endTime"] = "2026-10-16T18:00:00-04:00"
    invalid = hour(FRIDAY, 16)
    invalid["startTime"] = "not a timestamp"
    result = outlook.summarize_weather_outlook(scope([broad, invalid]))
    assert result["severity"] == "unknown"
    assert "work hours" in result["forecast"]


def test_today_omits_elapsed_weather_and_after_work_has_no_forecast():
    periods = [hour(FRIDAY, h, "Thunderstorms" if h < 19 else "Clear") for h in range(15, 24)]
    result = scope(periods, now=datetime(2026, 10, 16, 19, tzinfo=TZ))
    assert outlook.summarize_weather_outlook(result)["severity"] == "good"
    assert result["window_label"].startswith("Fri 7:00 PM")
    done = scope(periods, now=datetime(2026, 10, 17, 0, tzinfo=TZ))
    assert done["window_label"] == "No remaining planned driving hours"
    assert outlook.summarize_weather_outlook(done)["severity"] == "unknown"


def test_thursday_sunday_and_game_preferences_set_work_ranges():
    for day, start, end in [(date(2026, 10, 15), "16:00", "00:00"),
                            (date(2026, 10, 18), "14:30", "21:30")]:
        ranges = outlook.work_weather_ranges(generate_schedule([], BASE_LABEL, day.weekday()), day, NOW)
        assert [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in ranges] == [(start, end)]
    game = event_record("Michigan Football", "Michigan Stadium", "2026-10-17T15:30:00-04:00", None, "Fixture")
    slots = generate_schedule([game], BASE_LABEL, 5, stop_buffer_minutes=60, return_after_hours=3.0)
    ranges = outlook.work_weather_ranges(slots, date(2026, 10, 17), NOW)
    assert [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in ranges] == [
        ("10:30", "14:30"), ("16:30", "00:00")]
    assert outlook.work_weather_ranges([{"app": "Delivery", "start_minute": None,
                                         "end_minute": None}], FRIDAY, NOW) == []
