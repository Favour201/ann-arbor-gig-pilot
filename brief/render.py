"""Static HTML page for the brief: today + the next 4 days, then sources.

One self-contained file (inline CSS, a few lines of JS for the staleness
banner), mobile-first, graphite palette from the legacy app. Every string from
a feed is HTML-escaped. Public data only.
"""
from hashlib import sha256
from html import escape
import json
import re

from .text import day_label

CSS = """
:root{color-scheme:dark;--bg:#101113;--card:#1d1e21;--line:#3a3b3f;--text:#f1f0ed;--muted:#bcbab5;
--good:#5f8a6c;--caution:#b08d45;--high:#b5544f;--unknown:#7d828a;--accent:#b7b1a6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:18px 14px 40px}
header h1{font-size:1.35rem;margin:0 0 2px;letter-spacing:-.01em}
.muted{color:var(--muted);font-size:.85rem}
.stale{display:none;background:#3a2a1f;border:1px solid var(--caution);border-radius:10px;padding:10px 12px;margin:12px 0}
.day{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 15px;margin:14px 0}
.day.today{border-color:var(--accent)}
.day h2{font-size:1.1rem;margin:0 0 8px;display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.tag{font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.skip{background:#3b1f1e;border-left:4px solid var(--high);border-radius:8px;padding:8px 10px;margin:6px 0 10px;font-weight:600}
.gameday{border-left:3px solid var(--accent);padding-left:9px;color:var(--muted);font-size:.88rem;margin:4px 0 10px}
ul{list-style:none;margin:0;padding:0}
.win{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #2a2b2e}
.win:last-child{border-bottom:0}
.win .t{min-width:9.5em;font-weight:700;color:#e1dcd1}
.win.rest .t,.win.rest .n{color:var(--muted)}
.opt{color:var(--muted);font-size:.8rem}
.wx{border-left:4px solid var(--unknown);background:#18191b;border-radius:9px;padding:9px 11px;margin:10px 0}
.wx.good{border-color:var(--good)}.wx.caution{border-color:var(--caution)}.wx.high{border-color:var(--high)}
.wx b{display:block}
.ev{padding:6px 0;border-top:1px solid #2a2b2e}
.ev .n{font-weight:600}
.small{font-size:.84rem;color:var(--muted)}
.note{font-size:.84rem;color:var(--caution);margin-top:6px}
.rule{font-size:.84rem;color:var(--muted);margin-top:8px}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 13px;margin:12px 0}
summary{font-weight:650;cursor:pointer;min-height:28px}
.src{display:flex;justify-content:space-between;gap:10px;padding:4px 0;font-size:.86rem;border-bottom:1px solid #2a2b2e}
.src .bad{color:var(--caution);text-align:right}
a{color:#ded8cc}
footer{margin-top:18px}
"""

STALE_JS = """
(function(){var g=Date.parse(document.body.dataset.generated);if(!g)return;
var h=(Date.now()-g)/36e5;if(h<30)return;var el=document.getElementById('stale');
el.textContent='This brief is '+Math.round(h)+' hours old. The scheduled update may have stopped; check the Actions tab.';
el.style.display='block';})();
"""


def _clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def _day_html(d, is_today):
    e = escape
    tag = "Today" if is_today else ("" if d["driving_day"] else "Not a driving day")
    out = [f'<section class="day{" today" if is_today else ""}">',
           f'<h2><span>{e(d["date"].strftime("%A, %b"))} {d["date"].day}</span>'
           f'<span class="tag">{e(tag)}</span></h2>']
    if d["skip"]:
        out.append(f'<div class="skip">{e(d["skip"])}</div>')
    if d["game_day"]:
        out.append(f'<div class="gameday">{e(d["game_day"])}</div>')
    if d["windows"]:
        out.append("<ul>")
        for w in d["windows"]:
            opt = ' <span class="opt">optional</span>' if w["optional"] else ""
            out.append(f'<li class="win {w["kind"]}"><span class="t">{e(w["time"])}</span>'
                       f'<span class="n">{e(w["title"])}{opt}</span></li>')
        out.append("</ul>")
    for k in d["kickoff_tbd"]:
        out.append(f'<div class="note">Kickoff TBD: {e(k["name"])}. No split shift until a time is published.</div>')
    wx = d["weather"]
    if wx:
        temp = f", {wx['temperature']}°F" if wx["temperature"] is not None else ""
        out.append(f'<div class="wx {e(wx["severity"])}"><b>{e(wx["icon"])} {e(wx["pill"].title())}: '
                   f'{e(wx["forecast"])}{e(temp)}</b><span class="small">{e(wx["note"])} '
                   f'Hours checked: {e(wx.get("window_label", ""))}.</span></div>')
    events = [ev for ev in d["events"] if ev["time"] != "Kickoff TBD"]
    for ev in events:
        name = e(ev["name"])
        if ev["url"]:
            name = f'<a href="{e(ev["url"])}" rel="noopener">{name}</a>'
        out.append(f'<div class="ev"><div class="n">{e(ev["time"])} · {name}</div>'
                   f'<div class="small">{e(ev["venue"])}. {e(ev["traffic"])}</div></div>')
    if not d["events"]:
        out.append('<div class="ev small">No events found.</div>')
    if not d["events_complete"]:
        out.append('<div class="note">Some event sources weren\'t verified for this day, so this list may be incomplete.</div>')
    if d["offer_rule"]:
        out.append(f'<div class="rule">{e(d["offer_rule"])}</div>')
    out.append("</section>")
    return "\n".join(out)


