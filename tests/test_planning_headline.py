"""Drive headline timing and evidence; synthetic schedules, no live services."""
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

import app


def game():
    return app.event_record("Michigan Football", "Michigan Stadium",
                            "2026-10-17T15:30:00-04:00", None, "Fixture")


@pytest.mark.parametrize("hour,minute,title,phase", [
    (12, 0, "Pregame window starts at 12:30 PM", "pregame"),
    (12, 30, "Pregame window is open", "pregame"),
    (14, 44, "Pregame window is open", "pregame"),
    (14, 45, "Rest now", "rest"),
    (17, 29, "Rest now", "rest"),
    (17, 30, "Optional return", "return"),
])
def test_game_headline_changes_at_exact_phase_boundaries(hour, minute, title, phase):
    slots = app.generate_schedule([game()], app.BASE, 5)
    original = deepcopy(slots)
    result = app.planning_headline(slots, date(2026, 10, 17),
                                   datetime(2026, 10, 17, hour, minute, tzinfo=app.TZ))
    assert result["title"] == title
    assert result["slot"]["plan_phase"] == phase
    assert "confirmed Michigan kickoff (3:30 PM)" in result["basis"]
    assert "not measured demand" in result["basis"]
    assert slots == original


@pytest.mark.parametrize("weekday,day,hour,minute,title", [
    (0, 19, 16, 0, "Dinner window starts at 5:00 PM"),
    (0, 19, 17, 0, "Dinner window is open"),
    (0, 19, 20, 0, "No more planned windows today"),
    (3, 15, 20, 30, "Optional continuation"),
    (4, 16, 20, 0, "Optional continuation"),
    (6, 18, 16, 30, "Dinner window is open"),
])
def test_usual_dinner_and_optional_continuation(weekday, day, hour, minute, title):
    slots = app.generate_schedule([], app.BASE, weekday)
    result = app.planning_headline(slots, date(2026, 10, day),
                                   datetime(2026, 10, day, hour, minute, tzinfo=app.TZ))
    assert result["title"] == title
    if result["slot"]:
        assert result["basis"].startswith("Usual dinner plan.")
    if title == "Optional continuation":
        assert "heading home is fine" in result["note"]


def test_past_future_and_eastern_date_do_not_claim_an_active_window():
    slots = app.generate_schedule([game()], app.BASE, 5)
    future = app.planning_headline(slots, date(2026, 10, 17),
                                   datetime(2026, 10, 16, 18, tzinfo=app.TZ))
    assert future["title"] == "Pregame window starts at 12:30 PM"
    past = app.planning_headline(slots, date(2026, 10, 17),
                                 datetime(2026, 10, 18, 0, tzinfo=app.TZ))
    assert past["title"] == "Past plan · review only" and past["slot"] is None
    # UTC is already Sunday; it is still Saturday evening in Michigan.
    eastern = app.planning_headline(slots, date(2026, 10, 17),
                                    datetime(2026, 10, 18, 1, tzinfo=timezone.utc))
    assert eastern["title"] == "Optional return"


def test_tbd_and_disabled_split_do_not_claim_kickoff_adjusted_driving():
    tbd = app.event_record("Michigan Football", "Michigan Stadium",
                           "2026-10-17", None, "Fixture", all_day=True)
    for slots in (app.generate_schedule([tbd], app.BASE, 5),
                  app.generate_schedule([game()], app.BASE, 5, smart_game_day=False)):
        result = app.planning_headline(slots, date(2026, 10, 17),
                                       datetime(2026, 10, 17, 12, tzinfo=app.TZ))
        assert result["title"] == "Dinner window starts at 5:00 PM"
        assert result["basis"].startswith("Usual dinner plan.")


def test_adjusted_preferences_control_headline_and_remaining_windows():
    slots = app.generate_schedule([game()], app.BASE, 5,
                                  stop_buffer_minutes=60, return_after_hours=2.5)
    moment = datetime(2026, 10, 17, 14, 30, tzinfo=app.TZ)
    assert app.planning_headline(slots, moment.date(), moment)["title"] == "Rest now"
    remaining = app.filter_roadmap(slots, selected_date=moment.date(),
                                   now=moment, remaining=True)
    assert [slot["plan_phase"] for slot in remaining] == ["rest", "return"]
    assert app.planning_headline(slots, moment.date(), moment.replace(hour=18))["title"] == "Optional return"


def test_empty_and_untimed_plans_never_invent_a_clock():
    moment = datetime(2026, 10, 14, 12, tzinfo=app.TZ)
    untimed = [{"app": "Delivery", "start_minute": None, "end_minute": None}]
    for slots in ([], untimed):
        result = app.planning_headline(slots, date(2026, 10, 15), moment)
        assert result["title"] == "No timed windows planned"
        assert result["slot"] is None


def test_drive_renders_rest_and_its_basis_in_existing_card():
    at = AppTest.from_string(
        "import app\nfrom datetime import datetime\n"
        "now = datetime(2026, 10, 17, 15, tzinfo=app.TZ)\n"
        "game = app.event_record('Michigan Football', 'Michigan Stadium', "
        "'2026-10-17T15:30:00-04:00', None, 'Fixture')\n"
        "app.render_drive_workspace(now.date(), now, "
        "{'events': [game], 'coverage': []}, {'places': [], 'hazards': []}, app.BASE)",
        default_timeout=25).run()
    assert not at.exception
    assert any(item.value == "**Rest now**" for item in at.markdown)
    assert any("Adjusted for confirmed Michigan kickoff (3:30 PM)" in item.value
               for item in at.caption)
    assert not any("NEXT PLANNED OPPORTUNITY" in item.value for item in at.caption)
    assert not at.number_input
    assert any("Add planned shifts to calendar" in item.label for item in at.expander)
