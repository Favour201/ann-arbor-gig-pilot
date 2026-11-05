"""Event parsers (ported from tests/test_app.py) plus the new range/bucketing behavior."""
import json
from datetime import date, datetime

import pytest
import requests

from brief import events as ev
from brief.config import TIERS

DAY = date(2026, 10, 17)


def nuxt(table):
    return '<script id="__NUXT_DATA__">' + json.dumps(table) + '</script>'


def sidearm_table(day_iso, time_text, tbd, location="H"):
    return [None, {"date": 2, "opponent": 3, "location_indicator": 4, "location": 5, "time": 6, "tbd": 7},
            day_iso, {"title": 8}, location, "Ann Arbor, Mich.", time_text, tbd, "Penn State"]


# ------------------------------------------------------------ ported parsers
def test_home_schedule_and_away_dropped():
    table = sidearm_table("2026-10-17T15:30:00", "3:30 PM", False)
    events = ev.parse_sidearm(nuxt(table), (DAY,), "Michigan", "football", "https://mgoblue.com/")
    assert events[0]["tier"] == "CRITICAL IMPACT"
    assert events[0]["start"].minute == 30
    table[4] = "A"
    assert not ev.parse_sidearm(nuxt(table), (DAY,), "Michigan", "football", "")


def test_calendar_all_categories_and_cancellation():
    venues = ["Michigan Stadium", "Rynearson Stadium", "Crisler Center", "Hill Auditorium", "Pioneer High School"]
    rows = [dict(name="Football" if i == 0 else "Event", venue=v, start="2026-10-17T19:30:00-04:00")
            for i, v in enumerate(venues)]
    rows += [dict(rows[0], status="cancelled"), dict(rows[0], venue="Detroit")]
    found = ev.parse_extra(json.dumps({"events": rows}), "json", (DAY,), "Test", "")
    assert {e["tier"] for e in found} == set(TIERS)
    assert len(found) == 5


def test_recurring_ics():
    text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:1\r\n"
            "DTSTART;TZID=America/New_York:20261001T190000\r\nDTEND;TZID=America/New_York:20261001T210000\r\n"
            "RRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:School athletics\r\nLOCATION:Pioneer High School\r\n"
            "END:VEVENT\r\nEND:VCALENDAR")
    found = ev.parse_extra(text, "ics", (date(2026, 10, 8),), "School", "")
    assert len(found) == 1 and found[0]["start"].day == 8
    week = ev.horizon(date(2026, 10, 1), 15)
    assert len(ev.parse_extra(text, "ics", week, "School", "")) == 3


def test_marquee_official_card_and_selected_date():
    html = """<main><article><h3>Kenny Wayne Shepherd</h3>
    <div>Wednesday, October 14th, 2026 at 8:00 PM | Main Auditorium</div></article>
    <article><h3>Future Artist</h3><p>Wednesday, November 11, 2026 at 7:00 PM</p></article></main>"""
    found = ev.parse_marquee_events(html, (date(2026, 10, 14),), "https://marquee-arts.org/live-events/")
    assert len(found) == 1
    assert found[0]["name"] == "Kenny Wayne Shepherd"
    assert found[0]["tier"] == "PERFORMING ARTS"
    assert (found[0]["start"].hour, found[0]["start"].minute) == (20, 0)
    unknown_year = "<article><h3>Future show</h3><p>Saturday, February 6 at 8:00 PM</p></article>"
    assert ev.parse_marquee_events(unknown_year, (date(2026, 2, 6),), "") == []


def test_eventlink_local_only_and_no_invented_tbd_start():
    html = """<h1>EVENTS</h1>
    <h4>Football (Boys V)</h4>
    <div>Ann Arbor Huron High School (H)</div><div>Fri, Oct. 16 2026 7:00 PM EDT</div>
    <div>Pioneer High School</div>
    <h4>Soccer (Boys V)</h4><div>Dexter High School (A)</div>
    <div>Fri, Oct. 16 2026 7:00 PM EDT</div><div>Dexter High School</div>
    <h4>Field Hockey (Girls V)</h4><div>Regional (H)</div>
    <div>Fri, Oct. 16 2026 TBD</div><div>Pioneer High School</div>"""
    found = ev.parse_eventlink_events(html, (date(2026, 10, 16),), "Pioneer", "https://pioneerathletics.net/Events")
    assert len(found) == 2
    assert any(e["all_day"] for e in found)
    assert any(e["start"].hour == 19 for e in found)
    assert all("Dexter High School" not in e["venue"] for e in found)


SCHOOL_HTML = """<h2>October 2026</h2><h3>Friday, October 16</h3>
    <div>Pioneer Events Fall Choir Concert</div><div>7:30 PM - 9:00 PM</div>
    <div>Pioneer High School, Ann Arbor</div>
    <h3>Saturday, October 17</h3><div>School is closed</div><div>All Day</div>"""


