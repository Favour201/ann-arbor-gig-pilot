# Washtenaw Gig Pilot Pro

Graphite Pilot is a mobile-first Streamlit driver command center for Ogbuefi, operating a 16 MPG Kia Telluride in the Ann Arbor / Ypsilanti corridor. Application logic stays in `app.py`.

## Run

Python 3.10+:

```sh
pip install -r requirements.txt
streamlit run app.py
```

## Graphite Pilot design

- **Theme:** premium monochrome graphite/near-black native Streamlit theme in `.streamlit/config.toml`, charcoal cards, warm-silver accents, clear keyboard-focus indicators and reduced-motion support. Amber remains exclusively for weather caution. CSS stays in `app.py` and requires no new dependency.
- **Greeting:** uses the Eastern local hour to display “Good morning, Ogbuefi”, “Good afternoon, Ogbuefi” or “Good evening, Ogbuefi”. The dashboard no longer uses another form of address.
- **First screen:** date selector and refresh button, followed by compact **checked gas**, **published events**, and **NWS hazard-trigger** cards. Unknown fuel/forecast states remain unknown—no prices or false clear-weather claims.
- **Roadmap first:** tap-friendly chronological timeline cards; details remain under “Routing details”. A segmented control lets you show **All / Driving / Traffic & events**, while **Show remaining windows only** removes finished windows on today’s date but retains active windows and untimed TBD events. A noncommittal **current / next listed window** card surfaces the earliest still-relevant timed window for the selected date. The major-event and school-traffic toggles remain independent, with school avoidance enabled by default.
- **Less clutter:** event lists and source diagnostics live in optional expanders below the roadmap. GasBuddy links and the manual fuel entry live in **Fuel quick check**. Profit grader remains in its own tab, with the same 16 MPG calculations.

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

Tests cover time-aware Ogbuefi greetings, graphite style markers, three roadmap views, remaining-window and up-next boundaries (including active and TBD events), compact timeline headings, gas-price and profit math, NWS and events, officially confirmed kickoff updates, chronological roadmaps and offline Streamlit rendering.
