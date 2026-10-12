import json
from datetime import date, datetime, timedelta
from unittest.mock import patch
import app

NOW = datetime(2026, 10, 12, 10, tzinfo=app.TZ)



def test_weather_overnight_and_horizon():
    period = dict(startTime='2026-10-12T18:00:00-04:00', endTime='2026-10-13T06:00:00-04:00',
                  shortForecast='Rain and snow showers', temperature=-10, temperatureUnit='C')
    matched, hazards = app.weather_for_day([period], date(2026, 10, 13))
    assert len(matched) == 1
    assert {'Rain', 'Snow / ice', 'Extreme cold (≤15°F)'} <= set(hazards)
    assert app.weather_for_day([period], date(2026, 10, 14)) == ([], [])


def test_weather_midnight_exclusive():
    p = dict(startTime='2026-10-12T18:00:00-04:00', endTime='2026-10-13T00:00:00-04:00', shortForecast='Storms')
    assert app.weather_for_day([p], date(2026, 10, 13)) == ([], [])


def test_home_schedule_and_tbd():
    table = [None, {'date': 2, 'opponent': 3, 'location_indicator': 4, 'location': 5, 'time': 6, 'tbd': 7},
             '2026-10-17T15:30:00', {'title': 8}, 'H', 'Ann Arbor, Mich.', '3:30 PM', False, 'Penn State']
    html = '<script id="__NUXT_DATA__">'+json.dumps(table)+'</script>'
    events = app.parse_sidearm(html, date(2026, 10, 17), 'Michigan', 'football', 'https://mgoblue.com/')
    assert events[0]['tier'] == 'CRITICAL IMPACT'
    assert events[0]['start'].minute == 30
    table[4] = 'A'
    assert not app.parse_sidearm('<script id="__NUXT_DATA__">'+json.dumps(table)+'</script>', date(2026, 10, 17), 'Michigan', 'football', '')


def test_calendar_all_categories_and_cancellation():
    venues = ['Michigan Stadium', 'Rynearson Stadium', 'Crisler Center', 'Hill Auditorium', 'Pioneer High School']
    rows = [dict(name='Football' if i == 0 else 'Event', venue=v, start='2026-10-17T19:30:00-04:00') for i,v in enumerate(venues)]
    rows += [dict(rows[0], status='cancelled'), dict(rows[0], venue='Detroit')]
    events = app.parse_extra(json.dumps({'events': rows}), 'json', date(2026, 10, 17), 'Test', '')
    assert {e['tier'] for e in events} == set(app.TIERS)
    assert len(events) == 5


def test_recurring_ics():
    text = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTART;TZID=America/New_York:20261001T190000\r\nDTEND;TZID=America/New_York:20261001T210000\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:School athletics\r\nLOCATION:Pioneer High School\r\nEND:VEVENT\r\nEND:VCALENDAR'
    events = app.parse_extra(text, 'ics', date(2026, 10, 8), 'School', '')
    assert len(events) == 1
    assert events[0]['start'].day == 8


def test_thursday_keeps_football_and_minutes():
    ev = app.event_record('Michigan Football', 'Michigan Stadium', '2026-10-15T15:30:00-04:00', None, 'Test')
    slots = app.generate_schedule([ev], app.BASE, 3)
    assert any(s['time'] == '12:30 PM – 3:30 PM' for s in slots)
    assert any(s['app'] == 'XL / Comfort' for s in slots)


def test_profit():
    metrics = app.calculate_metrics(100, 4, 160, 3)
    assert metrics['fuel_cost'] == 30
    assert metrics['net_profit'] == 70
    assert app.calculate_metrics(100, 0, 20, 3) is None
    assert app.calculate_metrics(100, 4, 20, None) is None


def test_offline_ui_and_grader():
    from streamlit.testing.v1 import AppTest
    events = {'events': [], 'notes': ['Additional feeds not connected'], 'coverage': [], 'checked': NOW}
    weather = {'places': [{'place':'Ann Arbor', 'status':'NWS unavailable', 'periods':[], 'hazards':[], 'url':''}], 'hazards':[], 'checked':NOW}
    with patch.object(app, 'fetch_live_events', return_value=events), patch.object(app, 'fetch_weather', return_value=weather):
        at = AppTest.from_string('from app import main\nmain()', default_timeout=20).run()
        assert not at.exception
        assert next(b for b in at.button if b.label == 'Grade my week').disabled
        next(n for n in at.number_input if n.label == "Gas price you checked ($/gal)").set_value(3.79).run()
        assert not next(b for b in at.button if b.label == 'Grade my week').disabled
        next(b for b in at.button if b.label == 'Grade my week').click().run()
        assert not at.exception
        assert any('After-fuel earnings' in x.value for x in at.success)

