"""Reliable trigger: external scheduler mode, once-a-day and too-late guards, event ranking."""
from datetime import date, datetime

from brief import daybrief, events as ev, gate
from brief.config import TZ
from brief.push import compose_push, short_name, short_venue
from brief.render import live_pushed_on, render_page

from test_brief_daybrief import NOW, all_days, events_data, forecast


def ny(*args):
    return datetime(*args, tzinfo=TZ)


def brief_at(now, found=()):
    return daybrief.build_brief(now, events_data(found), forecast(all_days()))


# ---------------------------------------------------------------- the gate
def test_external_scheduler_mode_is_a_guarded_push_on_driving_days_only():
    assert gate.decide("workflow_dispatch", "", "scheduled-push", now=ny(2026, 11, 6, 15, 15))[0] == "push"
    assert gate.decide("workflow_dispatch", "", "scheduled-push", now=ny(2026, 11, 9, 15, 15))[0] == "skip"
    assert gate.is_guarded("workflow_dispatch", "scheduled-push")
    assert gate.is_guarded("schedule", "")
    assert not gate.is_guarded("workflow_dispatch", "push")  # your own manual push always sends


def test_gate_outputs_guard(tmp_path):
    out = tmp_path / "o"
    env = {"EVENT_NAME": "workflow_dispatch", "INPUT_MODE": "scheduled-push", "GITHUB_OUTPUT": str(out)}
    gate.main(env, now=ny(2026, 11, 6, 15, 15))  # a Friday; don't depend on today's date
    assert out.read_text().splitlines() == ["mode=push", "guard=true", "slot=afternoon"]
    out.write_text("")
    gate.main(env, now=ny(2026, 11, 9, 15, 15))  # a Monday: external trigger skips, no guard
    assert out.read_text().splitlines() == ["mode=skip", "guard=false", "slot=afternoon"]


# ---------------------------------------------------------------- the guards
def test_cutoff_is_first_evening_window_but_not_before_4pm():
    thu = brief_at(NOW)["days"][0]
    assert daybrief.push_cutoff_minute(thu) == 18 * 60  # Thu dinner window 6 PM
    fri = brief_at(ny(2026, 11, 6, 15, 15))["days"][0]
    assert daybrief.push_cutoff_minute(fri) == 17 * 60  # Fri 5 PM
    noon_game = ev.event_record("Michigan Football vs. Rival", "Michigan Stadium",
                                "2026-11-07T12:00:00-05:00", None, "Michigan Athletics")
    sat = brief_at(ny(2026, 11, 7, 15, 15), [noon_game])["days"][0]
    assert daybrief.push_cutoff_minute(sat) == 16 * 60  # return at 3 PM, floored at 4 PM


def test_should_send_once_per_day_and_not_late():
    from brief.__main__ import should_send
    on_time = brief_at(ny(2026, 11, 5, 15, 20))
    assert should_send(on_time, ny(2026, 11, 5, 15, 20), "")[0] is True
    assert should_send(on_time, ny(2026, 11, 5, 15, 20), "2026-11-04")[0] is True
    sent, why = should_send(on_time, ny(2026, 11, 5, 15, 20), "2026-11-05")
    assert sent is False and "already sent" in why
    late = brief_at(ny(2026, 11, 5, 19, 58))  # yesterday's 4h40m-late GitHub run
    sent, why = should_send(late, ny(2026, 11, 5, 19, 58), "")
    assert sent is False and "too late" in why and "6:00 PM" in why


def test_page_records_push_date():
    html = render_page(brief_at(NOW), "2026-11-05")
    assert live_pushed_on(html) == "2026-11-05"
    assert live_pushed_on(render_page(brief_at(NOW))) == ""
    assert live_pushed_on("<html>old page</html>") == ""


