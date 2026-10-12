from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
import csv
import io

from icalendar import Calendar
import pytest

import app
import pilot_features as pilot


def test_calendar_export_uses_real_330_game_plan_and_selected_shifts():
    saturday = date(2026, 10, 17)
    game = app.event_record("Michigan Football", "Michigan Stadium",
                            "2026-10-17T15:30:00-04:00", None, "U-M")
    schedule = app.generate_schedule([game], app.BASE, 5,
                                     stop_buffer_minutes=60, return_after_hours=2.0)
    assert [s["time"] for s in schedule] == [
        "12:30 PM – 2:30 PM", "2:30 PM – 5:30 PM", "5:30 PM onward"]
    entries = pilot.selected_calendar_entries(schedule, saturday, selected=[0, 1, 2],
                                               onward_hours=3)
    assert len(entries) == 2  # Rest isn't a paid driving shift.
    assert [e["start"].strftime("%H:%M") for e in entries] == ["12:30", "17:30"]
    assert [e["end"].strftime("%H:%M") for e in entries] == ["14:30", "20:30"]
    assert "Calendar duration chosen by driver" in entries[-1]["description"]
    with_break = pilot.selected_calendar_entries(schedule, saturday,
                                                  selected=[0, 1, 2], include_break=True)
    assert len(with_break) == 3
    assert with_break[1]["title"] == "Head home & recharge"


def test_calendar_google_template_and_ics_utc_dst_compatible():
    saturday = date(2026, 10, 17)
    game = app.event_record("Michigan Football", "Michigan Stadium",
                            "2026-10-17T15:30:00-04:00", None, "U-M")
    slots = app.generate_schedule([game], app.BASE, 5)
    entries = pilot.selected_calendar_entries(slots, saturday, selected=[0, 2],
                                               onward_hours=2)
    link = pilot.calendar_google_link(entries[0])
    parsed = urlparse(link)
    args = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.hostname == "calendar.google.com"
    assert args["action"] == ["TEMPLATE"]
    assert args["dates"] == ["20261017T163000Z/20261017T184500Z"]
    assert args["ctz"] == ["America/New_York"]
    ics = pilot.calendar_ics(entries)
    cal = Calendar.from_ical(ics)
    events = cal.walk("VEVENT")
    assert len(events) == 2
    assert events[0].decoded("DTSTART") == entries[0]["start"].astimezone(timezone.utc)
    assert events[0].decoded("DTEND") == entries[0]["end"].astimezone(timezone.utc)
    assert all(e.get("UID") and e.get("DESCRIPTION") for e in events)


def test_tbd_not_invented_as_calendar_entry():
    saturday = date(2026, 10, 17)
    unknown = app.event_record("Michigan Football", "Michigan Stadium",
                               "2026-10-17", None, "Official", all_day=True)
    schedule = app.generate_schedule([unknown], app.BASE, 5)
    entries = pilot.selected_calendar_entries(schedule, saturday)
    assert all(e["start"].hour != 0 for e in entries)
    assert all("kickoff tbd" not in e["title"].lower() for e in entries)
    assert not any("unknown" in e["title"].lower() for e in entries)
    with pytest.raises(ValueError):
        pilot.selected_calendar_entries(schedule, saturday, selected=[500])


def test_calendar_fall_back_dst_offset_is_utc_derived():
    day = date(2026, 11, 1)  # DST ends in Michigan.
    slots = [{"app": "Delivery", "time": "5:00 PM – 8:00 PM",
              "start_minute": 17*60, "end_minute": 20*60,
              "action": "Driver can choose."}]
    entry, = pilot.selected_calendar_entries(slots, day)
    assert entry["start"].astimezone(timezone.utc).hour == 22
    assert entry["end"].astimezone(timezone.utc).hour == 1