def test_gasbuddy_links_and_explicit_manual_price():
    assert "gasbuddy.com/home?search=Ann%20Arbor" in app.GASBUDDY_ANN_ARBOR_URL
    assert "gasbuddy.com/home?search=Ypsilanti" in app.GASBUDDY_YPSILANTI_URL
    assert app.calculate_metrics(100, 4, 160, 3.79)["fuel_cost"] == 37.9
    assert app.calculate_metrics(100, 4, 160, None) is None


def test_marquee_official_card_and_selected_date():
    html = """<main><article><h3>Kenny Wayne Shepherd</h3>
    <div>Wednesday, October 14th, 2026 at 8:00 PM | Main Auditorium</div></article>
    <article><h3>Future Artist</h3><p>Wednesday, November 11, 2026 at 7:00 PM</p></article></main>"""
    found = app.parse_marquee_events(html, date(2026, 10, 14), "https://marquee-arts.org/live-events/")
    assert len(found) == 1
    assert found[0]["name"] == "Kenny Wayne Shepherd"
    assert found[0]["tier"] == "PERFORMING ARTS"
    assert (found[0]["start"].hour, found[0]["start"].minute) == (20, 0)
    # A yearless event must not be assigned to whichever year the driver selects.
    unknown_year = "<article><h3>Future show</h3><p>Saturday, February 6 at 8:00 PM</p></article>"
    assert app.parse_marquee_events(unknown_year, date(2026, 2, 6), "https://marquee-arts.org/live-events/") == []


def test_eventlink_local_only_and_no_invented_tbd_start():
    html = """<h1>EVENTS</h1>
    <h4>Football (Boys V)</h4>
    <div>Ann Arbor Huron High School (H)</div><div>Fri, Oct. 16 2026 7:00 PM EDT</div>
    <div>Pioneer High School</div>
    <h4>Soccer (Boys V)</h4><div>Dexter High School (A)</div>
    <div>Fri, Oct. 16 2026 7:00 PM EDT</div><div>Dexter High School</div>
    <h4>Field Hockey (Girls V)</h4><div>Regional (H)</div>
    <div>Fri, Oct. 16 2026 TBD</div><div>Pioneer High School</div>"""
    found = app.parse_eventlink_events(html, date(2026, 10, 16), "Pioneer", "https://pioneerathletics.net/Events")
    assert len(found) == 2
    assert any(e["all_day"] for e in found)
    assert any(e["start"].hour == 19 for e in found)
    assert all("Dexter High School" not in e["venue"] for e in found)


def test_school_calendar_uses_only_visible_month_and_timed_events():
    html = """<h2>October 2026</h2><h3>Friday, October 16</h3>
    <div>Pioneer Events Fall Choir Concert</div><div>7:30 PM - 9:00 PM</div>
    <div>Pioneer High School, Ann Arbor</div>
    <h3>Saturday, October 17</h3><div>School is closed</div><div>All Day</div>"""
    found = app.parse_school_calendar(html, date(2026, 10, 16), "Pioneer", "https://pioneer.a2schools.org/calendar")
    assert len(found) == 1
    assert found[0]["start"].hour == 19 and found[0]["start"].minute == 30
    assert found[0]["tier"] == "NEIGHBORHOOD / SCHOOL"
    import pytest
    with pytest.raises(ValueError, match="month not visible"):
        app.parse_school_calendar(html, date(2026, 11, 16), "Pioneer", "")


def test_theater_bad_markup_reported_as_unknown():
    import pytest
    with pytest.raises(ValueError):
        app.parse_marquee_events("<html><h1>Blocked</h1></html>", date(2026, 10, 14), "")
    with pytest.raises(ValueError):
        app.parse_eventlink_events("<p>unavailable</p>", date(2026, 10, 14), "Pioneer", "")