def _sources_html(s):
    e = escape
    rows = []
    for p in s["weather"]:
        status = (f'OK · NWS issued {e(p["updated"])}' if p["ok"] and p["updated"] else
                  "OK" if p["ok"] else f'<span class="bad">Unavailable ({e(p["reason"] or "unknown")})</span>')
        rows.append(f'<div class="src"><span>NWS hourly · {e(p["place"])}</span><span>{status}</span></div>')
    for src in s["events"]:
        if not src["ok"]:
            status = f'<span class="bad">Not verified ({e(src["reason"] or "unknown")})</span>'
        elif src["missing_days"]:
            days = ", ".join(day_label(d) for d in src["missing_days"])
            status = f'<span class="bad">Not visible for {e(days)}</span>'
        else:
            status = "OK"
        rows.append(f'<div class="src"><span>{e(src["source"])}</span><span>{status}</span></div>')
    extra = "".join(f'<p class="small">Not covered: {e(n)}</p>' for n in s.get("not_covered", ()))
    extra += "".join(f'<p class="small">Note: {e(n)}</p>' for n in s["notes"])
    bad = sum(1 for src in s["events"] if not src["ok"]) + sum(1 for p in s["weather"] if not p["ok"])
    label = f"Sources ({bad} not verified)" if bad else "Sources (all checked)"
    return (f'<details><summary>{e(label)}</summary>'
            f'<p class="small">Weather checked {e(_clock(s["weather_checked"]))}, events checked '
            f'{e(_clock(s["events_checked"]))}. "Checked" is when the brief fetched the source, '
            f'not when the source last changed.</p>{"".join(rows)}{extra}</details>')


HASH_META = re.compile(r'<meta name="brief-content" content="([0-9a-f]{16})">')
PUSHED_META = re.compile(r'<meta name="brief-pushed" content="(\d{4}-\d{2}-\d{2})?">')
MORNING_META = re.compile(r'<meta name="brief-pushed-morning" content="(\d{4}-\d{2}-\d{2})?">')


def content_key(brief):
    """Fingerprint of what the page says, ignoring when it was generated or checked.

    Used by the morning refresh to skip redeploying an unchanged page. The page
    shows "today + 4 days", so a new day always changes it.
    """
    days = []
    for d in brief["days"]:
        wx = d["weather"]
        days.append({
            "date": d["date"].isoformat(), "skip": d["skip"], "game": d["game_day"],
            "windows": [(w["time"], w["title"], w["optional"]) for w in d["windows"]],
            "weather": wx and (wx["severity"], wx["forecast"], wx["temperature"], wx["note"]),
            "events": [(e["time"], e["name"], e["venue"]) for e in d["events"]],
            "complete": d["events_complete"],
        })
    sources = [(s["source"], s["ok"], s.get("reason", ""), [x.isoformat() for x in s["missing_days"]])
               for s in brief["sources"]["events"]]
    sources += [(p["place"], p["ok"]) for p in brief["sources"]["weather"]]
    blob = json.dumps({"days": days, "sources": sources}, sort_keys=True, default=str)
    return sha256(blob.encode()).hexdigest()[:16]


def live_pushed_on(html, slot="afternoon"):
    """Date (YYYY-MM-DD) of the last push in this slot recorded on the deployed page, or ''."""
    match = (MORNING_META if slot == "morning" else PUSHED_META).search(html or "")
    return (match.group(1) or "") if match else ""


def live_content_key(html):
    match = HASH_META.search(html or "")
    return match.group(1) if match else ""


def render_page(brief, pushed_on="", morning_on=""):
    generated = brief["generated"]
    days = "\n".join(_day_html(d, i == 0) for i, d in enumerate(brief["days"]))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<meta name="brief-content" content="{content_key(brief)}">
<meta name="brief-pushed" content="{escape(pushed_on)}">
<meta name="brief-pushed-morning" content="{escape(morning_on)}">
<title>Gig brief · {escape(day_label(brief["days"][0]["date"]))}</title>
<style>{CSS}</style></head>
<body data-generated="{escape(generated.isoformat())}"><main>
<header><h1>Gig brief</h1>
<div class="muted">Updated {escape(generated.strftime("%a %b"))} {generated.day}, {escape(_clock(generated))} ET ·
planning suggestions, not measured demand</div></header>
<div id="stale" class="stale" role="alert"></div>
{days}
{_sources_html(brief["sources"])}
<footer class="muted">Gridwise remains the record for earnings, miles and taxes.
<a href="https://github.com/Favour201/ann-arbor-gig-pilot">Source</a></footer>
</main><script>{STALE_JS}</script></body></html>
"""