def run_page(tmp_path, monkeypatch, now, live_html, args):
    import brief.__main__ as entry
    monkeypatch.setattr(entry, "fetch_events", lambda days, extra: events_data())
    monkeypatch.setattr(entry, "fetch_forecast", lambda: forecast(all_days()))
    monkeypatch.setattr(entry, "fetch_live_page", lambda url: live_html)
    out = tmp_path / "gh"
    out.write_text("")
    entry.main(["--mode", "page", "--now", now, "--out", str(tmp_path / "site"),
                "--push-file", str(tmp_path / "push.json"), *args],
               env={"PAGE_URL": "https://x.github.io/", "GITHUB_OUTPUT": str(out)})
    values = dict(line.split("=", 1) for line in out.read_text().splitlines())
    return values, live_pushed_on((tmp_path / "site" / "index.html").read_text())


def test_guarded_push_after_manual_push_is_skipped_but_page_still_deploys(tmp_path, monkeypatch):
    live = render_page(brief_at(NOW), "2026-11-05")  # you already pushed today
    values, recorded = run_page(tmp_path, monkeypatch, "2026-11-05T15:20", live, ["--push-run", "--guard"])
    assert values == {"publish": "true", "send": "false", "already_sent": "true"}
    assert recorded == "2026-11-05"


def test_guarded_push_on_time_sends_and_records_today(tmp_path, monkeypatch):
    live = render_page(brief_at(NOW), "2026-11-02")
    values, recorded = run_page(tmp_path, monkeypatch, "2026-11-05T15:16", live, ["--push-run", "--guard"])
    assert values["send"] == "true" and recorded == "2026-11-05"


def test_manual_push_ignores_guards(tmp_path, monkeypatch):
    live = render_page(brief_at(NOW), "2026-11-05")
    values, _ = run_page(tmp_path, monkeypatch, "2026-11-05T19:58", live, ["--push-run"])
    assert values["send"] == "true"


def test_page_only_build_keeps_the_last_push_date(tmp_path, monkeypatch):
    live = render_page(brief_at(NOW), "2026-11-05")
    values, recorded = run_page(tmp_path, monkeypatch, "2026-11-06T05:40", live, [])
    assert values["send"] == "false" and recorded == "2026-11-05"


# ---------------------------------------------------- event ranking polish
def test_college_ranks_ahead_of_high_school_in_same_tier_and_names_are_short():
    pioneer = ev.event_record("Pioneer • Hockey (Girls V) • Academy of the Sacred Heart (H)",
                              "Pioneer High School (Yost Ice Arena)", "2026-11-05T18:00:00-05:00",
                              None, "Pioneer Athletics")
    uconn = ev.event_record("Michigan Womens Basketball vs. #2 UConn", "Crisler Center",
                            "2026-11-05T18:30:00-05:00", None, "Michigan Athletics")
    assert pioneer["tier"] == uconn["tier"] == "INDOOR ARENA"
    line = compose_push(brief_at(NOW, [pioneer, uconn])["days"][0])["message"].split("\n")[2]
    assert line == ("Events: Michigan Womens Basketball vs. #2 UConn 6:30 PM (Crisler); "
                    "Pioneer girls hockey vs Academy of the Sacred Heart 6:00 PM (Yost)")


def test_short_name_leaves_other_names_alone():
    assert short_name("Michigan Football vs. Michigan State") == "Michigan Football vs. Michigan State"
    assert short_name("Pioneer • Swimming & Diving (Girls V) • SEC Championship @ Skyline (A)") == \
        "Pioneer girls swimming & diving vs SEC Championship @ Skyline"
    assert short_venue("Rackham Auditorium, Ann Arbor") == "Rackham Auditorium"


# ---------------------------------------------------- Saturday 8 AM game-day push
UCLA = ev.event_record("Michigan Football vs. UCLA", "Michigan Stadium", "2026-11-21T15:30:00-05:00", None, "M")
NOON = ev.event_record("Michigan Football vs. Rival", "Michigan Stadium", "2026-11-21T12:00:00-05:00", None, "M")
UCLA_TBD = ev.event_record("Michigan Football vs. UCLA", "Michigan Stadium", "2026-11-21", None, "M", all_day=True)


