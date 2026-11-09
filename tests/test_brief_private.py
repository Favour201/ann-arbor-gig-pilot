"""Private schedule (on-call, blockers): parsing, honesty, push merge, and no leaks to the log."""
from datetime import date
import json

from brief import private
from brief.__main__ import main, send
from brief.push import MAX_LINES, add_private_lines, compose_push

from test_brief_push import day_for
from test_brief_daybrief import NOW

KEY = json.dumps({"type": "service_account", "client_email": "x@y.iam.gserviceaccount.com"})
ENV = {"GOOGLE_SA_KEY": KEY, "PRIVATE_SHEET_ID": "sheet123"}
ONCALL = {"start_date": "2026-11-16", "end_date": "2026-11-23", "kind": "oncall", "note": ""}


def loaded(*rows):
    return private.load(ENV, fetch=lambda key, sheet: list(rows))


# ---------------------------------------------------------------- parsing
def test_dates_in_iso_us_and_sheets_serial_form():
    assert private.parse_date("2026-11-16") == date(2026, 11, 16)
    assert private.parse_date("11/16/2026") == date(2026, 11, 16)
    assert private.parse_date(46342) == date(2026, 11, 16)  # Sheets serial
    assert private.parse_date("next monday") is None
    assert private.parse_date(True) is None


def test_bad_rows_are_skipped_not_guessed():
    entries, skipped = private.parse_rows([
        ONCALL,
        {"start_date": "soon", "end_date": "", "kind": "oncall"},          # bad date
        {"start_date": "2026-11-20", "end_date": "", "kind": "vacation"},  # unknown kind
        {"start_date": "2026-11-20", "end_date": "2026-11-19", "kind": "blocker"},  # ends before start
        {"start_date": "", "end_date": "", "kind": "", "note": ""},       # blank row
        {"Start_Date": "2026-11-27", "End_Date": "", "Kind": "Blocker", "Note": "Family"},
    ])
    assert skipped == 3
    assert [e["kind"] for e in entries] == ["oncall", "blocker"]
    assert entries[1]["end"] == date(2026, 11, 28)  # one-day blocker when no end date


# ---------------------------------------------------------------- honesty
def test_oncall_week_is_monday_to_monday_end_exclusive():
    info = loaded(ONCALL)
    assert private.lines_for(info, date(2026, 11, 15)) == []                    # Sun before
    assert private.lines_for(info, date(2026, 11, 19)) == [private.ONCALL_LINE]  # Thu
    assert private.lines_for(info, date(2026, 11, 22)) == [private.ONCALL_LINE]  # Sun
    assert private.lines_for(info, date(2026, 11, 23)) == []                    # next Mon


def test_missing_or_broken_sheet_says_unknown_never_all_clear():
    assert private.lines_for(private.load({}), date(2026, 11, 19)) == [private.UNSET_LINE]
    bad_key = private.load({"GOOGLE_SA_KEY": "{not json", "PRIVATE_SHEET_ID": "s"})
    assert bad_key["status"] == "down" and private.lines_for(bad_key, date(2026, 11, 19)) == [private.DOWN_LINE]

    def boom(key, sheet):
        raise PermissionError("The caller does not have permission: sheet123 x@y.iam")
    down = private.load(ENV, fetch=boom)
    assert private.lines_for(down, date(2026, 11, 19)) == [private.DOWN_LINE]
    assert down["reason"] == "PermissionError"  # no message text: it can carry ids/emails


def test_blocker_comes_before_oncall():
    info = loaded(ONCALL, {"start_date": "2026-11-20", "end_date": "", "kind": "blocker", "note": "Dinner"})
    assert private.lines_for(info, date(2026, 11, 20)) == [
        "Blocked tonight: Dinner. Skip?", private.ONCALL_LINE]


# ---------------------------------------------------------------- push merge
def test_private_line_goes_after_plan_and_drops_offer_rule_when_full():
    push = compose_push(day_for(NOW.date()))
    assert push["date"] == NOW.date().isoformat()
    before = push["message"].split("\n")
    merged = add_private_lines(push, [private.ONCALL_LINE])["message"].split("\n")
    assert len(merged) <= MAX_LINES
    assert merged[0] == before[0] and merged[1] == private.ONCALL_LINE
    if len(before) == MAX_LINES:
        assert not any(line.startswith("Offer rule:") for line in merged)
    assert add_private_lines(push, []) is push


# ---------------------------------------------------------------- no leaks
def test_send_logs_only_the_read_status(capsys, monkeypatch):
    sent = {}
    monkeypatch.setattr("brief.__main__.send_ntfy", lambda push, topic: sent.update(push))
    push = compose_push(day_for(date(2026, 11, 19)))
    secret_note = {"start_date": "2026-11-19", "end_date": "", "kind": "blocker", "note": "Lawyer meeting"}
    code = send(push, {"NTFY_TOPIC": "t"}, load=lambda env: private.load(
        ENV, fetch=lambda k, s: [ONCALL, secret_note]))
    out = capsys.readouterr().out
    assert code == 0
    assert "Lawyer meeting" in sent["message"] and private.ONCALL_LINE in sent["message"]
    assert "Lawyer meeting" not in out and "On call" not in out and "Blocked" not in out
    assert "Private schedule: ok (read OK)." in out


def test_send_mode_reads_push_file_and_adds_private_lines(tmp_path, monkeypatch, capsys):
    sent = {}
    monkeypatch.setattr("brief.__main__.send_ntfy", lambda push, topic: sent.update(push))
    real_load = private.load
    monkeypatch.setattr(private, "load", lambda env: real_load(ENV, fetch=lambda k, s: [ONCALL]))
    push_file = tmp_path / "push.json"
    push_file.write_text(json.dumps(compose_push(day_for(date(2026, 11, 19)))), encoding="utf-8")
    assert main(["--mode", "send", "--push-file", str(push_file)], env={"NTFY_TOPIC": "t"}) == 0
    assert private.ONCALL_LINE in sent["message"]
    assert private.ONCALL_LINE not in capsys.readouterr().out
    assert private.ONCALL_LINE not in push_file.read_text(encoding="utf-8")  # never saved


def test_workflow_passes_private_secrets_to_notify_only():
    import pathlib
    import yaml
    wf = yaml.safe_load(pathlib.Path(".github/workflows/brief.yml").read_text())
    for name, job in wf["jobs"].items():
        text = json.dumps(job)
        assert ("GOOGLE_SA_KEY" in text) == (name == "notify"), name
        assert ("PRIVATE_SHEET_ID" in text) == (name == "notify"), name
