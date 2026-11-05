"""Scheduling: DST gating, cron/YAML agreement, refresh change detection."""
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from brief import gate
from brief.config import TZ
from brief.render import content_key, live_content_key, render_page

from test_brief_daybrief import NOW, all_days, events_data, forecast
from brief import daybrief

EDT, EST = "15 19 * * 0,4,5,6", "15 20 * * 0,4,5,6"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "brief.yml"


def ny(*args):
    return datetime(*args, tzinfo=TZ)


def test_workflow_crons_match_gate_constants():
    doc = yaml.safe_load(WORKFLOW.read_text())
    on = doc.get("on", doc.get(True))  # PyYAML reads the key `on` as True
    crons = {item["cron"] for item in on["schedule"]}
    assert crons == set(gate.PUSH_CRONS) | {gate.REFRESH_CRON}


@pytest.mark.parametrize("when, schedule, expected", [
    # Thu Nov 5 2026 is EST (DST ended Sun Nov 1): only the 20:15 UTC line pushes.
    (ny(2026, 11, 5, 15, 15), EST, "push"),
    (ny(2026, 11, 5, 14, 15), EDT, "skip"),
    # Sun Nov 1 2026, the changeover day: already EST by the afternoon.
    (ny(2026, 11, 1, 15, 15), EST, "push"),
    (ny(2026, 11, 1, 14, 15), EDT, "skip"),
    # Thu Oct 29 2026 was still EDT.
    (ny(2026, 10, 29, 15, 15), EDT, "push"),
    (ny(2026, 10, 29, 16, 15), EST, "skip"),
    # Sun Mar 14 2027: DST starts at 2 AM, so the afternoon is EDT.
    (ny(2027, 3, 14, 15, 15), EDT, "push"),
    (ny(2027, 3, 14, 16, 15), EST, "skip"),
])
def test_dst_gate_runs_exactly_one_slot(when, schedule, expected):
    assert gate.decide("schedule", schedule, now=when)[0] == expected


def test_late_run_still_goes_by_the_cron_line_not_the_clock():
    # The EST slot fired but GitHub started it 50 minutes late: still the push.
    assert gate.decide("schedule", EST, now=ny(2026, 11, 5, 16, 5))[0] == "push"


def test_only_driving_days_push():
    # Cron is Thu-Sun UTC; at 20:15 UTC that is the same New York day, but guard anyway.
    assert gate.decide("schedule", EST, now=ny(2026, 11, 9, 15, 15))[0] == "skip"  # Monday


def test_refresh_and_manual_modes():
    assert gate.decide("schedule", gate.REFRESH_CRON, now=NOW)[0] == "refresh"
    assert gate.decide("workflow_dispatch", "", "push")[0] == "push"
    assert gate.decide("workflow_dispatch", "", "")[0] == "dry-run"
    assert gate.decide("workflow_dispatch", "", "rm -rf")[0] == "skip"
    assert gate.decide("push", "")[0] == "skip"
    assert gate.decide("schedule", "0 0 * * *", now=NOW)[0] == "skip"


def test_gate_writes_github_output(tmp_path, capsys):
    out = tmp_path / "out"
    gate.main({"EVENT_NAME": "workflow_dispatch", "INPUT_MODE": "page", "GITHUB_OUTPUT": str(out)})
    assert out.read_text() == "mode=page\n"
    assert "mode=page (manual run)" in capsys.readouterr().out


# ------------------------------------------------------- refresh: changed?
def build(found=(), now=NOW, periods=None):
    return daybrief.build_brief(now, events_data(found), forecast(periods or all_days()))


def test_content_key_ignores_generation_and_check_times():
    a, b = build(), build(now=NOW.replace(minute=40))
    assert content_key(a) == content_key(b)
    assert live_content_key(render_page(a)) == content_key(a)


def test_content_key_changes_with_what_the_page_says():
    base = content_key(build())
    assert content_key(build(periods=all_days("Freezing Rain", 30))) != base
    assert content_key(build(now=NOW.replace(day=6))) != base  # a new day moves the window


def test_refresh_skips_deploy_only_when_live_page_matches(tmp_path, monkeypatch):
    import brief.__main__ as entry
    monkeypatch.setattr(entry, "fetch_events", lambda days, extra: events_data())
    monkeypatch.setattr(entry, "fetch_forecast", lambda: forecast(all_days()))
    live = render_page(build())

    def run(fetch_result):
        out = tmp_path / "gh_output"
        out.write_text("")
        monkeypatch.setattr(entry, "get", fetch_result)
        entry.main(["--mode", "refresh", "--now", "2026-11-05T15:15", "--out", str(tmp_path / "site"),
                    "--push-file", str(tmp_path / "push.json")],
                   env={"PAGE_URL": "https://example.github.io/", "GITHUB_OUTPUT": str(out)})
        return out.read_text()

    page = lambda html: (lambda url, **_: type("R", (), {"text": html})())
    assert run(page(live)) == "publish=false\n"
    assert run(page(live.replace('name="brief-content" content="', 'name="brief-content" content="0', 1))) == "publish=true\n"
    def down(url, **_):
        raise OSError("unreachable")
    assert run(down) == "publish=true\n"  # can't tell -> deploy


def test_page_mode_always_publishes(tmp_path, monkeypatch):
    import brief.__main__ as entry
    monkeypatch.setattr(entry, "fetch_events", lambda days, extra: events_data())
    monkeypatch.setattr(entry, "fetch_forecast", lambda: forecast(all_days()))
    out = tmp_path / "gh_output"
    entry.main(["--mode", "page", "--now", "2026-11-05T15:15", "--out", str(tmp_path / "site"),
                "--push-file", str(tmp_path / "push.json")], env={"GITHUB_OUTPUT": str(out)})
    assert out.read_text() == "publish=true\n"
