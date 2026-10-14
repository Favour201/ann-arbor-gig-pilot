"""Archive import regressions use synthetic reports; no personal PDF data in GitHub."""
from datetime import date, datetime, timezone
from decimal import Decimal
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

import app
import gridwise_import as gridwise


@pytest.mark.parametrize("header,first,second", [
    ("Date,Service,Earnings", "2022-07-01,Uber Eats,22.5",
     "2022-07-01,Uber Eats,22.50"),
    ("Date,Hours,Miles,Rate,Deduction", "2022-07-01,2,10,62.5,6.25",
     "2022-07-01,2.00,10.0,62.50,6.250"),
    ("Date,Service,Earnings", "2022-07-01,Uber Eats,0",
     "2022-07-01,Uber Eats,-0.00"),
])
def test_numeric_formatting_does_not_change_archive_identity(header, first, second):
    a = gridwise.parse_gridwise_csv(f"{header}\n{first}\n".encode())["rows"][0]
    b = gridwise.parse_gridwise_csv(f"{header}\n{second}\n".encode())["rows"][0]
    assert a["row_key"] == b["row_key"]
    assert a["source_id"] != b["source_id"]


@pytest.mark.parametrize("header,line,numeric_fields", [
    ("Date,Service,Trips,Earnings", "2022-07-01,Uber Eats,2,22.50",
     ("trips", "earnings")),
    ("Date,Hours,Miles,Rate,Deduction", "2022-07-01,2.00,10.00,62.50,6.250",
     ("hours", "miles", "deduction_rate", "tax_deduction")),
])
def test_legacy_archive_dedupe_preserves_records_and_repeated_transactions(
        monkeypatch, header, line, numeric_fields):
    rows = gridwise.parse_gridwise_csv(
        f"{header}\n{line}\n{line}\n".encode())["rows"]
    assert rows[0]["row_key"] != rows[1]["row_key"]
    legacy = {**rows[0], "row_key": "existing-pre-fix-key", "source_id": "old-source"}
    # gspread returns numeric cell values, not necessarily their original text.
    for field in numeric_fields:
        legacy[field] = float(legacy[field])
    original = deepcopy(legacy)

    class Archive:
        def __init__(self):
            self.records = [legacy]
        def get_all_records(self):
            return deepcopy(self.records)
        def append_rows(self, values, value_input_option=None):
            assert value_input_option == "RAW"
            self.records.extend(dict(zip(gridwise.ARCHIVE_COLUMNS, row)) for row in values)

    sheet = Archive()
    monkeypatch.setattr(gridwise, "archive_sheet", lambda credentials, sheet_id: sheet)
    # One old transaction is skipped; the second identical occurrence is real.
    assert gridwise.archive_append_new({}, "test-sheet", rows) == (1, 1)
    assert gridwise.archive_append_new({}, "test-sheet", rows) == (0, 2)
    assert sheet.records[0] == original
    assert len(sheet.records) == 2


def test_archive_identity_keeps_missing_values_and_distinct_amounts_separate():
    rows = gridwise.parse_gridwise_csv(
        b"Date,Service,Trips,Earnings\n2022-07-01,Uber Eats,,22.50\n"
        b"2022-07-01,Uber Eats,0,22.50\n2022-07-01,Uber Eats,0,22.51\n"
    )["rows"]
    assert len({row["row_key"] for row in rows}) == 3


def test_gridwise_earnings_pdf_parses_zero_trip_adjustments_and_tips(monkeypatch):
    # Mimics pypdf extraction, where date/service and trip/payout may be glued.
    text = ("Gridwise\n2022\n$35.00\nEarnings\n"
            "191\nTrips\nGridwise\nEarnings Report\n"
            "May 1, 2022Uber Eats 2$30.00\n"
            "May 2, 2022Uber 0$0.50\n"
            "May 3, 2022DoorDash Tips $4.50\n")
    monkeypatch.setattr(gridwise, "_extract_pdf_text", lambda data: text)
    result = gridwise.parse_gridwise_pdf(b"%PDF sample")
    assert result["type"] == "earnings"
    assert len(result["rows"]) == 3
    assert sum(Decimal(x["earnings"]) for x in result["rows"]) == Decimal("35.00")
    assert result["rows"][1]["trips"] == "0"
    assert result["rows"][2]["category"] == "Tips"
    assert result["rows"][2]["trips"] == ""
    assert all(x["cohort"].startswith("Archive only") for x in result["rows"])


def test_gridwise_mileage_pdf_parses_glued_hours_miles_and_validates_total(monkeypatch):
    text = ("Gridwise\n$12.50\nTax Deduction\n"
            "Mileage Report\nMay 1, 2022 2.010.0 62.5¢ $6.25\n"
            "May 2, 2022 1.0 10.0 62.5¢ $6.25\n")
    monkeypatch.setattr(gridwise, "_extract_pdf_text", lambda data: text)
    parsed = gridwise.parse_gridwise_pdf(b"%PDF mileage")
    assert parsed["type"] == "mileage"
    assert len(parsed["rows"]) == 2
    assert parsed["rows"][0]["hours"] == "2.0"
    assert parsed["rows"][0]["miles"] == "10.0"
    assert gridwise.report_summary(parsed)["total"] == 20
    monkeypatch.setattr(gridwise, "_extract_pdf_text",
                        lambda data: text.replace("$12.50", "$32.50"))
    with pytest.raises(ValueError, match="totals failed validation"):
        gridwise.parse_gridwise_pdf(b"%PDF mileage")


