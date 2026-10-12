# Washtenaw Gig Pilot Pro

Graphite Pilot is a mobile-first Streamlit driver command center for Ogbuefi, operating a 16 MPG Kia Telluride in the Ann Arbor / Ypsilanti corridor. Application logic stays in `app.py`.

## Run

Python 3.10+:

```sh
pip install -r requirements.txt
streamlit run app.py
```

## Smart Michigan game-day split shifts

For a **confirmed Michigan Stadium football kickoff on Saturday or Sunday**, the roadmap automatically recommends a practical **pregame drive → home rest → return** pattern instead of showing only one broad pregame crowd window. By default, a **3:30 PM** kickoff suggests driving **12:30–2:45 PM**, taking a break **2:45–5:30 PM**, and returning **5:30 PM onward** (subject to real demand, traffic, road closures and personal preference). Use the existing *Roadmap filters & game-day preferences* expander to change the stop buffer to **30 / 45 / 60 minutes**, or the return delay to **1–4 hours after kickoff**. For noon kickoffs, the return block will not begin before 3 PM.

The three recommendations are clearly identified as optional and are chronological. They replace the generic Saturday/Sunday baseline to avoid contradictory overlap, but other independent local event alerts remain visible. The game stays **Kickoff TBD** until its official athletics schedule publishes a usable clock time; we do not fabricate split-shift timings for unknown or away games. The plan does not predict surge or override unsafe weather/road conditions.

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
- **Earnings & Fuel:** GasBuddy Ann Arbor/Ypsilanti links, manual checked regular
  fuel price and Weekly Profit Grader at 16 MPG. This view deliberately does not
  call the live event or weather feeds, making finance work simpler.

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