def test_roadmap_chronology_early_events_before_evening_shift():
    stadium = app.event_record("Michigan Football", "Michigan Stadium",
                               "2026-10-15T15:30:00-04:00", None, "Fixture")
    theater = app.event_record("Show", "Michigan Theater",
                               "2026-10-15T20:00:00-04:00", None, "Fixture")
    slots = app.generate_schedule([theater, stadium], app.BASE, 3)
    assert slots[0]["time"] == "12:30 PM – 3:30 PM"
    assert slots[1]["time"] == "6:00 PM – 8:30 PM"
    assert slots[2]["time"] == "7:00 PM – 8:00 PM"
    assert slots[3]["time"] == "8:30 PM onward"


def test_school_traffic_on_by_default_but_remains_optional():
    school = app.event_record("Pioneer Fall Choir Concert", "Pioneer High School",
                              "2026-10-15T19:00:00-04:00", None, "School")
    stadium = app.event_record("Michigan Football", "Michigan Stadium",
                               "2026-10-15T15:30:00-04:00", None, "U-M")
    default = app.generate_schedule([school, stadium], app.BASE, 3)
    assert any(slot["app"].startswith("NEIGHBORHOOD") for slot in default)
    assert any("Pioneer campus" in slot["action"] for slot in default)
    assert any(slot["app"].startswith("CRITICAL") for slot in default)
    opt_out = app.generate_schedule([school, stadium], app.BASE, 3, include_local=False)
    assert not any(slot["app"].startswith("NEIGHBORHOOD") for slot in opt_out)
    traffic_only = app.generate_schedule([school, stadium], app.BASE, 3, include_event_cues=False)
    assert any(slot["app"].startswith("NEIGHBORHOOD") for slot in traffic_only)
    assert not any(slot["app"].startswith("CRITICAL") for slot in traffic_only)
    baseline = app.generate_schedule([school, stadium], app.BASE, 3,
                                     include_local=False, include_event_cues=False)
    assert all(slot["app"] in ("Delivery", "XL / Comfort") for slot in baseline)
    impact = app.event_impact(school)
    assert "Performance arrival/departure" in impact["traffic"]
    assert "uncertain" in impact["demand"]



def test_unknown_kickoff_timing_is_separate_and_last():
    mystery = app.event_record("Unconfirmed kickoff", "Michigan Stadium",
                               "2026-10-15", None, "Fixture", all_day=True)
    slots = app.generate_schedule([mystery], app.BASE, 3)
    assert slots[-1]["time"] == "Kickoff TBD • timing unconfirmed"
    assert "advisory" in slots[-1]["app"]
    assert slots[0]["time"] == "6:00 PM – 8:30 PM"

def test_michigan_kickoff_tbd_then_official_time_updates():
    table = [None, {"date": 2, "opponent": 3, "location_indicator": 4,
                    "location": 5, "time": 6, "tbd": 7},
             "2026-11-21", {"title": 8}, "H", "Ann Arbor, Mich.", "TBA", True,
             "Ohio State"]
    def fetch_fixture():
        return '<script id="__NUXT_DATA__">' + json.dumps(table) + '</script>'

    unknown = app.parse_sidearm(fetch_fixture(), date(2026, 11, 21),
                                "Michigan", "football", "https://mgoblue.com/sports/football/schedule")
    assert len(unknown) == 1
    assert unknown[0]["all_day"] is True
    assert app.event_time_label(unknown[0]) == "Kickoff TBD"
    assert app.generate_schedule(unknown, app.BASE, 5)[-1]["time"].startswith("Kickoff TBD")

    # Same published game with an announced kickoff: this is fetched again after cache refresh.
    table[6], table[7] = "3:30 PM ET", False
    announced = app.parse_sidearm(fetch_fixture(), date(2026, 11, 21),
                                  "Michigan", "football", "https://mgoblue.com/sports/football/schedule")
    assert len(announced) == 1
    assert not announced[0]["all_day"]
    assert (announced[0]["start"].hour, announced[0]["start"].minute) == (15, 30)
    assert app.event_time_label(announced[0]) == "3:30 PM"
    assert any(slot["time"] == "12:30 PM – 3:30 PM"
               for slot in app.generate_schedule(announced, app.BASE, 5))


def test_official_schedule_clock_explicit_not_midnight_guessed():
    tba, is_tbd = app.resolve_published_start("2026-10-17T15:30:00-04:00", "TBA")
    assert is_tbd and tba.hour == 0
    early, is_tbd = app.resolve_published_start("2026-10-17", "12:00 PM")
    assert not is_tbd and early.hour == 12
    midnight, is_tbd = app.resolve_published_start("2026-10-17", "")
    assert is_tbd and midnight.hour == 0
    noon, is_tbd = app.resolve_published_start("2026-10-17", "Noon")
    assert not is_tbd and noon.hour == 12



