"""Decide what a workflow run should do: push, refresh, page, dry-run or skip.

GitHub cron runs in UTC and knows nothing about daylight saving time. The
workflow therefore schedules the 3:15 PM push twice, once for EDT (UTC-4) and
once for EST (UTC-5). Each run reads which cron line fired
(github.event.schedule) and only proceeds if that line matches New York's
current UTC offset. Deciding by the cron line, not by the clock, keeps this
correct even when GitHub starts a scheduled run late.

Usage in Actions: python -m brief.gate  (reads EVENT_NAME, SCHEDULE, INPUT_MODE;
writes mode=... to $GITHUB_OUTPUT).
"""
from datetime import datetime, timedelta
import os
import sys

from .config import DRIVING_WEEKDAYS, TZ

# Must match .github/workflows/brief.yml exactly (a test checks this).
PUSH_CRONS = {
    "15 19 * * 0,4,5,6": -4,  # 3:15 PM EDT, Thu-Sun
    "15 20 * * 0,4,5,6": -5,  # 3:15 PM EST, Thu-Sun
}
REFRESH_CRON = "40 10 * * *"  # 5:40 AM EST / 6:40 AM EDT, daily page refresh
MANUAL_MODES = ("dry-run", "page", "push")
# Sent by the external scheduler (cron-job.org) through the workflow_dispatch
# API at 3:15 PM New York time. Treated like the GitHub cron push: driving
# days only, and guarded against duplicates and late runs.
EXTERNAL_MODE = "scheduled-push"


def utc_offset_hours(now):
    return int(now.astimezone(TZ).utcoffset() / timedelta(hours=1))


def decide(event_name, schedule="", input_mode="", now=None):
    """Return (mode, reason). mode is one of push, refresh, page, dry-run, skip."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if event_name == "workflow_dispatch" and input_mode == EXTERNAL_MODE:
        if now.weekday() not in DRIVING_WEEKDAYS:
            return "skip", "external trigger on a non-driving day"
        return "push", "3:15 PM driving-day brief (external scheduler)"
    if event_name == "workflow_dispatch":
        mode = input_mode or "dry-run"
        if mode not in MANUAL_MODES:
            return "skip", f"unknown manual mode {mode!r}"
        return mode, "manual run"
    if event_name != "schedule":
        return "skip", f"not a scheduled or manual run ({event_name})"
    if schedule in PUSH_CRONS:
        offset = utc_offset_hours(now)
        if offset != PUSH_CRONS[schedule]:
            return "skip", f"this slot is for UTC{PUSH_CRONS[schedule]:+d}; New York is UTC{offset:+d} today"
        if now.weekday() not in DRIVING_WEEKDAYS:
            return "skip", "not a driving day in New York"
        return "push", f"3:15 PM driving-day brief (UTC{offset:+d})"
    if schedule == REFRESH_CRON:
        return "refresh", "morning page refresh (deploys only if the page changed)"
    return "skip", f"unrecognized schedule {schedule!r}"


def is_guarded(event_name, input_mode):
    """Automatic pushes (GitHub cron or external scheduler) are guarded; your own manual push isn't."""
    return event_name == "schedule" or input_mode == EXTERNAL_MODE


def main(env=os.environ):
    event, mode_in = env.get("EVENT_NAME", ""), env.get("INPUT_MODE", "")
    mode, reason = decide(event, env.get("SCHEDULE", ""), mode_in)
    guard = mode == "push" and is_guarded(event, mode_in)
    print(f"mode={mode} guard={str(guard).lower()} ({reason})")
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write(f"mode={mode}\nguard={str(guard).lower()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