def test_school_calendar_uses_only_visible_month_and_timed_events():
    found, covered = ev.parse_school_calendar(SCHOOL_HTML, (date(2026, 10, 16),), "Pioneer", "")
    assert len(found) == 1
    assert (found[0]["start"].hour, found[0]["start"].minute) == (19, 30)
    assert found[0]["tier"] == "NEIGHBORHOOD / SCHOOL"
    assert covered == (date(2026, 10, 16),)
    with pytest.raises(ValueError, match="month not visible"):
        ev.parse_school_calendar(SCHOOL_HTML, (date(2026, 11, 16),), "Pioneer", "")


def test_theater_bad_markup_reported_as_unknown():
    with pytest.raises(ValueError):
        ev.parse_marquee_events("<html><h1>Blocked</h1></html>", (date(2026, 10, 14),), "")
    with pytest.raises(ValueError):
        ev.parse_eventlink_events("<p>unavailable</p>", (date(2026, 10, 14),), "Pioneer", "")


def test_official_schedule_clock_explicit_not_midnight_guessed():
    tba, is_tbd = ev.resolve_published_start("2026-10-17T15:30:00-04:00", "TBA")
    assert is_tbd and tba.hour == 0
    early, is_tbd = ev.resolve_published_start("2026-10-17", "12:00 PM")
    assert not is_tbd and early.hour == 12
    midnight, is_tbd = ev.resolve_published_start("2026-10-17", "")
    assert is_tbd and midnight.hour == 0
    noon, is_tbd = ev.resolve_published_start("2026-10-17", "Noon")
    assert not is_tbd and noon.hour == 12


def test_school_impact_for_performance_is_traffic_not_demand():
    school = ev.event_record("Pioneer Fall Choir Concert", "Pioneer High School",
                             "2026-10-15T19:00:00-04:00", None, "School")
    impact = ev.event_impact(school)
    assert "Performance arrival/departure" in impact["traffic"]
    assert "uncertain" in impact["demand"]


# ------------------------------------------------------- new: range behavior
def test_sidearm_range_keeps_every_home_game_in_window_only():
    table = sidearm_table("2026-11-07T15:30:00", "3:30 PM", False)
    week = ev.horizon(date(2026, 11, 5), 5)  # Thu..Mon
    assert len(ev.parse_sidearm(nuxt(table), week, "Michigan", "football", "")) == 1
    later = ev.horizon(date(2026, 11, 8), 5)
    assert ev.parse_sidearm(nuxt(table), later, "Michigan", "football", "") == []


def test_school_calendar_spanning_months_covers_only_visible_month():
    days = ev.horizon(date(2026, 10, 30), 5)  # Oct 30 .. Nov 3
    found, covered = ev.parse_school_calendar(SCHOOL_HTML, days, "Pioneer", "")
    assert found == []  # Oct 16 isn't in range
    assert covered == (date(2026, 10, 30), date(2026, 10, 31))


def test_horizon_is_consecutive_and_crosses_month():
    assert ev.horizon(date(2026, 11, 28), 5)[-1] == date(2026, 12, 2)


# --------------------------------------------- new: one fetch, bucket by day
def test_fetch_events_fetches_each_source_once_and_buckets(monkeypatch):
    days = ev.horizon(date(2026, 11, 5), 5)
    calls = []
    football = nuxt(sidearm_table("2026-11-07T15:30:00", "3:30 PM", False))

    class Resp:
        def __init__(self, text="", data=None):
            self.text, self._data = text, data
        def json(self):
            return self._data

    def fake_get(url, params=None, **_):
        calls.append(url)
        if "events.umich.edu" in url:
            return Resp(data=[])
        if "mgoblue.com/sports/football" in url:
            return Resp(football)
        raise requests.ConnectionError("blocked in test")

    monkeypatch.setattr(ev, "get", fake_get)
    data = ev.fetch_events(days)
    assert len(calls) == len(set(calls)) == len(ev.source_jobs()[0])  # once per source
    saturday = ev.events_for_day(data, date(2026, 11, 7))
    friday = ev.events_for_day(data, date(2026, 11, 6))
    assert [e["name"] for e in saturday["events"]] == ["Michigan Football vs. Penn State"]
    assert friday["events"] == []
    status = {c["source"]: c for c in saturday["coverage"]}
    assert status["Michigan football"]["status"] == "Checked • 1 matches"
    assert status["U-M campus / Hill Auditorium"]["verified"]
    assert not status["Michigan Theater (Marquee Arts)"]["verified"]
    assert status["Michigan Theater (Marquee Arts)"]["status"].startswith("Not verified")


