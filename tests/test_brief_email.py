"""Email copy of the brief: Gmail SMTP sender and the two-channel send()."""
from datetime import date
import json
import smtplib

import pytest

from brief import private
from brief.__main__ import send
from brief.mailer import EmailError, build_message, email_state, send_email
from brief.notify import PushError
from brief.push import compose_push

from test_brief_push import day_for

ENV = {"GMAIL_ADDRESS": "bot@example.com", "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop",
       "BRIEF_EMAIL_TO": "me@example.com"}
NO_SHEET = lambda env: private.load({})  # noqa: E731 - private sheet not configured


def push_for(click="https://favour201.github.io/ann-arbor-gig-pilot/"):
    return compose_push(day_for(date(2026, 11, 19)), click)


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.login_args, self.sent = None, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _reset():
    FakeSMTP.instances = []


# ------------------------------------------------------------- configuration
def test_email_state_off_ready_partial():
    assert email_state({}) == "off"
    assert email_state({"GMAIL_ADDRESS": " "}) == "off"
    assert email_state(ENV) == "ready"
    assert email_state({"GMAIL_ADDRESS": "bot@example.com"}) == "partial"


def test_partial_config_names_missing_secrets_not_values():
    with pytest.raises(EmailError) as err:
        send_email(push_for(), {"GMAIL_ADDRESS": "bot@example.com"}, smtp=FakeSMTP)
    assert "GMAIL_APP_PASSWORD" in str(err.value) and "BRIEF_EMAIL_TO" in str(err.value)
    assert "bot@example.com" not in str(err.value)
    assert FakeSMTP.instances == []


# ------------------------------------------------------------- message + SMTP
def test_message_has_title_brief_and_page_link():
    push = push_for()
    msg = build_message(push, "bot@example.com", "me@example.com")
    body = msg.get_content()
    assert msg["Subject"] == push["title"] and msg["To"] == "me@example.com"
    assert "bot@example.com" in msg["From"]
    assert push["message"] in body and "Full plan: https://favour201.github.io/" in body


def test_message_without_page_link_and_with_notes():
    msg = build_message(push_for(click=""), "a@x.com", "b@x.com", notes=["Phone push failed: HTTP 500"])
    body = msg.get_content()
    assert "Full plan" not in body and "⚠ Phone push failed: HTTP 500" in body


def test_send_email_uses_gmail_tls_and_strips_app_password_spaces():
    send_email(push_for(), ENV, smtp=FakeSMTP)
    server = FakeSMTP.instances[0]
    assert (server.host, server.port) == ("smtp.gmail.com", 465)
    assert server.login_args == ("bot@example.com", "abcdefghijklmnop")
    assert len(server.sent) == 1


@pytest.mark.parametrize("exc, expected", [
    (smtplib.SMTPAuthenticationError(535, b"bad creds for bot@example.com"), "rejected the login"),
    (smtplib.SMTPServerDisconnected("bot@example.com gone"), "SMTPServerDisconnected"),
    (OSError("network down"), "OSError"),
])
def test_errors_never_include_addresses_or_password(exc, expected):
    class Boom(FakeSMTP):
        def login(self, user, password):
            raise exc
    with pytest.raises(EmailError) as err:
        send_email(push_for(), ENV, smtp=Boom)
    text = str(err.value)
    assert expected in text
    for secret in ("bot@example.com", "me@example.com", "abcd", "abcdefghijklmnop"):
        assert secret not in text


# ------------------------------------------------------------- two channels
def channels(monkeypatch, ntfy_fails=False, email_fails=False):
    """Patch both senders; return the list of (channel, payload) calls."""
    calls = []

    def fake_ntfy(push, topic):
        calls.append(("ntfy", push))
        if ntfy_fails and push["title"].endswith("Gig brief"):
            raise PushError("ntfy answered HTTP 500")
        return 200

    def fake_email(push, env, notes=()):
        calls.append(("email", push, list(notes)))
        if email_fails:
            raise EmailError("Gmail rejected the login (check GMAIL_ADDRESS / GMAIL_APP_PASSWORD)")

    monkeypatch.setattr("brief.__main__.send_ntfy", fake_ntfy)
    monkeypatch.setattr("brief.__main__.send_email", fake_email)
    return calls


