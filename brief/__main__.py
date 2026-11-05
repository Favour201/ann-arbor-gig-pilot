"""Entry point: python -m brief --mode dry-run [--now 2026-11-07T15:15]

PR 1 supports dry-run only (fetch live sources, print the brief to stdout).
Page and push modes arrive in later PRs.
"""
import argparse
from datetime import datetime
import os
import sys

from .config import TZ
from .daybrief import build_brief
from .events import fetch_events, horizon
from .config import HORIZON_DAYS
from .text import render_text
from .weather import fetch_forecast


def parse_now(value):
    if not value:
        return datetime.now(TZ)
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m brief")
    parser.add_argument("--mode", choices=("dry-run",), default="dry-run")
    parser.add_argument("--now", help="Override 'now' (America/New_York if no offset), for testing")
    args = parser.parse_args(argv)

    now = parse_now(args.now)
    events_data = fetch_events(horizon(now.date(), HORIZON_DAYS),
                               os.environ.get("EVENT_FEEDS_JSON", "[]"))
    forecast = fetch_forecast()
    print(render_text(build_brief(now, events_data, forecast)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
