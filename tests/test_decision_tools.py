from datetime import date, datetime, timezone
from types import SimpleNamespace
import sys

import pytest
from streamlit.testing.v1 import AppTest

import app
import decision_tools as decision


def test_predeparture_cost_uses_checked_price_and_explicit_round_trip():
    assert decision.position_fuel_cost(8, 4) == 4.0  # 16 deadhead mi at 16 MPG
    assert decision.position_fuel_cost(0, 4) == 0
    with pytest.raises(ValueError):
        decision.position_fuel_cost(10, float("inf"))
    with pytest.raises(ValueError):
        decision.position_fuel_cost(-1, 3.5)


def test_drive_advice_does_not_claim_unobserved_demand():
    day = date(2026, 10, 13)
    base_weather = {"hazards": [], "places": [
        {"status": "Available", "place": "Ann Arbor"},
        {"status": "Available", "place": "Ypsilanti"}]}
    critical = [{"tier": "CRITICAL IMPACT", "name": "Michigan Football"}]
    a = decision.drive_advice(day, day, base_weather, critical,
                              one_way_miles=2, gas_price=4)
    assert a["status"] == "CHECK LIVE OFFERS"
    assert a["major_events"] == 1
    assert "actual driving apps" in a["reason"]
    costlier = decision.drive_advice(day, day, base_weather, critical,
                                     one_way_miles=9, gas_price=4,
                                     max_position_fuel=3)
    assert costlier["status"] == "AVOID SPECULATIVE DEADHEAD"
    assert costlier["fuel_cost"] == 4.5
    caution = decision.drive_advice(day, day, {"hazards": ["Snow"],
                                                "places": [{"status": "Available"}]},
                                   critical, one_way_miles=0, gas_price=4)
    assert caution["status"] == "CHECK CONDITIONS FIRST"
    assert "Safety" in caution["reason"]
    assert decision.drive_advice(date(2026, 10, 12), day,
                                  base_weather, [])["status"] == "REVIEW DAY"
    assert decision.drive_advice(date(2026, 10, 14), day,
                                  base_weather, [])["status"] == "PLAN AHEAD"
    assert decision.drive_advice(day, day, base_weather, [],
                                 gas_price=None)["fuel_cost"] is None


def test_offer_check_does_not_invent_rates_and_counts_all_miles():
    assert decision.offer_check()["status"] == "NO OFFER PROVIDED"
    assert decision.offer_check(25, 12, 60, None)["status"] == "NO OFFER PROVIDED"
    assert decision.offer_check(25, 12, 60, 0)["status"] == "GAS PRICE UNKNOWN"
    fits = decision.offer_check(40, 16, 60, 4, 30)
    assert fits["fuel"] == 4
    assert fits["after_fuel_hour"] == 36
    assert fits["status"] == "MEETS YOUR TARGET"
    weak = decision.offer_check(15, 16, 60, 4, 20)
    assert weak["status"] == "BELOW YOUR TARGET"
    with pytest.raises(ValueError):
        decision.offer_check(15, 10, 0, 4)


def test_decision_schema_preserves_actual_time_context_and_formula_safety():
    slot = {"title": "Pregame drive", "time": "12:30 PM – 2:45 PM",
            "app": "Delivery", "start_minute": 750}
    row = decision.new_decision(date(2026, 10, 17), slot, "Drive",
                                "=HYPERLINK(1)", {"events": "Michigan Stadium",
                                                  "weather": "Clear"},
                                one_way_miles=5, gas_price=4)
    assert row["window_time"] == "12:30 PM – 2:45 PM"
    assert row["choice"] == "Drive"
    assert row["positioning_fuel_cost"] == 2.5
    assert row["reason"].startswith("'=")
    assert row["result"] == "Unreviewed"
    assert row["event_context"] == "Michigan Stadium"
    with pytest.raises(ValueError):
        decision.new_decision(date(2026, 10, 17), slot, "Surge Guaranteed", "", {})
    with pytest.raises(ValueError):
        decision.new_decision(date(2026, 10, 17), {**slot, "start_minute": None},
                              "Drive", "", {})


