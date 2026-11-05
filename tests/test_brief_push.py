"""Push composition (3–5 lines, honest) and the ntfy sender (topic never leaks)."""
from datetime import date

import pytest
import requests

from brief import daybrief, events as ev
from brief.notify import PushError, send_ntfy
from brief.push import MAX_LINES, compose_push

from test_brief_daybrief import NOW, all_days, events_data, forecast, hourly


def day_for(d, found=(), periods=None, ok=True):
    return daybrief.build_day(d, NOW, events_data(found, ok=ok), forecast(periods or all_days()))


def check_shape(push):
    lines = push["message"].split("\n")
    assert 3 <= len(lines) <= MAX_LINES
    for banned in ("surge", "guarantee", "profit", "Arbor Circle"):
        assert banned.lower() not in push["message"].lower()
    return lines


def test_thursday_push_has_plan_weather_events_and_offer_rule():
    game = ev.event_record("Michigan Womens Basketball vs. #2 UConn", "Crisler Center",
                           "2026-11-05T18:30:00-05:00", None, "Michigan Athletics")
    push = compose_push(day_for(NOW.date(), [game]))
    lines = check_shape(push)
    assert push["title"] == "Thu Nov 5 · Gig brief"
    assert lines[0] == "Delivery 6:00–8:30 PM, optional after"
    assert lines[1].startswith("Weather: ☀️ Low impact, Clear, 45°F")
    assert lines[2] == "Events: Michigan Womens Basketball vs. #2 UConn 6:30 PM (Crisler)"
    assert lines[3] == "Offer rule: $3+ per est. total mile"
    assert push["priority"] == 3 and push["click"] == ""


def test_game_day_line_shows_split_and_ranks_football_first():
    football = ev.event_record("Michigan Football vs. Michigan State", "Michigan Stadium",
                               "2026-11-07T19:30:00-05:00", None, "Michigan Athletics")
    school = ev.event_record("Pioneer • Hockey", "Pioneer High School", "2026-11-07T17:00:00-05:00", None, "P")
    lines = check_shape(compose_push(day_for(date(2026, 11, 7), [school, football])))
    assert lines[0] == "Game day: drive 4:30–6:45 PM · rest 6:45–9:30 PM · optional return 9:30 PM+"
    assert lines[2].startswith("Events: Michigan Football vs. Michigan State 7:30 PM (Michigan Stadium); Pioneer")


def test_red_weather_leads_with_skip_and_raises_priority():
    push = compose_push(day_for(NOW.date(), periods=all_days("Freezing Rain", 30)))
    lines = check_shape(push)
    assert lines[0].startswith("⚠ Skip tonight?")
    assert "HIGH IMPACT" in lines[2]
    assert push["priority"] == 4 and push["tags"] == ["warning"]


def test_unknown_weather_and_unverified_sources_are_said_out_loud():
    push = compose_push(day_for(NOW.date(), periods=hourly(date(2026, 11, 1)), ok=False))
    lines = check_shape(push)
    assert lines[1] == "Weather: unknown for driving hours. Check a forecast before leaving."
    assert lines[2] == "Events: none found (some sources unverified)"


def test_more_than_two_events_are_summarized():
    found = [ev.event_record(f"Show {i}", "Hill Auditorium", f"2026-11-05T1{i}:00:00-05:00", None, "U")
             for i in range(4)]
    assert "+2 more" in compose_push(day_for(NOW.date(), found))["message"]


def test_non_driving_day_push_is_short():
    push = compose_push(day_for(date(2026, 11, 9)))
    assert push["message"].startswith("Not a driving day.")
    assert "Offer rule" not in push["message"]


def test_page_link_is_passed_through():
    push = compose_push(day_for(NOW.date()), "https://favour201.github.io/ann-arbor-gig-pilot/")
    assert push["click"].endswith("/ann-arbor-gig-pilot/")


# ------------------------------------------------------------- ntfy sender
class FakeResponse:
    def __init__(self, status):
        self.status_code = status


def test_send_posts_json_to_server_root_with_topic_in_body():
    calls = []
    def post(url, json, timeout):
        calls.append((url, json))
        return FakeResponse(200)
    push = compose_push(day_for(NOW.date()), "https://example.github.io/")
    assert send_ntfy(push, " gp-secret ", post=post) == 200
    url, body = calls[0]
    assert url == "https://ntfy.sh" and "gp-secret" not in url
    assert body["topic"] == "gp-secret" and body["click"] == "https://example.github.io/"
    assert body["message"] == push["message"]


@pytest.mark.parametrize("topic", ["", "   ", "a/b", "has space"])
def test_missing_or_malformed_topic_fails_clearly(topic):
    with pytest.raises(PushError, match="NTFY_TOPIC"):
        send_ntfy(compose_push(day_for(NOW.date())), topic, post=lambda *a, **k: FakeResponse(200))


def test_errors_never_include_the_topic():
    def boom(url, json, timeout):
        raise requests.ConnectionError(f"failed for {json['topic']}")
    with pytest.raises(PushError) as err:
        send_ntfy(compose_push(day_for(NOW.date())), "gp-secret", post=boom)
    assert "gp-secret" not in str(err.value)
    with pytest.raises(PushError, match="HTTP 429"):
        send_ntfy(compose_push(day_for(NOW.date())), "gp-secret",
                  post=lambda *a, **k: FakeResponse(429))


def test_main_push_mode_reports_missing_topic(monkeypatch, capsys):
    import brief.__main__ as entry
    monkeypatch.setattr(entry, "fetch_events", lambda days, extra: events_data())
    monkeypatch.setattr(entry, "fetch_forecast", lambda: forecast(all_days()))
    assert entry.main(["--mode", "dry-run", "--now", "2026-11-05T15:15"], env={}) == 0
    assert "--- push: Thu Nov 5 · Gig brief" in capsys.readouterr().out
    assert entry.main(["--mode", "push", "--now", "2026-11-05T15:15"], env={}) == 1
    assert "NTFY_TOPIC is missing" in capsys.readouterr().err