def test_midnight_pilot_greeting_uses_ogbuefi_only():
    assert app.greeting_for_hour(7) == "Good morning, Ogbuefi"
    assert app.greeting_for_hour(12) == "Good afternoon, Ogbuefi"
    assert app.greeting_for_hour(19) == "Good evening, Ogbuefi"
    import pytest
    with pytest.raises(ValueError):
        app.greeting_for_hour(24)


def test_compact_roadmap_headlines_preserve_details():
    football = app.event_record("Michigan Football vs Penn State", "Michigan Stadium",
                                "2026-10-15T15:30:00-04:00", None, "Michigan Athletics")
    lineup = app.generate_schedule([football], app.BASE, 3)
    assert app.roadmap_title(lineup[0]) == "Michigan Football vs Penn State"
    assert app.roadmap_title(lineup[1]) == "Delivery window"
    assert "Traffic:" in lineup[0]["action"]
    assert lineup[0]["time"] == "12:30 PM – 3:30 PM"


def test_dark_theme_css_and_mobile_ux_markers():
    assert "pilot-greeting" in app.MIDNIGHT_CSS
    assert "#101113" in app.MIDNIGHT_CSS
    assert "#8BC8FF" not in app.MIDNIGHT_CSS
    assert "#84C0FF" not in app.MIDNIGHT_CSS
    assert "@media(max-width:600px)" in app.MIDNIGHT_CSS
    assert ":focus-visible" in app.MIDNIGHT_CSS



def test_focus_views_keep_chronology_and_tbd():
    football = app.event_record("Michigan Football", "Michigan Stadium",
                                "2026-10-15T15:30:00-04:00", None, "Fixture")
    pioneer = app.event_record("Pioneer Fall Choir Concert", "Pioneer High School",
                                "2026-10-15T19:00:00-04:00", None, "Fixture")
    untimed = app.event_record("Michigan Football kickoff", "Michigan Stadium",
                                "2026-10-15", None, "Fixture", all_day=True)
    timeline = app.generate_schedule([pioneer, football, untimed], app.BASE, 3)
    assert len(app.filter_roadmap(timeline, "All")) == len(timeline)
    driving = app.filter_roadmap(timeline, "Driving")
    alerts = app.filter_roadmap(timeline, "Traffic & events")
    assert len(driving) == 2
    assert len(driving) + len(alerts) == len(timeline)
    assert alerts[0]["time"] == "12:30 PM – 3:30 PM"
    assert alerts[-1]["start_minute"] is None
    assert alerts[-1]["end_minute"] is None
    import pytest
    with pytest.raises(ValueError):
        app.filter_roadmap(timeline, "Unknown")


def test_remaining_filter_keeps_active_does_not_guess_tbd():
    thursday = date(2026, 10, 15)
    moment = datetime(2026, 10, 15, 19, 0, tzinfo=app.TZ)
    event = app.event_record("Michigan Football kickoff", "Michigan Stadium",
                             "2026-10-15", None, "Fixture", all_day=True)
    slots = app.generate_schedule([event], app.BASE, 3)
    remaining = app.filter_roadmap(slots, selected_date=thursday, now=moment, remaining=True)
    assert not any(s["time"] == "6:00 PM – 8:30 PM" and s["end_minute"] < 19*60
                   for s in remaining)
    assert any(s["time"] == "6:00 PM – 8:30 PM" for s in remaining)
    assert any(s["start_minute"] is None for s in remaining)
    upcoming = app.next_planning_window(slots, thursday, moment)
    assert upcoming["time"] == "6:00 PM – 8:30 PM"
    later = datetime(2026, 10, 15, 23, 55, tzinfo=app.TZ)
    assert app.next_planning_window(slots, thursday, later) is None
    assert app.next_planning_window(slots, date(2026, 10, 14), moment) is None
    assert app.next_planning_window(slots, date(2026, 10, 16), moment) is not None


def test_graphite_native_theme_avoids_blue():
    from pathlib import Path
    config = Path(".streamlit/config.toml").read_text()
    assert '#101113' in config
    assert '#CFCAC1' in config
    assert '#84C0FF' not in config
    assert "ROADMAP_VIEWS" not in config
