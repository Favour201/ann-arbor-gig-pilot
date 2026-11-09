"""Driving-window plan (ported from tests/test_app.py) with the public 'home' label."""
import json
from datetime import date

from brief import events as ev
from brief.config import BASE_LABEL, OFFER_TARGET_DOLLARS_PER_MILE
from brief.plan import (event_time_label, generate_schedule, is_confirmed_michigan_home_game,
                        offer_target_guidance, roadmap_title)


def driving(slots):
    return [s for s in slots if s["app"] in ("Delivery", "XL / Comfort")]


def test_thursday_keeps_football_and_minutes():
    game = ev.event_record("Michigan Football", "Michigan Stadium", "2026-10-15T15:30:00-04:00", None, "Test")
    slots = generate_schedule([game], BASE_LABEL, 3)
    assert any(s["time"] == "12:30 PM – 3:30 PM" for s in slots)
    assert any(s["app"] == "Delivery" and "Optional continuation" in s.get("title", "") for s in slots)


def test_roadmap_chronology_early_events_before_evening_shift():
    stadium = ev.event_record("Michigan Football", "Michigan Stadium", "2026-10-15T15:30:00-04:00", None, "F")
    theater = ev.event_record("Show", "Michigan Theater", "2026-10-15T20:00:00-04:00", None, "F")
    slots = generate_schedule([theater, stadium], BASE_LABEL, 3)
    assert [s["time"] for s in slots] == [
        "12:30 PM – 3:30 PM", "6:00 PM – 8:30 PM", "7:00 PM – 8:00 PM", "8:30 PM onward"]


def test_unknown_kickoff_timing_is_separate_and_last():
    mystery = ev.event_record("Unconfirmed kickoff", "Michigan Stadium", "2026-10-15", None, "F", all_day=True)
    slots = generate_schedule([mystery], BASE_LABEL, 3)
    assert slots[-1]["time"] == "Kickoff TBD • timing unconfirmed"
    assert slots[0]["time"] == "6:00 PM – 8:30 PM"


def test_michigan_kickoff_tbd_then_official_time_updates():
    table = [None, {"date": 2, "opponent": 3, "location_indicator": 4, "location": 5, "time": 6, "tbd": 7},
             "2026-11-21", {"title": 8}, "H", "Ann Arbor, Mich.", "TBA", True, "Ohio State"]
    html = lambda: '<script id="__NUXT_DATA__">' + json.dumps(table) + "</script>"
    day = (date(2026, 11, 21),)
    unknown = ev.parse_sidearm(html(), day, "Michigan", "football", "")
    assert unknown[0]["all_day"] is True and event_time_label(unknown[0]) == "Kickoff TBD"
    assert generate_schedule(unknown, BASE_LABEL, 5)[-1]["time"].startswith("Kickoff TBD")
    table[6], table[7] = "3:30 PM ET", False
    announced = ev.parse_sidearm(html(), day, "Michigan", "football", "")
    assert event_time_label(announced[0]) == "3:30 PM"
    times = [s["time"] for s in generate_schedule(announced, BASE_LABEL, 5)]
    assert "12:30 PM – 2:45 PM" in times and "5:30 PM onward" in times


def test_saturday_330_game_recommends_drive_rest_and_return():
    game = ev.event_record("Michigan Football vs. Penn State", "Michigan Stadium",
                           "2026-10-17T15:30:00-04:00", None, "U-M Athletics")
    slots = generate_schedule([game], BASE_LABEL, 5)
    assert [x["time"] for x in slots] == ["12:30 PM – 2:45 PM", "2:45 PM – 5:30 PM", "5:30 PM onward"]
    assert [roadmap_title(x) for x in slots] == [
        "Pregame · best offers", "Head home & recharge", "Optional return · delivery first"]
    assert "rides first" in slots[0]["action"] and "UberX on only then" in slots[0]["action"]
    assert [x["app"] for x in slots] == ["Delivery", "Break / Reset", "Delivery"]
    assert "$3 per estimated total mile" in slots[2]["action"]
    assert "no surge is guaranteed" in slots[2]["action"].lower()
    assert "toward home" in slots[1]["action"]


def test_friday_and_saturday_after_dinner_are_optional_delivery():
    for weekday in (4, 5):
        slots = driving(generate_schedule([], BASE_LABEL, weekday))
        assert [x["time"] for x in slots] == ["5:00 PM – 8:00 PM", "8:00 PM onward"]
        assert roadmap_title(slots[1]) == "Optional continuation · delivery first"
        assert "not a required shift" in slots[1]["action"]
        assert slots[1]["end_minute"] == 1440


