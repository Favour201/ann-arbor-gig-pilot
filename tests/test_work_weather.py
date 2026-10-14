"""Hourly forecast selection uses synthetic NWS responses, never live weather."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

import app
import weather_outlook as outlook

FRIDAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 14, 10, tzinfo=app.TZ)


def hour(day, h, text="Clear", temp=60, **extra):
    start = datetime.combine(day, datetime.min.time(), tzinfo=app.TZ) + timedelta(hours=h)
    return {"startTime": start.isoformat(), "endTime": (start + timedelta(hours=1)).isoformat(),
            "name": "", "shortForecast": text, "temperature": temp, "temperatureUnit": "F", **extra}


def forecast(periods, second=None):
    return {"places": [{"place": "Ann Arbor", "status": "Available", "periods": periods, "url": ""},
                       {"place": "Ypsilanti", "status": "Available",
                        "periods": periods if second is None else second, "url": ""}],
            "hazards": ["Old overnight storm"], "checked": NOW}


def scope(periods, second=None, now=NOW):
    return outlook.weather_for_work(forecast(periods, second),
                                    app.generate_schedule([], app.BASE, 4), FRIDAY, now)


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
    result = scope(periods, now=datetime(2026, 10, 16, 19, tzinfo=app.TZ))
    assert outlook.summarize_weather_outlook(result)["severity"] == "good"
    assert result["window_label"].startswith("Fri 7:00 PM")
    done = scope(periods, now=datetime(2026, 10, 17, 0, tzinfo=app.TZ))
    assert done["window_label"] == "No remaining planned driving hours"
    assert outlook.summarize_weather_outlook(done)["severity"] == "unknown"


def test_thursday_sunday_and_game_preferences_set_work_ranges():
    for day, start, end in [(date(2026, 10, 15), "16:00", "00:00"),
                            (date(2026, 10, 18), "14:30", "21:30")]:
        ranges = outlook.work_weather_ranges(app.generate_schedule([], app.BASE, day.weekday()), day, NOW)
        assert [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in ranges] == [(start, end)]
    game = app.event_record("Michigan Football", "Michigan Stadium", "2026-10-17T15:30:00-04:00", None, "Fixture")
    slots = app.generate_schedule([game], app.BASE, 5, stop_buffer_minutes=60, return_after_hours=3.0)
    ranges = outlook.work_weather_ranges(slots, date(2026, 10, 17), NOW)
    assert [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in ranges] == [
        ("10:30", "14:30"), ("16:30", "00:00")]
    assert outlook.work_weather_ranges([{"app": "Delivery", "start_minute": None,
                                         "end_minute": None}], FRIDAY, NOW) == []


def test_hourly_fetch_uses_nws_hourly_url_and_retains_forecast_bounds(monkeypatch):
    app.fetch_weather.clear()
    calls = []
    def get(url):
        calls.append(url)
        payload = ({"forecastHourly": "https://api.weather.gov/gridpoints/DTX/1,1/forecast/hourly"}
                   if "/points/" in url else {"periods": [hour(FRIDAY, 17)], "updated": "test"})
        return SimpleNamespace(json=lambda: {"properties": payload})
    monkeypatch.setattr(app, "get", get)
    result = app.fetch_weather(FRIDAY)
    assert len(calls) == 4
    assert sum(url.endswith("/forecast/hourly") for url in calls) == 2
    assert result["places"][0]["periods"][0]["startTime"].startswith("2026-10-16T17")
    app.fetch_weather.clear()


@pytest.mark.parametrize("speed,expected", [("5 mph", "good"), ("20 mph", "caution"), ("40 mph", "high")])
def test_hourly_wind_speed_is_considered_when_short_forecast_is_clear(speed, expected):
    assert outlook.period_impact(hour(FRIDAY, 17, windSpeed=speed)) == expected


def test_hourly_card_labels_scope_and_range_instead_of_daily_high_low():
    at = AppTest.from_string(
        "from datetime import datetime, date, timedelta\nimport app\n"
        "import weather_outlook as w\n"
        "day = date(2026,10,16)\nperiods=[]\n"
        "for h in range(15,24):\n"
        "    start=datetime(2026,10,16,h,tzinfo=app.TZ)\n"
        "    periods.append(dict(startTime=start.isoformat(),endTime=(start+timedelta(hours=1)).isoformat(),"
        "shortForecast='Clear',temperature=60,temperatureUnit='F'))\n"
        "weather={'places':[{'place':'Ann Arbor','status':'Available','periods':periods}]}\n"
        "app.render_weather(w.weather_for_work(weather,app.generate_schedule([],app.BASE,4),"
        "day,datetime(2026,10,14,tzinfo=app.TZ)),details=False)", default_timeout=25).run()
    assert not at.exception
    html = " ".join(item.value for item in at.markdown)
    assert "Fri 3:00 PM–12:00 AM" in html
    assert "Work-hour range" in html and "Forecast high" not in html
    assert "Fri 3:00 PM" in html and "Thursday Night" not in html


def test_both_workspaces_keep_adjusted_game_weather_after_navigation():
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    game = app.event_record("Michigan Football", "Michigan Stadium",
                            "2026-10-17T15:30:00-04:00", None, "Fixture")
    events = {"events": [game], "notes": [], "coverage": [], "checked": NOW}
    weather = forecast([hour(date(2026, 10, 17), h) for h in range(10, 24)])
    with patch.object(app, "datetime", FixedDatetime), \
         patch.object(app, "fetch_live_events", return_value=events), \
         patch.object(app, "fetch_weather", return_value=weather):
        at = AppTest.from_string("from app import main\nmain()", default_timeout=25).run()
        at.date_input[0].set_value(date(2026, 10, 17)).run()
        next(s for s in at.slider if s.label == "Stop before kickoff (minutes)").set_value(60).run()
        next(s for s in at.slider if s.label == "Return after kickoff (hours)").set_value(3.0).run()
        expected = "Sat 10:30 AM–2:30 PM · Sat 4:30 PM–12:00 AM"
        assert not at.exception
        assert expected in " ".join(m.value for m in at.markdown)
        at.segmented_control[0].set_value("Events & Traffic").run()
        assert not at.exception
        assert expected in " ".join(m.value for m in at.markdown)
        at.segmented_control[0].set_value("Drive").run()
        assert not at.exception
        assert next(s for s in at.slider if s.label == "Return after kickoff (hours)").value == 3.0
        assert expected in " ".join(m.value for m in at.markdown)
