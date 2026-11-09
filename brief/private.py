"""Private schedule input (on-call weeks, blockers) from a private Google Sheet.

PRIVACY: the repo is public, so its Actions logs and artifacts are public too.
Everything read here is used ONLY in the notify job, added to the push right
before it is sent. Nothing from the sheet is printed, written to disk, put on
the page or the .ics feed. Log lines say only whether the read worked.

Access: a Google service account (key JSON in the GOOGLE_SA_KEY secret) with a
read-only Sheets scope, so the token can only read even if the sheet is shared
with it as Editor. PRIVATE_SHEET_ID names the spreadsheet.

Schedule tab, header row:  start_date | end_date | kind | note
  kind: oncall | blocker | remote      end_date is exclusive (Mon -> next Mon)

Honesty: if the sheet isn't set up or can't be read, the push says on-call is
unknown. Missing data is never treated as "not on call".
"""
from datetime import date, datetime, timedelta
import json

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SCHEDULE_TAB = "Schedule"
KINDS = ("oncall", "blocker", "remote")
SHEETS_EPOCH = date(1899, 12, 30)  # serial day 0 in Google Sheets

ONCALL_LINE = "On call this week: laptop handy, skip airport/long trips (30-min reply)."
UNSET_LINE = "On-call: unknown (private sheet not set up)."
DOWN_LINE = "On-call: unknown (private sheet unavailable)."


def parse_date(value):
    """ISO 2026-11-16, US 11/16/2026, or a Sheets date serial. None if unreadable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return SHEETS_EPOCH + timedelta(days=int(value)) if 40000 < value < 80000 else None
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_rows(records):
    """Schedule rows -> (entries, skipped_count). Bad rows are skipped, not guessed."""
    entries, skipped = [], 0
    for row in records:
        row = {str(k).strip().lower(): v for k, v in row.items()}
        if not any(str(v).strip() for v in row.values()):
            continue  # blank row
        start, end = parse_date(row.get("start_date")), parse_date(row.get("end_date"))
        kind = str(row.get("kind", "")).strip().lower().replace("-", "").replace(" ", "")
        if start is None or kind not in KINDS:
            skipped += 1
            continue
        end = end or start + timedelta(days=1)  # one-day entry if no end date
        if end <= start:
            skipped += 1
            continue
        entries.append({"start": start, "end": end, "kind": kind,
                        "note": str(row.get("note", "")).strip()[:80]})
    return entries, skipped


def fetch_schedule_records(key_json, sheet_id):
    """Read the Schedule tab with a read-only token. Network; not unit-tested."""
    import gspread
    client = gspread.service_account_from_dict(json.loads(key_json), scopes=SCOPES)
    client.http_client.set_timeout(15)
    sheet = client.open_by_key(sheet_id).worksheet(SCHEDULE_TAB)
    return sheet.get_all_records(value_render_option="UNFORMATTED_VALUE")


def load(env, fetch=fetch_schedule_records):
    """{'status': ok|unset|down, 'entries': [...], 'reason': str (safe to log), 'skipped': n}"""
    key, sheet_id = env.get("GOOGLE_SA_KEY", "").strip(), env.get("PRIVATE_SHEET_ID", "").strip()
    if not key or not sheet_id:
        return {"status": "unset", "entries": [], "skipped": 0,
                "reason": "GOOGLE_SA_KEY / PRIVATE_SHEET_ID not set"}
    try:
        json.loads(key)
    except ValueError:
        return {"status": "down", "entries": [], "skipped": 0, "reason": "GOOGLE_SA_KEY is not valid JSON"}
    try:
        entries, skipped = parse_rows(fetch(key, sheet_id))
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown"; never log details
        return {"status": "down", "entries": [], "skipped": 0, "reason": type(exc).__name__}
    return {"status": "ok", "entries": entries, "skipped": skipped, "reason": "read OK"}


def active(entries, day, kind):
    return [e for e in entries if e["kind"] == kind and e["start"] <= day < e["end"]]


def lines_for(private, day):
    """Push lines for this day, most important first."""
    if private["status"] == "unset":
        return [UNSET_LINE]
    if private["status"] != "ok":
        return [DOWN_LINE]
    lines = []
    for blocker in active(private["entries"], day, "blocker"):
        lines.append(f"Blocked tonight: {blocker['note'] or 'personal blocker'}. Skip?")
    if active(private["entries"], day, "oncall"):
        lines.append(ONCALL_LINE)
    return lines


def log_summary(private):
    """The only thing about the private sheet that may reach the (public) log."""
    note = f", {private['skipped']} row(s) unreadable" if private.get("skipped") else ""
    return f"Private schedule: {private['status']} ({private['reason']}{note})."