def test_thursday_and_sunday_baselines():
    thu = driving(generate_schedule([], BASE_LABEL, 3))
    assert [x["time"] for x in thu] == ["6:00 PM – 8:30 PM", "8:30 PM onward"]
    sun = driving(generate_schedule([], BASE_LABEL, 6))
    assert [x["time"] for x in sun] == ["4:30 PM – 9:30 PM"]


def test_offer_target_is_guidance_not_predicted_demand():
    assert OFFER_TARGET_DOLLARS_PER_MILE == 3.0
    rule = offer_target_guidance()
    assert "Personal goal" in rule and "pickup and likely unpaid miles" in rule


def test_game_day_preference_can_adjust_stop_time_and_return():
    game = ev.event_record("Michigan Football vs. Penn State", "Michigan Stadium",
                           "2026-10-17T15:30:00-04:00", None, "U-M Athletics")
    slots = generate_schedule([game], BASE_LABEL, 5, stop_buffer_minutes=60, return_after_hours=2.5)
    assert [s["time"] for s in slots] == ["12:30 PM – 2:30 PM", "2:30 PM – 6:00 PM", "6:00 PM onward"]


def test_tbd_or_away_game_does_not_create_split_shift():
    tbd = ev.event_record("Michigan Football vs. Penn State", "Michigan Stadium", "2026-10-17", None, "U", all_day=True)
    assert not any(x["app"] == "Break / Reset" for x in generate_schedule([tbd], BASE_LABEL, 5))
    away = ev.event_record("Michigan Football vs. Penn State", "Penn State Stadium",
                           "2026-10-17T15:30:00-04:00", None, "F")
    assert not is_confirmed_michigan_home_game(away)
    assert not is_confirmed_michigan_home_game(tbd)


def test_early_kickoff_return_waits_until_afternoon():
    game = ev.event_record("Michigan Football vs. Rival", "Michigan Stadium", "2026-10-17T12:00:00-04:00", None, "U")
    assert [s["time"] for s in generate_schedule([game], BASE_LABEL, 5)] == [
        "9:00 AM – 11:15 AM", "11:15 AM – 3:00 PM", "3:00 PM onward"]


def test_no_street_address_in_any_public_plan_text():
    game = ev.event_record("Michigan Football", "Michigan Stadium", "2026-10-17T15:30:00-04:00", None, "U")
    texts = [s["action"] for wd in range(7) for s in generate_schedule([], BASE_LABEL, wd)]
    texts += [s["action"] for s in generate_schedule([game], BASE_LABEL, 5)]
    assert texts and all("Arbor Circle" not in t and "2100" not in t for t in texts)


def test_night_kickoff_uses_the_nov_7_shape_with_estimated_end():
    game = ev.event_record("Michigan Football vs. Michigan State", "Michigan Stadium",
                           "2026-11-07T19:30:00-05:00", None, "U-M Athletics")
    slots = generate_schedule([game], BASE_LABEL, 5)
    assert [(s["time"], s.get("plan_phase")) for s in slots] == [
        ("3:30 PM – 7:30 PM", "pregame"), ("7:30 PM – 9:30 PM", "ingame"),
        ("9:30 PM – 10:45 PM", "rest"), ("10:45 PM – 12:45 AM (+1 day)", "postgame")]
    assert [s["app"] for s in slots] == ["Delivery", "Delivery", "Break / Reset", "XL / Comfort"]
    assert slots[3]["end_minute"] == 24 * 60 + 45
    assert "estimate" in slots[2]["action"] and "11:00 PM" in slots[2]["action"]
    assert "End time is an estimate" in slots[0]["plan_basis"]
    assert "away from the stadium" in slots[1]["action"]
    for s in slots:  # honesty: no demand/surge/profit claims, only "no surge is guaranteed"
        text = s["action"].lower().replace("no surge is guaranteed", "")
        assert not any(word in text for word in ("surge", "profit", "guarantee"))


def test_noon_and_330_kickoffs_keep_the_day_split():
    noon = ev.event_record("Michigan Football", "Michigan Stadium", "2026-09-05T12:00:00-04:00", None, "T")
    assert [s["time"] for s in generate_schedule([noon], BASE_LABEL, 5)] == [
        "9:00 AM – 11:15 AM", "11:15 AM – 3:00 PM", "3:00 PM onward"]
    five = ev.event_record("Michigan Football", "Michigan Stadium", "2026-09-05T17:00:00-04:00", None, "T")
    assert generate_schedule([five], BASE_LABEL, 5)[0]["time"] == "1:00 PM – 5:00 PM"  # 5 PM counts as night
