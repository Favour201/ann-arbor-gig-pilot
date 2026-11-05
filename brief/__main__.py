"""Entry point: python -m brief --mode dry-run|push [--now 2026-11-07T15:15]

dry-run: fetch live sources, print the full brief and the push it would send.
push:    same, then send today's push via ntfy (topic from NTFY_TOPIC).
"""
import argparse
from datetime import datetime
import os
import sys

from .config import HORIZON_DAYS, TZ
from .daybrief import build_brief
from .events import fetch_events, horizon
from .notify import PushError, send_ntfy
from .push import compose_push
from .text import render_text
from .weather import fetch_forecast


def parse_now(value):
    if not value:
        return datetime.now(TZ)
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def render_push(push):
    return f"--- push: {push['title']} (priority {push['priority']})\n{push['message']}"


def main(argv=None, env=os.environ):
    parser = argparse.ArgumentParser(prog="python -m brief")
    parser.add_argument("--mode", choices=("dry-run", "push"), default="dry-run")
    parser.add_argument("--now", help="Override 'now' (America/New_York if no offset), for testing")
    args = parser.parse_args(argv)

    now = parse_now(args.now)
    events_data = fetch_events(horizon(now.date(), HORIZON_DAYS), env.get("EVENT_FEEDS_JSON", "[]"))
    forecast = fetch_forecast()
    brief = build_brief(now, events_data, forecast)
    push = compose_push(brief["days"][0], env.get("PAGE_URL", ""))

    print(render_text(brief))
    print()
    print(render_push(push))
    if args.mode == "push":
        try:
            send_ntfy(push, env.get("NTFY_TOPIC", ""))
        except PushError as exc:
            print(f"Push failed: {exc}", file=sys.stderr)
            return 1
        print("Push sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
