# Washtenaw Gig Pilot Pro

Mobile-first Streamlit driver dashboard for Favour (Ogbuefi), operating a 16 MPG Kia Telluride in the Ann Arbor / Ypsilanti corridor. Application logic stays in `app.py`.

## Run

Python 3.10+:

```sh
pip install -r requirements.txt
streamlit run app.py
```

## Live systems and actual coverage

- **Fuel:** one-tap GasBuddy search links for Ann Arbor and Ypsilanti. GasBuddy's own app/site handles nearby station discovery, prices, and location permissions; the dashboard does **not** scrape GasBuddy or claim to know live station prices. Check report age, grade, payment restrictions, and club membership. Enter the regular-gas price you checked into the single dashboard number field. This value is retained during the current Streamlit session only, is never silently invented, and drives the Weekly Profit Grader at 16 MPG.
- **Events:** official Michigan/EMU athletics and U-M campus/Hill feeds remain active. Additional built-in readers now check the official Marquee Arts Michigan Theater live-events page, Pioneer Athletics (Eventlink), and calendars for Pioneer, Huron and Skyline high schools, with 30-minute caching. These school and theater listings are **best-effort public HTML readers, not guaranteed JSON/iCalendar APIs**. Some pages show only the current month or block server requests; the source coverage table marks these *Not verified*, and event links let you check manually. The dashboard never assumes an unverified feed means no events. Extra JSON/ICS/JSON-LD feeds still work through optional `EVENT_FEEDS_JSON` settings.
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

Tests cover manual-price-to-grader behavior, GasBuddy links, NWS date boundaries/cold conversion, Michigan/EMU schedules, best-effort Marquee Arts/Pioneer athletics/AAPS calendar readers, canceled and recurring calendars, Thursday roadmap retention, profit math and offline Streamlit rendering.