def test_gameday_mode_is_a_guarded_morning_push_on_saturdays_only(tmp_path):
    assert gate.decide("workflow_dispatch", "", "gameday-push", now=ny(2026, 11, 21, 8, 0))[0] == "push"
    assert gate.decide("workflow_dispatch", "", "gameday-push", now=ny(2026, 11, 22, 8, 0))[0] == "skip"
    assert gate.is_guarded("workflow_dispatch", "gameday-push")
    out = tmp_path / "o"
    gate.main({"EVENT_NAME": "workflow_dispatch", "INPUT_MODE": "gameday-push", "GITHUB_OUTPUT": str(out)},
              now=ny(2026, 11, 21, 8, 0))
    assert out.read_text().splitlines() == ["mode=push", "guard=true", "slot=morning"]


def test_morning_push_needs_a_published_home_kickoff():
    from brief.__main__ import should_send
    at8 = ny(2026, 11, 21, 8, 0)
    assert should_send(brief_at(at8, [UCLA]), at8, "", "morning") == (True, "on time")
    assert should_send(brief_at(at8, [UCLA_TBD]), at8, "", "morning") == (
        False, "no published Michigan home kickoff today")  # TBD stays TBD: no plan to push
    assert should_send(brief_at(at8), at8, "", "morning")[0] is False


def test_morning_push_is_too_late_once_pregame_starts():
    from brief.__main__ import should_send
    late = ny(2026, 11, 21, 9, 5)
    sent, why = should_send(brief_at(late, [NOON]), late, "", "morning")  # noon game: drive from 9:00 AM
    assert sent is False and why == "too late: it's past 9:00 AM, when driving starts"
    assert should_send(brief_at(late, [UCLA]), late, "", "morning")[0] is True  # 3:30 game: drive from 12:30


def test_morning_and_afternoon_markers_are_independent(tmp_path, monkeypatch):
    import brief.__main__ as entry
    monkeypatch.setattr(entry, "fetch_events", lambda days, extra: events_data([UCLA]))
    monkeypatch.setattr(entry, "fetch_forecast", lambda: forecast(all_days()))
    sat = brief_at(ny(2026, 11, 21, 8, 0), [UCLA])
    pages = {"live": render_page(sat, "2026-11-20", "")}
    monkeypatch.setattr(entry, "fetch_live_page", lambda url: pages["live"])

    def run(now, slot):
        out = tmp_path / "gh"
        out.write_text("")
        site = tmp_path / slot
        entry.main(["--mode", "page", "--now", now, "--out", str(site), "--push-file", str(tmp_path / "p.json"),
                    "--push-run", "--guard", "--slot", slot],
                   env={"PAGE_URL": "https://x.github.io/", "GITHUB_OUTPUT": str(out)})
        pages["live"] = (site / "index.html").read_text()
        return dict(line.split("=", 1) for line in out.read_text().splitlines())

    assert run("2026-11-21T08:00", "morning")["send"] == "true"
    assert live_pushed_on(pages["live"], "morning") == "2026-11-21"
    assert live_pushed_on(pages["live"]) == "2026-11-20"           # afternoon marker untouched
    assert run("2026-11-21T08:05", "morning")["send"] == "false"   # once per morning
    assert run("2026-11-21T15:15", "afternoon")["send"] == "true"  # the 3:15 brief still goes out
    assert live_pushed_on(pages["live"], "morning") == "2026-11-21"  # and keeps the morning marker


def test_workflow_wires_the_gameday_mode_and_its_own_heartbeat():
    import pathlib
    import yaml
    wf = yaml.safe_load(pathlib.Path(".github/workflows/brief.yml").read_text())
    on = wf.get("on") or wf[True]
    assert "gameday-push" in on["workflow_dispatch"]["inputs"]["mode"]["options"]
    assert "HC_PING_URL_GAMEDAY" in str(wf["jobs"]["heartbeat-gameday"])
    assert "slot != 'morning'" in wf["jobs"]["heartbeat"]["if"]
    assert "gameday-push" in wf["jobs"]["alert"]["if"]
