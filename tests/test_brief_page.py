"""Static page: content, escaping, honesty labels, and the page -> send flow."""
from datetime import date
import json

from brief import daybrief, events as ev
from brief.render import render_page

from test_brief_daybrief import NOW, all_days, events_data, forecast


def brief_with(found=(), ok=True, periods=None):
    data = events_data(found, ok=ok)
    data["not_covered"] = ("A2 school calendars: events load only in a browser.",)
    return daybrief.build_brief(NOW, data, forecast(all_days() if periods is None else periods))


def test_page_shows_five_days_today_first_and_sources():
    html = render_page(brief_with())
    assert html.startswith("<!doctype html>")
    assert html.count('<section class="day') == 5
    assert html.index("Thursday, Nov 5") < html.index("Monday, Nov 9")
    assert '<section class="day today">' in html
    assert "6:00 PM – 8:30 PM" in html and "optional" in html
    assert "Sources (all checked)" in html
    assert "Not covered: A2 school calendars" in html
    assert 'name="robots" content="noindex"' in html
    assert 'data-generated="2026-11-05T15:15:00-05:00"' in html


def test_feed_text_is_escaped():
    evil = ev.event_record('<script>alert(1)</script> & "Friends"', "Hill Auditorium",
                           "2026-11-05T19:00:00-05:00", None, "UMS", "https://ums.org/x?a=1&b=2")
    html = render_page(brief_with([evil]))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;Friends&quot;" in html
    assert 'href="https://ums.org/x?a=1&amp;b=2"' in html


def test_unverified_and_unknown_are_labeled_not_hidden():
    html = render_page(brief_with(ok=False, periods=[]))
    assert "Sources (1 not verified)" in html
    assert "may be incomplete" in html
    assert 'class="wx unknown"' in html


def test_skip_and_game_day_render():
    game = ev.event_record("Michigan Football vs. Michigan State", "Michigan Stadium",
                           "2026-11-07T19:30:00-05:00", None, "Michigan Athletics")
    html = render_page(brief_with([game], periods=all_days("Freezing Rain", 30)))
    assert '<div class="skip">Skip tonight?' in html
    assert "confirmed Michigan kickoff (7:30 PM)" in html
    assert '<li class="win rest">' in html


def test_page_has_no_private_or_overclaiming_text():
    html = render_page(brief_with()).lower()
    for banned in ("arbor circle", "surge expected", "guaranteed earnings", "profit", "$/hr"):
        assert banned not in html


def test_page_mode_writes_site_and_push_then_send_reads_it(tmp_path, monkeypatch, capsys):
    import brief.__main__ as entry
    monkeypatch.setattr(entry, "fetch_events", lambda days, extra: events_data())
    monkeypatch.setattr(entry, "fetch_forecast", lambda: forecast(all_days()))
    site, push_file = tmp_path / "site", tmp_path / "push.json"
    env = {"PAGE_URL": "https://favour201.github.io/ann-arbor-gig-pilot/"}
    assert entry.main(["--mode", "page", "--now", "2026-11-05T15:15", "--out", str(site),
                       "--push-file", str(push_file)], env=env) == 0
    assert (site / "index.html").read_text().startswith("<!doctype html>")
    assert (site / ".nojekyll").exists()
    push = json.loads(push_file.read_text())
    assert push["click"] == env["PAGE_URL"] and push["title"] == "Thu Nov 5 · Gig brief"
    assert not (site / "push.json").exists()  # the push file never ships with the site

    sent = []
    monkeypatch.setattr(entry, "send_ntfy", lambda p, topic: sent.append((p, topic)) or 200)
    assert entry.main(["--mode", "send", "--push-file", str(push_file)], env={"NTFY_TOPIC": "gp-x"}) == 0
    assert sent == [(push, "gp-x")]