def test_gridwise_csv_detection_and_strict_validation():
    raw = (b"Date,Service,Category,Trips,Earnings\n"
           b"2022-06-01,Uber Eats,,2,$32.25\n"
           b"2022-06-01,DoorDash,Tips,,$5.00\n")
    parsed = gridwise.parse_gridwise_upload("gridwise.csv", raw)
    assert parsed["format"] == "csv" and parsed["type"] == "earnings"
    assert parsed["rows"][0]["platform"] == "Uber Eats"
    assert parsed["rows"][1]["earnings"] == "5.00"
    m = gridwise.parse_gridwise_csv(
        b'Date,Hours,Miles,Rate,Deduction\n"June 1, 2022",3.5,42.5,62.5\xc2\xa2,$26.56\n')
    assert m["type"] == "mileage"
    assert m["rows"][0]["deduction_rate"] == "62.5"
    with pytest.raises(ValueError):
        gridwise.parse_gridwise_csv(b"Date,Nothing Else\n2022-01-01,no\n")
    with pytest.raises(ValueError):
        gridwise.parse_gridwise_csv(b"Date,Miles,Hours\n2022-01-01,4,-2\n")
    with pytest.raises(ValueError):
        gridwise.parse_gridwise_upload("fake.xlsx", raw)


def test_gridwise_dedupe_exact_reupload_and_partial_daily_match():
    earnings = gridwise.parse_gridwise_csv(
        b"Date,Service,Trips,Earnings\n2022-06-01,Uber Eats,3,35.00\n"
        b"2022-06-02,Uber,0,0.50\n")
    again = gridwise.parse_gridwise_csv(
        b"Date,Service,Trips,Earnings\n2022-06-01,Uber Eats,3,35.00\n"
        b"2022-06-02,Uber,0,0.50\n")
    mileage = gridwise.parse_gridwise_csv(
        b"Date,Hours,Miles\n2022-06-01,3.5,41.0\n"
        b"2022-06-03,4.0,62.2\n")
    assert earnings["rows"][0]["row_key"] == again["rows"][0]["row_key"]
    joined = gridwise.join_by_date(earnings["rows"] + mileage["rows"])
    assert len(joined) == 3
    assert joined[0]["earnings"] == 35
    assert joined[0]["miles"] == 41
    assert joined[1]["miles"] is None  # No invented zero mileage.
    assert joined[2]["earnings"] is None


def test_import_uses_separate_private_sheet_and_skips_duplicates(monkeypatch):
    class Missing(Exception): pass
    class WS:
        def __init__(self):
            self.header = []
            self.rows = []
        def row_values(self, index):
            return self.header
        def append_row(self, values, value_input_option=None):
            assert value_input_option == "RAW"
            self.header = list(values)
        def get_all_records(self):
            return [dict(zip(gridwise.ARCHIVE_COLUMNS, v)) for v in self.rows]
        def append_rows(self, rows, value_input_option=None):
            assert value_input_option == "RAW"
            self.rows.extend(rows)
    class Book:
        def __init__(self):
            self.ws = None
        def worksheet(self, name):
            assert name == "Gridwise_Archive"
            if not self.ws: raise Missing()
            return self.ws
        def add_worksheet(self, name, rows, cols):
            assert name == "Gridwise_Archive"
            assert cols == len(gridwise.ARCHIVE_COLUMNS)
            self.ws = WS()
            return self.ws
    book = Book()
    import pilot_features
    monkeypatch.setattr(pilot_features, "open_sheet",
                        lambda credentials, sheet_id: book)
    monkeypatch.setitem(sys.modules, "gspread",
                        SimpleNamespace(WorksheetNotFound=Missing))
    result = gridwise.parse_gridwise_csv(
        b"Date,Service,Earnings\n2022-07-01,Uber Eats,22.5\n")
    first = gridwise.archive_append_new({}, "private", result["rows"])
    second = gridwise.archive_append_new({}, "private", result["rows"])
    assert first == (1, 0)
    assert second == (0, 1)
    assert len(gridwise.archive_load({}, "private")) == 1
    assert book.ws.header == list(gridwise.ARCHIVE_COLUMNS)


def test_results_view_has_no_feeds_or_manual_grading_until_advanced():
    weather = {"places": [{"place": "Ann Arbor", "status": "Available",
                           "hazards": [], "periods": []}], "hazards": [],
               "checked": datetime(2026, 10, 13, tzinfo=timezone.utc)}
    events = {"events": [], "notes": [], "coverage": [],
              "checked": datetime(2026, 10, 13, tzinfo=timezone.utc)}
    with patch.object(app, "fetch_live_events", return_value=events) as ef, \
         patch.object(app, "fetch_weather", return_value=weather) as wf:
        at = AppTest.from_string("from app import main\nmain()", default_timeout=25).run()
        assert not at.exception
        assert at.session_state["pilot_workspace"] == "Drive"
        assert not at.number_input
        assert not any("Private decision journal" in x.label for x in at.expander)
        assert not any("Before you leave" in t.label for t in at.toggle)
        assert not any("Import Gridwise reports" in x.label for x in at.expander)
        assert any("Add planned shifts to calendar" in x.label for x in at.expander)
        at.segmented_control[0].set_value("Results & Learning").run()
        assert not at.exception
        assert not at.number_input
        assert not any("Grade my week" == b.label for b in at.button)
        assert any("Import Gridwise reports" in x.label for x in at.expander)
        assert ef.call_count == 1
        assert wf.call_count == 1