def test_outcome_summary_is_self_reported_and_bounded():
    rows = [{"result": "Worth it"}, {"result": "Not worth it"},
            {"result": "Mixed"}, {"result": "Unreviewed"}]
    result = decision.decision_summary(rows)
    assert result["reviewed"] == 3
    assert result["worthwhile"] == 1
    assert result["sufficient"]
    assert "self-reported" in result["message"].lower()
    assert not decision.decision_summary(rows[:2])["sufficient"]


def test_decision_sheet_creates_separate_tab_and_raw_values(monkeypatch):
    class MissingSheet(Exception):
        pass
    class FakeWorksheet:
        def __init__(self):
            self.headers = []
            self.rows = []
            self.updated = []
        def row_values(self, index):
            return self.headers
        def append_row(self, row, value_input_option=None):
            assert value_input_option == "RAW"
            if not self.headers:
                self.headers = row
            else:
                self.rows.append(row)
        def get_all_records(self):
            return [dict(zip(decision.DECISION_COLUMNS, row)) for row in self.rows]
        def col_values(self, column):
            return ["id"] + [row[0] for row in self.rows]
        def update(self, range_name=None, values=None, raw=None):
            assert raw is True
            self.updated.append((range_name, values))
    class FakeBook:
        def __init__(self):
            self.ws = None
        def worksheet(self, name):
            assert name == "Decisions"
            if self.ws is None:
                raise MissingSheet()
            return self.ws
        def add_worksheet(self, name, rows, cols):
            assert name == "Decisions" and cols == len(decision.DECISION_COLUMNS)
            self.ws = FakeWorksheet()
            return self.ws
    book = FakeBook()
    monkeypatch.setattr(sys.modules["pilot_features"], "open_sheet",
                        lambda credentials, sheet_id: book)
    monkeypatch.setitem(sys.modules, "gspread",
                        SimpleNamespace(WorksheetNotFound=MissingSheet))
    row = decision.new_decision(date(2026, 10, 17),
                                {"time": "5:00 PM – 8:00 PM", "title": "Drive",
                                 "app": "Delivery", "start_minute": 1020},
                                "Drive", "Test", {})
    decision.decision_sheet_append({}, "private", row)
    assert book.ws.headers == list(decision.DECISION_COLUMNS)
    assert len(decision.decision_sheet_load({}, "private")) == 1
    decision.decision_sheet_review({}, "private", row["id"],
                                   "Worth it", "=danger")
    assert book.ws.updated == [("L2:M2", [["Worth it", "'=danger"]])]
    with pytest.raises(ValueError):
        decision.decision_sheet_review({}, "private", "nonexistent", "Mixed", "bad")


def test_private_decision_records_gate_fails_closed():
    from unittest.mock import patch
    at = AppTest.from_string("import app\nimport streamlit as st\n"
                             "st.write(app.load_private_decisions())",
                             default_timeout=20).run()
    assert not at.exception
    assert "[]" in [x.value for x in at.markdown]
    assert not any("user" in x.value for x in at.markdown)


def test_default_dashboard_keeps_graphite_and_protects_journal():
    from unittest.mock import patch
    today = date(2026, 10, 13)
    weather = {"hazards": [], "places": [{"place": "Ann Arbor",
               "status": "Available", "hazards": [], "periods": []}],
               "checked": datetime(2026,10,13,tzinfo=timezone.utc)}
    events = {"events": [], "notes": [], "coverage": [], "checked":
              datetime(2026,10,13,tzinfo=timezone.utc)}
    with patch.object(app, "fetch_live_events", return_value=events), \
         patch.object(app, "fetch_weather", return_value=weather):
        at = AppTest.from_string("from app import main\nmain()",
                                 default_timeout=25).run()
        assert not at.exception
        assert at.session_state["pilot_workspace"] == "Drive"
        assert any("Estimated positioning miles" in n.label for n in at.number_input)
        assert "Add planned shifts to calendar" in [x.label for x in at.expander]
        assert any("Private decision journal" in x.label for x in at.expander)
        assert not at.dataframe
