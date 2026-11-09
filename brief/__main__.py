"""Entry point: python -m brief --mode MODE [--now 2026-11-07T15:15]

dry-run: fetch live sources, print the full brief and the push it would send.
page:    same, then write the static site (--out) and the push (--push-file).
refresh: like page, but publish=false if the live page already says the same.
send:    send a push written earlier by `page` (no fetching). Used after deploy,
         so the link in the push opens a page that is already up to date.
push:    fetch, build and send in one go (handy locally; no page).
"""
import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import sys

from .config import HORIZON_DAYS, TZ
from .daybrief import build_brief
from .events import fetch_events, horizon
from .notify import PushError, send_ntfy
from . import private
from .push import add_private_lines, compose_push
from .http import get
from .daybrief import push_cutoff_minute
from .render import content_key, live_content_key, live_pushed_on, render_page
from .text import render_text
from .weather import fetch_forecast


def parse_now(value):
    if not value:
        return datetime.now(TZ)
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def render_push(push):
    return f"--- push: {push['title']} (priority {push['priority']})\n{push['message']}"


def write_site(brief, out_dir, pushed_on=""):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_page(brief, pushed_on), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is
    return out / "index.html"


def without_page(push):
    """Pages failed to deploy: still send the brief, but don't link to a stale page."""
    return {**push, "click": "", "title": push["title"] + " (page not updated)"}


def fetch_live_page(page_url):
    """HTML of the deployed page, or None if there's no URL or it can't be read."""
    if not page_url:
        return None
    try:
        return get(page_url, timeout=(4, 10)).text
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown"
        print(f"Couldn't read the live page ({type(exc).__name__}).")
        return None


def should_send(brief, now, already_pushed_on):
    """Guard for scheduled pushes: once per day, and not after the window has started."""
    today = brief["days"][0]
    if already_pushed_on == today["date"].isoformat():
        return False, "today's brief was already sent"
    cutoff = push_cutoff_minute(today)
    minute = now.hour * 60 + now.minute
    if minute >= cutoff:
        return False, f"too late: it's past {cutoff // 60 % 12 or 12}:{cutoff % 60:02d} PM, when driving starts"
    return True, "on time"


def set_output(env, **values):
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            for key, value in values.items():
                out.write(f"{key}={value}\n")


def with_private(push, env, load=None):
    """Add on-call/blocker lines from the private sheet. Logs only the read status."""
    info = (load or private.load)(env)
    print(private.log_summary(info))
    day = date.fromisoformat(push["date"]) if push.get("date") else datetime.now(TZ).date()
    return add_private_lines(push, private.lines_for(info, day))


def send(push, env, load=None):
    print(render_push(push))  # public part only; private lines are added after this
    push = with_private(push, env, load)
    try:
        send_ntfy(push, env.get("NTFY_TOPIC", ""))
    except PushError as exc:
        print(f"Push failed: {exc}", file=sys.stderr)
        return 1
    print("Push sent.")
    return 0


def main(argv=None, env=os.environ):
    parser = argparse.ArgumentParser(prog="python -m brief")
    parser.add_argument("--mode", choices=("dry-run", "page", "refresh", "send", "push"), default="dry-run")
    parser.add_argument("--now", help="Override 'now' (America/New_York if no offset), for testing")
    parser.add_argument("--out", default="site", help="Site directory for --mode page")
    parser.add_argument("--push-file", default="push.json", help="Push JSON written by page, read by send")
    parser.add_argument("--push-run", action="store_true", help="This page build goes with a push")
    parser.add_argument("--guard", action="store_true",
                        help="Scheduled push: skip if already sent today or too late")
    args = parser.parse_args(argv)

    if args.mode == "send":
        push = json.loads(Path(args.push_file).read_text(encoding="utf-8"))
        if env.get("PAGE_DEPLOYED", "true") != "true":
            push = without_page(push)
        return send(push, env)

    now = parse_now(args.now)
    events_data = fetch_events(horizon(now.date(), HORIZON_DAYS), env.get("EVENT_FEEDS_JSON", "[]"))
    forecast = fetch_forecast()
    brief = build_brief(now, events_data, forecast)
    push = compose_push(brief["days"][0], env.get("PAGE_URL", ""))

    print(render_text(brief))
    print()
    if args.mode != "push":
        print(render_push(push))  # send() prints it in push mode
    if args.mode in ("page", "refresh"):
        live = fetch_live_page(env.get("PAGE_URL", ""))
        pushed_on = live_pushed_on(live)
        will_send, reason = False, "not a push run"
        if args.push_run:
            will_send, reason = should_send(brief, now, pushed_on) if args.guard else (True, "manual push")
            if will_send:
                pushed_on = now.date().isoformat()
        print(f"Push: {'send' if will_send else 'skip'} ({reason})")
        print(f"Wrote {write_site(brief, args.out, pushed_on)}")
        Path(args.push_file).write_text(json.dumps(push, ensure_ascii=False), encoding="utf-8")
        if args.mode == "page":
            publish = True
        else:
            publish = live is None or live_content_key(live) != content_key(brief)
            print("Page changed; deploying." if publish else "Live page already up to date; skipping deploy.")
        set_output(env, publish=str(publish).lower(), send=str(will_send).lower(),
                   already_sent=str(pushed_on == now.date().isoformat() and not will_send).lower())
    elif args.mode == "push":
        return send(push, env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
