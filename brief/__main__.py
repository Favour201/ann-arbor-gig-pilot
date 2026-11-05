"""Entry point: python -m brief --mode MODE [--now 2026-11-07T15:15]

dry-run: fetch live sources, print the full brief and the push it would send.
page:    same, then write the static site (--out) and the push (--push-file).
refresh: like page, but publish=false if the live page already says the same.
send:    send a push written earlier by `page` (no fetching). Used after deploy,
         so the link in the push opens a page that is already up to date.
push:    fetch, build and send in one go (handy locally; no page).
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

from .config import HORIZON_DAYS, TZ
from .daybrief import build_brief
from .events import fetch_events, horizon
from .notify import PushError, send_ntfy
from .push import compose_push
from .http import get
from .render import content_key, live_content_key, render_page
from .text import render_text
from .weather import fetch_forecast


def parse_now(value):
    if not value:
        return datetime.now(TZ)
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def render_push(push):
    return f"--- push: {push['title']} (priority {push['priority']})\n{push['message']}"


def write_site(brief, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_page(brief), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is
    return out / "index.html"


def without_page(push):
    """Pages failed to deploy: still send the brief, but don't link to a stale page."""
    return {**push, "click": "", "title": push["title"] + " (page not updated)"}


def page_changed(brief, page_url):
    """Compare with the deployed page. Unknown (fetch failed) counts as changed."""
    if not page_url:
        return True
    try:
        live = live_content_key(get(page_url, timeout=(4, 10)).text)
    except Exception as exc:  # noqa: BLE001 - any failure means "deploy to be safe"
        print(f"Couldn't read the live page ({type(exc).__name__}); deploying.")
        return True
    return live != content_key(brief)


def set_output(env, **values):
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            for key, value in values.items():
                out.write(f"{key}={value}\n")


def send(push, env):
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
    args = parser.parse_args(argv)

    if args.mode == "send":
        push = json.loads(Path(args.push_file).read_text(encoding="utf-8"))
        if env.get("PAGE_DEPLOYED", "true") != "true":
            push = without_page(push)
        print(render_push(push))
        return send(push, env)

    now = parse_now(args.now)
    events_data = fetch_events(horizon(now.date(), HORIZON_DAYS), env.get("EVENT_FEEDS_JSON", "[]"))
    forecast = fetch_forecast()
    brief = build_brief(now, events_data, forecast)
    push = compose_push(brief["days"][0], env.get("PAGE_URL", ""))

    print(render_text(brief))
    print()
    print(render_push(push))
    if args.mode in ("page", "refresh"):
        print(f"Wrote {write_site(brief, args.out)}")
        Path(args.push_file).write_text(json.dumps(push, ensure_ascii=False), encoding="utf-8")
        publish = args.mode == "page" or page_changed(brief, env.get("PAGE_URL", ""))
        print("Page changed; deploying." if publish else "Live page already up to date; skipping deploy.")
        set_output(env, publish=str(publish).lower())
    elif args.mode == "push":
        return send(push, env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