def test_bad_extra_feed_settings_are_noted_not_fatal():
    jobs, notes = ev.source_jobs('{"not": "a list"}')
    assert notes and len(jobs) == len(ev.source_jobs()[0])
    _, notes = ev.source_jobs('[{"name": "x", "format": "json", "url": "http://insecure"}]')
    assert notes


# ------------------------------------------- failure reasons (diagnostics)
def test_failure_reason_is_short_and_specific():
    resp = requests.Response()
    resp.status_code = 403
    assert ev.failure_reason(requests.HTTPError(response=resp)) == "HTTP 403"
    assert ev.failure_reason(requests.ReadTimeout()) == "Timed out"
    assert ev.failure_reason(requests.ConnectionError("x")) == "Connection failed"
    assert ev.failure_reason(ValueError("Selected month not visible\nin school calendar")) == \
        "ValueError: Selected month not visible in school calendar"
    assert len(ev.failure_reason(ValueError("x" * 500))) == 160


def test_fetch_events_records_reason_per_failed_source(monkeypatch):
    def fake_get(url, params=None, **_):
        if "events.umich.edu" in url:
            raise requests.ReadTimeout()
        return type("R", (), {"text": "<html>nothing here</html>"})()
    monkeypatch.setattr(ev, "get", fake_get)
    data = ev.fetch_events(ev.horizon(date(2026, 11, 5), 5))
    reasons = {c["source"]: c["reason"] for c in data["coverage"]}
    assert reasons["U-M campus / Hill Auditorium"] == "Timed out"
    assert reasons["Michigan football"] == "ValueError: Schedule structure changed"
    assert reasons["Pioneer Athletics"] == "ValueError: Athletics listing markup unavailable"


# ------------------------------------------- feed fixes from the first live run
EVENTLINK_TABLE = """<h1>EVENTS 11/5/26 - 11/9/26</h1><table><tbody>
<tr><td><h4 class="fw-bold my-auto">Hockey (Girls V)</h4></td>
<td><p class="my-0">Academy of the Sacred Heart <span title="Home Game">(H)</span></p></td>
<td><p class="m-0">Thu, Nov. 5 2026</p> <p class="m-0">6:00 PM EST</p></td>
<td><p class="my-0">Pioneer High School (Yost Ice Arena)</p></td><td><a>DETAILS</a></td></tr>
<tr><td><h4>Hockey (Girls V)</h4></td><td><p>Wash U <span>(A)</span></p></td>
<td><p>Fri, Nov. 6 2026</p> <p>5:00 PM EST</p></td><td></td><td><a>DETAILS</a></td></tr>
<tr><td><h4>Cross Country (Boys V)</h4></td><td><p>MHSAA State Finals @ MIS <span>(A)</span></p></td>
<td><p>Sat, Nov. 7 2026</p> <p>TBD</p></td><td><p>MHSAA State Finals @ MIS</p></td><td><a>DETAILS</a></td></tr>
</tbody></table>"""


def test_eventlink_table_markup_keeps_date_and_time_together():
    days = ev.horizon(date(2026, 11, 5), 5)
    found = ev.parse_eventlink_events(EVENTLINK_TABLE, days, "Pioneer", "https://pioneerathletics.net/Events")
    assert len(found) == 1  # away game with no venue and the out-of-town meet are dropped
    game = found[0]
    assert (game["start"].day, game["start"].hour) == (5, 18)
    assert game["venue"] == "Pioneer High School (Yost Ice Arena)"
    assert "Hockey (Girls V)" in game["name"] and "Academy of the Sacred Heart (H)" in game["name"]


def test_sidearm_slugs_match_current_sites():
    urls = {url for _, fmt, url, _ in ev.source_jobs()[0] if fmt == "sidearm"}
    assert "https://mgoblue.com/sports/mens-ice-hockey/schedule" in urls
    assert "https://emueagles.com/sports/womens-volleyball/schedule" in urls
    assert "https://emueagles.com/sports/womens-gymnastics/schedule" in urls
    assert not any(u.endswith(("/ice-hockey/schedule", "/volleyball/schedule", "/gymnastics/schedule"))
                   and "womens" not in u and "mens-" not in u for u in urls)


def test_school_calendars_listed_as_not_covered_instead_of_failing():
    names = {name for name, *_ in ev.source_jobs()[0]}
    assert not any("School Calendar" in n for n in names)
    assert any("school calendars" in n for n in ev.NOT_COVERED)


def test_each_request_asks_for_one_content_type(monkeypatch):
    seen = {}
    def fake_get(url, params=None, accept=None, **_):
        seen[url] = accept
        raise requests.ConnectionError()
    monkeypatch.setattr(ev, "get", fake_get)
    ev.fetch_events(ev.horizon(date(2026, 11, 5), 5))
    assert seen["https://events.umich.edu/list/json"] == "application/json"
    assert seen["https://mgoblue.com/sports/football/schedule"] is None  # default: HTML
