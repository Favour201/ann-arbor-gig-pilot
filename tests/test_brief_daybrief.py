"""Day assembly, skip flag, unknown-stays-unknown, and the dry-run text."""
from datetime import date, datetime, timedelta

from brief import daybrief, events as ev
from brief.config import TZ
from brief.text import render_text
from brief.weather import weather_for_day

NOW = datetime(2026, 11, 5, 15, 15, tzinfo=TZ)  # Thursday
DAYS = ev.horizon(NOW.date(), 5)


def hourly(day, text="Clear", temp=45, hours=range(24)):
    base = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
    return [{"startTime": (base + timedelta(hours=h)).isoformat(),
             "endTime": (base + timedelta(hours=h + 1)).isoformat(),
             "shortForecast": text, "temperature": temp, "temperatureUnit": "F"} for h in hours]


def forecast(periods, ypsi_ok=True):
    return {"places": [
        {"place": "Ann Arbor", "ok": True, "periods": periods, "url": "https://api.weather.gov/x", "updated": "u"},
        {"place": "Ypsilanti", "ok": ypsi_ok, "periods": periods if ypsi_ok else [], "url": "", "updated": ""}],
        "checked": NOW}


def events_data(found=(), ok=True, covered=DAYS):
    found = [{**e, "feed": "Michigan football"} for e in found]
    return {"events": list(found), "notes": [], "checked": NOW, "days": DAYS,
            "coverage": [{"source": "Michigan football", "url": "https://mgoblue.com/", "ok": ok,
                          "covered": covered if ok else (), "checked": NOW}]}


def all_days(text="Clear", temp=45):
    return [p for d in DAYS for p in hourly(d, text, temp)]


def test_thursday_clear_has_windows_green_weather_and_offer_rule():
    day = daybrief.build_day(NOW.date(), NOW, events_data(), forecast(all_days()))
    assert [w["time"] for w in day["windows"]] == ["6:00 PM – 8:30 PM", "8:30 PM onward"]
    assert [w["optional"] for w in day["windows"]] == [False, True]
    assert day["weather"]["severity"] == "good"
    assert day["skip"] is None
    assert "$3+" in day["offer_rule"]


def test_red_weather_suggests_skip_without_forecasting():
    day = daybrief.build_day(NOW.date(), NOW, events_data(), forecast(all_days("Freezing Rain", 30)))
    assert day["weather"]["severity"] == "high"
    assert day["skip"].startswith("Skip tonight?") and "not a forecast" in day["skip"]


def test_one_city_down_is_never_an_all_clear():
    day = daybrief.build_day(NOW.date(), NOW, events_data(), forecast(all_days(), ypsi_ok=False))
    assert day["weather"]["severity"] == "unknown"
    assert day["skip"] is None


def test_day_beyond_forecast_horizon_is_unknown():
    only_today = hourly(NOW.date())
    sunday = daybrief.build_day(date(2026, 11, 8), NOW, events_data(), forecast(only_today))
    assert sunday["weather"]["severity"] == "unknown"


def test_non_driving_day_has_no_windows_or_weather():
    monday = daybrief.build_day(date(2026, 11, 9), NOW, events_data(), forecast(all_days()))
    assert not monday["driving_day"]
    assert monday["windows"] == [] and monday["weather"] is None and monday["offer_rule"] is None


def test_game_day_split_and_kickoff_tbd():
    game = ev.event_record("Michigan Football vs. Penn State", "Michigan Stadium",
                           "2026-11-07T15:30:00-05:00", None, "Michigan Athletics")
    sat = daybrief.build_day(date(2026, 11, 7), NOW, events_data([game]), forecast(all_days()))
    assert [w["kind"] for w in sat["windows"]] == ["drive", "rest", "drive"]
    assert "confirmed Michigan kickoff (3:30 PM)" in sat["game_day"]
    tbd = ev.event_record("Michigan Football vs. Penn State", "Michigan Stadium", "2026-11-07", None,
                          "Michigan Athletics", all_day=True)
    sat = daybrief.build_day(date(2026, 11, 7), NOW, events_data([tbd]), forecast(all_days()))
    assert sat["game_day"] is None
    assert [e["name"] for e in sat["kickoff_tbd"]] == ["Michigan Football vs. Penn State"]


def test_failed_source_means_events_incomplete_and_text_says_so():
    brief = daybrief.build_brief(NOW, events_data(ok=False), forecast(all_days()))
    assert not brief["days"][0]["events_complete"]
    text = render_text(brief)
    assert "not an all-clear" in text
    assert "Michigan football: NOT VERIFIED" in text


def test_brief_covers_five_days_and_text_has_no_money_or_address():
    brief = daybrief.build_brief(NOW, events_data(), forecast(all_days()))
    assert [d["date"] for d in brief["days"]] == list(DAYS)
    text = render_text(brief)
    assert "Thu Nov 5" in text and "Mon Nov 9 · not a driving day" in text
    for banned in ("Arbor Circle", "surge expected", "guaranteed earnings", "profit"):
        assert banned not in text


def test_weather_for_day_shape_matches_legacy():
    sliced = weather_for_day(forecast(hourly(NOW.date())), NOW.date())
    assert {p["status"] for p in sliced["places"]} == {"Available"}
    nxt = weather_for_day(forecast(hourly(NOW.date())), date(2026, 11, 6))
    assert {p["status"] for p in nxt["places"]} == {"Selected date outside available forecast"}


def test_nws_update_time_field_fallbacks():
    from brief.weather import forecast_updated
    assert forecast_updated({"updateTime": "2026-11-05T15:00:00+00:00"}) == "2026-11-05T15:00:00+00:00"
    assert forecast_updated({"generatedAt": "g"}) == "g"
    assert forecast_updated({}) == ""


def test_failure_reason_reaches_text():
    data = events_data(ok=False)
    data["coverage"][0]["reason"] = "HTTP 403"
    text = render_text(daybrief.build_brief(NOW, data, forecast(all_days())))
    assert "Michigan football: NOT VERIFIED (HTTP 403)" in text
