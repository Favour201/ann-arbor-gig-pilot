# Washtenaw Gig Pilot Pro

Graphite Pilot is a mobile-first Streamlit driver command center for Ogbuefi, operating a 16 MPG Kia Telluride in the Ann Arbor / Ypsilanti corridor. Application logic stays in `app.py`.

## Run

Python 3.10+:

```sh
pip install -r requirements.txt
streamlit run app.py
```

## Private deployment and session security

The source GitHub repository may stay public, but **the deployed Streamlit app
should be private** because it is connected to an owner-only Google Sheet.

**Required owner action (not possible from a GitHub code commit):**

1. Sign into the Streamlit Community Cloud workspace at
   https://share.streamlit.io/ and open the Gig Pilot app **Settings → Sharing**.
2. Under **Who can view this app**, choose
   **Only specific people can view this app**; save.
3. Limit the approved viewer list to your own account and trusted household
   members only. Test with a private/incognito window: an unapproved visitor
   must not be able to open the dashboard. Community Cloud currently allows
   one private app at a time per workspace.
4. Confirm your Google Sheet's General access remains **Restricted** and the
   only permitted identities are your Google account and the app service
   account. Keep Google account two-step verification enabled.

**Inside Results & Learning:** Use the single collapsed **Private access**
section to unlock history/imports. Your existing
\`DRIVER_ACCESS_PASSWORD\` remains in Streamlit Secrets (not GitHub). A valid
unlock creates a session ticket stored only on the Streamlit server.
The ticket expires after **30 minutes of inactivity** or **8 hours** since
unlock, whichever comes first, when the app next processes an interaction.
Use **Lock private data now** to immediately revoke the ticket and clear
cached Sheet records, unsaved session-only records, and Gridwise uploader state
from the running session. **Back up unsynced rows BEFORE locking**.
Changing the secret password invalidates an existing ticket on its next
access check. A boolean stored in session state alone cannot grant access.

The previous per-session five-failed-attempt cap is retained as a convenience,
not a network-level brute-force control: a new browser session can reset it.
**Private Streamlit hosting is essential**. Account-based sign-in to view the
private app is provided by Streamlit; the in-app passphrase is an extra
protection for private financial records, not a replacement for real identity
authentication. It is not necessary to create a second OAuth client solely
for the first security phase.

No API keys, private reports, OAuth secrets or passwords should ever be placed
in GitHub, a support ticket, or a shared screenshot. The repository ignores
\`.streamlit/secrets.toml\` and the \`.env\` file; confirm such files are
never committed before pushing any branch.

## What to open and when (focused three-workspace pilot)

**Drive is for a 30-second decision BEFORE you start driving.** It shows one
next planned window, a short event/weather/traffic caution, other chronological
windows and the original working calendar export. The plan prioritizes Uber
Eats and DoorDash deliveries at every time of day, including Michigan football
pregame and return windows. Uber Comfort and XL can remain enabled for good
actual ride offers, but Gig Pilot never assumes that you should switch to
rideshare at 8 PM (or after the game). Lyft isn't a scheduled driving priority.
Open-ended evening windows are **optional reconsideration points**, not a
commitment to keep driving. The driver's current **$3 per estimated total mile**
target is shown as a personal live-offer preference, counting pickup and likely
unpaid miles—not a forecast, minimum guaranteed earnings, or an automatic filter.
Verified game-day drive → rest → return timings and sliders are unchanged.
No live offer input or private journal appears on the landing screen. This is
*not* a live Uber/Lyft offer scanner and never promises demand, surge or profit.

**Events & Traffic** retains the official calendar feeds, venue/school
congestion context, detailed NWS forecasts and the collapsed diagnostics.

**Results & Learning** complements Gridwise Plus, which remains the source
of truth for actual earnings, mileage and taxes. On Results you can optionally
import Gridwise reports. Expand *Advanced tools* only if you specifically need
the previous predeparture calculator, decision journal, weekly grader, checked
fuel-price form or legacy manual shift log. The legacy tools and original
private **Shifts**/**Decisions** worksheet schemas remain intact and are not
deleted or migrated automatically. Results doesn't fetch event/weather feeds;
for journal planning windows, first visit Drive in that same session.

### Gridwise PDF/CSV report import

Open **Results & Learning → Import Gridwise reports · PDF or CSV** and unlock
private access with your existing driver password. Select one or both supported
Gridwise Earnings/Mileage PDFs or tabular CSVs. The app reads the uploaded files
**in server memory** and displays a summary and first 25 structured rows. It
requires explicit approval before appending normalized rows to a new,
**separate private** Google Sheet tab named **Gridwise_Archive**. Duplicate
natural rows in repeat imports are skipped. It does not store the original
uploaded PDFs or alter your existing Shifts/Decisions tabs.

The parser supports the column/row format of your **2022 sample PDFs** and
simple date-based CSVs with recognizably named Date/Earnings or Date/Hours/Miles
columns. Unsupported PDFs, scanned pictures, missing rows, invalid amounts,
excessive file sizes or PDF totals that cannot be validated are rejected—not
silently filled with guesses. Future Gridwise report format changes may require
parser updates; no personal Gridwise API or automated account access is assumed.
Multiple earnings-service rows on a date stay separate. When both reports are
present, the overview counts matching dates without interpreting missing miles
as zero or forcing trip-level matches. Tax deductions stay labeled as tax
deductions, **never** direct fuel or economic expense.

**Imported report rows are archive-only regardless of year**. Because the 2022
records came from a different state, different vehicle and different gas-price
environment, they are intentionally excluded from the 2026 Telluride model,
live driving advice and current after-fuel benchmarks. Future comparison
features can add explicit cohort and provenance selection after the operator
verifies it. Never upload private reports to the public GitHub repository;
use the password-protected Streamlit interface instead.

## Calendar exports (current planning date)

On Drive, expand **Add planned shifts to calendar**, explicitly select the
actual driving windows, and open a prefilled Google Calendar event link for each
or download a single .ics calendar file (Apple Calendar and Outlook). The link
only prepares an event, not a write to your calendar; save each event yourself.
Events use the schedule after any game-day timing adjustments and handle
America/New_York daylight saving time correctly. Unknown/TBD kickoff times
are never invented.

Open-ended "onward" shifts use your *explicitly chosen* 1–5-hour calendar
duration, not a published end time. Rest breaks may optionally be included.
This is manual export, not live sync. Changes to kickoff times do not update
already-saved calendar events; revise them yourself when necessary.

## Private shift history: Google Sheets setup

Shift history is designed for a private Google Sheet and is disabled until you
configure a strong driver passphrase. Your deployed app URL is public. A basic
passphrase gate is not equivalent to private hosting or identity-based login.
Use a unique 12+ character passphrase, and preferably restrict app access before
storing information you consider sensitive. No credentials are in this repository.

One-time setup:

1. Create a private Google Sheet and copy its spreadsheet ID from the URL.
2. In Google Cloud Console enable the Google Sheets and Drive APIs, create a
   service account and download its JSON key. Share ONLY the private spreadsheet
   with that service account's client_email as Editor; never make it public.
3. In Streamlit Community Cloud, open this app's settings and Secrets. Define
   these top-level TOML keys: DRIVER_ACCESS_PASSWORD (your long private passphrase)
   and SHIFT_SHEET_ID (the private spreadsheet ID).
4. Put all fields from the service account's JSON credential into a TOML section
   named [gcp_service_account]. Include type, project_id, private_key_id,
   private_key, client_email, client_id, auth_uri, token_uri,
   auth_provider_x509_cert_url and client_x509_cert_url. Quote the private_key
   with newline escapes (\\n). Alternatively, set GCP_SERVICE_ACCOUNT_JSON to
   a correctly escaped JSON string at the root of Streamlit Secrets.
5. Do not commit secrets.toml or a credentials JSON file to GitHub. The app
   creates a Shifts tab with fixed headers upon first authenticated use. Save
   a completed shift to test your access.

Under Results & Learning → Advanced tools → Legacy fuel and earnings tools → Shift history & insights, unlock the owner gate and
record actual gross pay, total online hours (including idle time), **all miles**
(including unpaid mileage), checked regular gas price, platform, area and shift
window. Optionally enter other variable vehicle costs per mile. The app
calculates **after fuel** and an explicitly estimated amount after extra costs.
Neither is full profit after taxes, insurance, wear and depreciation.

The private Sheet is persistent once configured and reachable. If storage is
unavailable, a record is held temporarily in the Streamlit session ONLY, with
a warning to download a CSV backup. CSV backups can be restored into the
current session; explicit Sync adds missing records to the private Sheet.
Never rely on session-only records across app restarts.

## Daily decision brief

Drive → Your daily decision brief combines real published event counts,
forecast cautions and the owner's actual recorded weekday shifts, only if
history is unlocked. It gives a historical **median after-fuel earnings per
online hour** only with at least 3 matching weekday shifts recorded within
90 days. This is a descriptive baseline, not a live-demand prediction.
Unknown feed coverage and insufficient history are labeled explicitly.

## Smart Michigan game-day split shifts

For a **confirmed Michigan Stadium football kickoff on Saturday or Sunday**, the roadmap automatically recommends a practical **pregame drive → home rest → return** pattern instead of showing only one broad pregame crowd window. By default, a **3:30 PM** kickoff suggests driving **12:30–2:45 PM**, taking a break **2:45–5:30 PM**, and returning **5:30 PM onward** (subject to real demand, traffic, road closures and personal preference). Use the existing *Roadmap filters & game-day preferences* expander to change the stop buffer to **30 / 45 / 60 minutes**, or the return delay to **1–4 hours after kickoff**. For noon kickoffs, the return block will not begin before 3 PM.

The pregame and optional return blocks are **delivery-first**, with Comfort/XL treated as optional opportunistic rides rather than an automatic mode change. Keep Uber Eats and DoorDash in focus; use actual offers and the personal $3/mile goal to decide whether driving makes sense. They replace the generic Saturday/Sunday baseline to avoid contradictory overlap, but other independent local event alerts remain visible. The game stays **Kickoff TBD** until its official athletics schedule publishes a usable clock time; we do not fabricate split-shift timings for unknown or away games. The plan does not predict surge or override unsafe weather/road conditions.

### Less intrusive Streamlit chrome

In `.streamlit/config.toml`, `[client] toolbarMode = "minimal"` reduces optional top-right controls. Narrow CSS hides only Streamlit's optional toolbar and gives the custom graphite hero extra top clearance while **preserving the collapsed-sidebar button** and debugging/management access through Streamlit Community Cloud's workspace. This intentionally avoids hiding the entire Streamlit header or relying on fragile full-page embedding tricks. Streamlit Cloud can still show platform-managed UI outside the app's control.

## Focused three-workspace navigation

The landing experience now opens **Drive** by default. This shows the selected date,
a compact next-step recommendation and the chronological driving windows. Tapping a
window reveals its full routing notes. Michigan confirmed game-day drive / rest /
return shifts, optional game-day timing sliders, NWS warnings, and the current
traffic-caution summary are retained. There is no gas form, feed table or sprawling
event list on this first screen.

The top **Workspace** switch moves between:

- **Drive:** recommendations first, optional "Adjust driving plan" controls and a
  "Hide finished windows" switch. A shortcut opens Events & Traffic.
- **Events & Traffic:** major venues and school/neighborhood traffic displayed in
  distinct interactive sections with event links and NWS detail. The full source
  status and timestamps are hidden inside **Advanced · Feed diagnostics**, only
  for troubleshooting, never on the Drive opening screen.
- **Results & Learning:** Private Gridwise report import and optional Advanced tools for the
  previous calculators, journal and historical shift form. This view does not
  call event/weather feeds.

Navigation uses native Streamlit state, not a new host or additional dependency.
The selected driving date and fuel input survive normal in-app navigation within
the current session. Selecting a new date refreshes the date-scoped event/forecast
lookups according to their existing caches.

## Graphite Pilot design

- **Theme:** premium monochrome graphite/near-black native Streamlit theme in `.streamlit/config.toml`, charcoal cards, warm-silver accents, clear keyboard-focus indicators and reduced-motion support. Amber remains exclusively for weather caution. CSS stays in `app.py` and requires no new dependency.
- **Greeting:** uses the Eastern local hour to display “Good morning, Ogbuefi”, “Good afternoon, Ogbuefi” or “Good evening, Ogbuefi”. The dashboard no longer uses another form of address.
- **First screen:** a compact date selector and refresh button, a next-step card, and only driving windows and relevant hazards. Fuel and full event/source details are intentionally in other workspaces; unknown weather remains unknown.
- **Roadmap first:** tap-friendly chronological driving cards with expandable routing detail. Finished windows may be hidden; game-day timing and event/traffic inclusion remain adjustable. All published event-specific traffic details are available under Events & Traffic rather than crowding the daily plan.
- **Less clutter:** Only one workspace renders at a time. Full event listings and advanced feed diagnostics appear solely in Events & Traffic. GasBuddy, the manually checked price and the profit grader live together under Earnings & Fuel.

## Live systems and actual coverage

- **Fuel:** one-tap GasBuddy search links for Ann Arbor and Ypsilanti. GasBuddy's own app/site handles nearby station discovery, prices, and location permissions; the dashboard does **not** scrape GasBuddy or claim to know live station prices. Check report age, grade, payment restrictions, and club membership. Enter the regular-gas price you checked into the single dashboard number field. This value is retained during the current Streamlit session only, is never silently invented, and drives the Weekly Profit Grader at 16 MPG.
- **Events and routing:** Official Michigan/EMU athletics and U-M campus feeds remain active, plus best-effort Marquee Arts Michigan Theater, Pioneer Athletics (Eventlink), and Pioneer/Huron/Skyline school calendars. All events are planning signals for **both demand and congestion**: major events can increase rideshare and delivery activity while delaying pickups and orders; school theater/concerts can create localized queues worth routing around, even without worthwhile gig demand. The school/neighborhood *traffic avoidance* section and chronological roadmap cues now appear by default, but can be switched off independently of major-event cues. No cue instructs you to take a trip. If Michigan football kickoff is unannounced, it stays **Kickoff TBD** with no invented clock. When the official athletics page publishes a time, the next successful event-feed refresh displays the confirmed kickoff and chronologically positions its pregame window. Events are cached for 30 minutes on a rerun, and **Refresh live sources** checks immediately; this is not a push alert or background watcher. Public HTML feeds can be blocked or show only the currently displayed month, so unverified coverage is flagged and never interpreted as an empty event slate. Optional extra JSON/ICS/JSON-LD feeds remain available through `EVENT_FEEDS_JSON`.
- **Weather:** resolves both Ann Arbor and Ypsilanti NWS `/points` grids, retrieves forecasts and intersects forecast periods with the selected Eastern-time calendar date, including overnight periods. Rain, storms, snow/ice or temperature ≤15°F adds an amber **Weather Premium Surge Warning** in the roadmap. That is an app planning label, not an official weather alert or guaranteed rideshare surge. Dates beyond the forecast horizon and outages display an unknown state.
- **Profit:** uses only the gas price the driver enters. No price means the grader stays disabled. Results recalculate when your input changes. Earnings after fuel exclude tax, wear, insurance and depreciation.

Weather cache: 15 minutes. Events: 30 minutes. Requests are bounded and independent sources run concurrently. Refresh happens on rerun/interaction or via **Refresh live sources**, not through a background scheduler. GasBuddy opens externally and is not part of the refresh cache.

## Check local gas prices

Use the on-page GasBuddy links to search in Ann Arbor or Ypsilanti. GasBuddy may permit a device-location search from its app or website, but the preset links themselves are city-based rather than exact home-address coordinates. The prefilled search parameters are convenience links; if GasBuddy ignores the search, enter a city or ZIP code there. GasBuddy is a third-party community-reported source and may show stale or incorrect prices. **Only the price you type manually** is used for calculations. No external browser-storage plugin or API key is needed.

## Optional extra event calendars

Set `EVENT_FEEDS_JSON` to a JSON array. Only use feeds you are authorized to consume. Example Streamlit secrets:

```toml
EVENT_FEEDS_JSON = '''[
  {"name":"Michigan Theater","format":"json","url":"https://your-provider.example/theater-events"},
  {"name":"Local school athletics","format":"ics","url":"https://your-provider.example/school.ics"}
]'''
```

Built-in Marquee Arts and AAPS/Pioneer sources do not need `EVENT_FEEDS_JSON` or any secrets; this optional setting remains available for additional sources. Replace the example URLs with actual feed URLs. `jsonld` supports published schema.org event data in HTML. JSON format:

```json
{"events":[{"name":"Concert","venue":"Michigan Theater, Ann Arbor","start":"2026-10-17T19:30:00-04:00","end":"2026-10-17T21:30:00-04:00","url":"https://your-provider.example/concert"}]}
```

Use a date-only `start` for TBD/all-day events. Unknown end times remain unknown. JSON rows may set `status` to `cancelled` or `postponed`. Local venues are required; remote events are excluded. Each selected date is a separate cache key. No fake event schedule or mock price fallback is enabled in production.

## Verification

```sh
pip install pytest
python -m pytest -q
```

Tests cover time-aware Ogbuefi greetings, graphite styling, three-workspace navigation, hidden feed diagnostics, no unnecessary event/weather fetching in Earnings & Fuel, remaining-window and next-step boundaries, gas-price/profit math, NWS/events, officially confirmed kickoff updates, chronological roadmaps and offline Streamlit rendering. Smart-game Saturday 3:30 PM split shifts, preference overrides, TBD/away fallback, early-kickoff behavior and toolbar config are regression-tested.