def test_both_channels_get_the_same_brief(monkeypatch, capsys):
    calls = channels(monkeypatch)
    assert send(push_for(), {"NTFY_TOPIC": "t", **ENV}, load=NO_SHEET) == 0
    assert [c[0] for c in calls] == ["ntfy", "email"]
    assert calls[0][1] == calls[1][1] and calls[1][2] == []
    out = capsys.readouterr().out
    assert "Push sent." in out and "Email sent." in out
    for secret in ENV.values():
        assert secret not in out


def test_email_off_is_ntfy_only_as_before(monkeypatch, capsys):
    calls = channels(monkeypatch)
    assert send(push_for(), {"NTFY_TOPIC": "t"}, load=NO_SHEET) == 0
    assert [c[0] for c in calls] == ["ntfy"]
    assert "Email: not configured." in capsys.readouterr().out


def test_ntfy_down_email_still_delivers_and_says_so(monkeypatch, capsys):
    calls = channels(monkeypatch, ntfy_fails=True)
    assert send(push_for(), {"NTFY_TOPIC": "t", **ENV}, load=NO_SHEET) == 0
    assert calls[-1][0] == "email" and calls[-1][2] == ["Phone push failed: ntfy answered HTTP 500"]
    assert "Push failed" in capsys.readouterr().err


def test_email_down_sends_short_follow_up_push(monkeypatch, capsys):
    calls = channels(monkeypatch, email_fails=True)
    assert send(push_for(), {"NTFY_TOPIC": "t", **ENV}, load=NO_SHEET) == 0
    follow_up = calls[-1]
    assert follow_up[0] == "ntfy" and follow_up[1]["title"] == "Gig brief: email copy failed"
    assert "rejected the login" in follow_up[1]["message"]
    assert "Email failed" in capsys.readouterr().err


def test_partial_email_config_is_reported_not_silent(monkeypatch):
    calls = channels(monkeypatch)
    monkeypatch.setattr("brief.__main__.send_email", send_email)  # real check, no SMTP reached
    assert send(push_for(), {"NTFY_TOPIC": "t", "GMAIL_ADDRESS": "bot@example.com"}, load=NO_SHEET) == 0
    assert calls[-1][1]["title"] == "Gig brief: email copy failed"
    assert "missing GMAIL_APP_PASSWORD, BRIEF_EMAIL_TO" in calls[-1][1]["message"]


def test_nothing_delivered_fails_the_job(monkeypatch):
    channels(monkeypatch, ntfy_fails=True, email_fails=True)
    assert send(push_for(), {"NTFY_TOPIC": "t", **ENV}, load=NO_SHEET) == 1


def test_private_lines_reach_the_email_but_not_the_log(monkeypatch, capsys):
    calls = channels(monkeypatch)
    oncall = {"start_date": "2026-11-16", "end_date": "2026-11-23", "kind": "oncall", "note": ""}
    sheet_env = {"GOOGLE_SA_KEY": "{}", "PRIVATE_SHEET_ID": "x"}
    load = lambda env: private.load(sheet_env, fetch=lambda k, s: [oncall])  # noqa: E731
    assert send(push_for(), {"NTFY_TOPIC": "t", **ENV}, load=load) == 0
    assert private.ONCALL_LINE in calls[1][1]["message"]
    assert private.ONCALL_LINE not in capsys.readouterr().out


def test_workflow_passes_email_secrets_to_notify_only():
    import pathlib
    import yaml
    wf = yaml.safe_load(pathlib.Path(".github/workflows/brief.yml").read_text())
    for name, job in wf["jobs"].items():
        text = json.dumps(job)
        for secret in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "BRIEF_EMAIL_TO"):
            assert (f"secrets.{secret}" in text) == (name == "notify"), (name, secret)