def test_shifts_fuel_costs_other_cost_optional_and_csv_backup():
    row = pilot.new_shift(date(2026, 10, 17), "Uber", "Ann Arbor", "Game pregame",
                          140, 4, 64, 3.75, 0.12, "=HYPERLINK(1)")
    metrics = pilot.shift_metrics(row)
    assert metrics["fuel_cost"] == 15
    assert metrics["after_fuel"] == 125
    assert metrics["other_cost"] == 7.68
    assert metrics["after_extra_costs"] == 117.32
    assert row["notes"].startswith("'=")  # Spreadsheet formula injection guarded.
    restored = pilot.csv_restore(pilot.csv_backup([row]))
    assert len(restored) == 1
    assert restored[0]["id"] == row["id"]
    assert pilot.shift_metrics(restored[0])["after_fuel"] == 125
    assert len(pilot.merge_shift_rows([row], restored)) == 1


def test_shift_validation_csv_limits_and_no_assumed_gas_price():
    with pytest.raises(ValueError):
        pilot.new_shift(date(2026, 10, 17), "Uber", "Ann Arbor", "Dinner",
                        100, 3, 50, 0)
    with pytest.raises(ValueError):
        pilot.new_shift(date(2026, 10, 17), "Uber", "Ann Arbor", "Dinner",
                        100, 0, 50, 3.50)
    with pytest.raises(ValueError):
        pilot.csv_restore(b"not,a,gig,pilot,backup\n")
    with pytest.raises(ValueError):
        pilot.csv_restore(b"0" * 500001)


def test_weekday_benchmark_requires_three_actual_shifts():
    target = date(2026, 10, 24)  # Saturday
    rows = []
    for weeks in (1, 2, 3):
        rows.append(pilot.new_shift(target - timedelta(weeks=weeks), "Uber",
                                    "Ann Arbor", "Dinner", 100 + weeks * 10,
                                    4, 64, 4))
    two = pilot.daily_history_brief(rows[:2], target)
    assert not two["enough"] and two["count"] == 2
    three = pilot.daily_history_brief(rows, target)
    assert three["enough"] and three["count"] == 3
    assert "not a prediction" in three["message"].lower()
    assert "median" in three["message"].lower()
    wrong_day = pilot.daily_history_brief(rows, date(2026, 10, 25))
    assert not wrong_day["enough"]


def test_daily_brief_handles_feed_gap_and_bad_weather_without_false_guarantees():
    events = {"events": [], "coverage": [
        {"Source": "Marquee", "Status": "Not verified"}]}
    weather = {"hazards": ["Rain"], "places": [{"status": "Available"}]}
    brief = pilot.daily_decision_brief(date(2026, 10, 17), events, weather)
    assert "unverified" in brief["events"].lower()
    assert "does not prove" in brief["events"].lower()
    assert "no surge is confirmed" in brief["weather"].lower()
    assert not brief["history"]["enough"]


def test_sheet_adapter_creates_header_and_uses_raw_values(monkeypatch):
    # No real credentials or external API calls. Validate the Sheets schema behavior.
    class MissingSheet(Exception): pass
    class FakeWorksheet:
        def __init__(self):
            self.headers = []
            self.rows = []
        def row_values(self, index):
            return self.headers
        def append_row(self, values, value_input_option=None):
            assert value_input_option == "RAW"
            if not self.headers:
                self.headers = values
            else:
                self.rows.append(values)
        def get_all_records(self):
            return [dict(zip(pilot.SHIFT_COLUMNS, x)) for x in self.rows]
    class FakeBook:
        def __init__(self):
            self.sheet = None
        def worksheet(self, name):
            if self.sheet is None:
                raise MissingSheet()
            return self.sheet
        def add_worksheet(self, name, rows, cols):
            self.sheet = FakeWorksheet()
            assert name == "Shifts" and cols == len(pilot.SHIFT_COLUMNS)
            return self.sheet
    book = FakeBook()
    monkeypatch.setattr(pilot, "open_sheet", lambda credentials, sheet_id: book)
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "gspread",
                        SimpleNamespace(WorksheetNotFound=MissingSheet))
    record = pilot.new_shift(date(2026, 10, 17), "Uber", "Ann Arbor",
                              "Dinner", 90, 3, 40, 3.5)
    pilot.sheet_append({"fake": "credentials"}, "private-sheet-id", record)
    rows = pilot.sheet_load({"fake": "credentials"}, "private-sheet-id")
    assert len(rows) == 1 and rows[0]["id"] == record["id"]
    assert book.sheet.headers == list(pilot.SHIFT_COLUMNS)
