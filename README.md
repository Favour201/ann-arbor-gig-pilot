# Washtenaw Gig Pilot Pro

Mobile-first Streamlit driver dashboard for a 16 MPG Kia Telluride in the Ann Arbor / Ypsilanti corridor. Application logic stays in `app.py`.

## Run

Python 3.10+:

```sh
pip install -r requirements.txt
streamlit run app.py
```

## Live systems and actual coverage

- **Fuel:** one-tap GasBuddy search links for Ann Arbor and Ypsilanti. GasBuddy's own app/site handles nearby station discovery, prices, and location permissions; the dashboard does **not** scrape GasBuddy or claim to know live station prices. Check report age, grade, payment restrictions, and club membership. Enter the regular-gas price you checked into the single dashboard number field. This value is retained during the current Streamlit session only, is never silently invented, and drives the Weekly Profit Grader at 16 MPG.
- **Events:** official Michigan and EMU athletics schedule parsers filter home games and local venues, retain kickoff minutes and TBD times, and exclude canceled/postponed events. Michigan's public calendar adds campus / Hill Auditorium coverage. Configurable JSON, iCalendar (including recurring events), and JSON-LD adapters support Michigan Theater, neighborhood and high-school sources. Those additional feeds are **not preconnected**. Source failures are visible; no-match results never imply clear traffic. Published athletics seasons limit date coverage.
- **Weather:** resolves both Ann Arbor and Ypsilanti NWS `/points` grids, retrieves forecasts and intersects forecast periods with the selected Eastern-time calendar date, including overnight periods. Rain, storms, snow/ice or temperature ≤15°F adds an amber **Weather Premium Surge Warning** in the roadmap. That is an app planning label, not an official weather alert or guaranteed rideshare surge. Dates beyond the forecast horizon and outages display an unknown state.
- **Profit:** uses only the gas price the driver enters. No price means the grader stays disabled. Results recalculate when your input changes. Earnings after fuel exclude tax, wear, insurance and depreciation.

Weather cache: 15 minutes. Events: 30 minutes. Requests are bounded and independent sources run concurrently. Refresh happens on rerun/interaction or via **Refresh live sources**, not through a background scheduler. GasBuddy opens externally and is not part of the refresh cache.

## Check local gas prices

Use the on-page GasBuddy links to search in Ann Arbor or Ypsilanti. GasBuddy may permit a device-location search from its app or website, but the preset links themselves are city-based rather than exact home-address coordinates. The prefilled search parameters are convenience links; if GasBuddy ignores the search, enter a city or ZIP code there. GasBuddy is a third-party community-reported source and may show stale or incorrect prices. **Only the price you type manually** is used for calculations. No external browser-storage plugin or API key is needed.

## Connect additional event calendars

Set `EVENT_FEEDS_JSON` to a JSON array. Only use feeds you are authorized to consume. Example Streamlit secrets:

```toml
EVENT_FEEDS_JSON = '''[
  {"name":"Michigan Theater","format":"json","url":"https://your-provider.example/theater-events"},
  {"name":"Local school athletics","format":"ics","url":"https://your-provider.example/school.ics"}
]'''
```

Replace the example URLs with actual feed URLs. `jsonld` supports published schema.org event data in HTML. JSON format:

```json
{"events":[{"name":"Concert","venue":"Michigan Theater, Ann Arbor","start":"2026-10-17T19:30:00-04:00","end":"2026-10-17T21:30:00-04:00","url":"https://your-provider.example/concert"}]}
```

Use a date-only `start` for TBD/all-day events. Unknown end times remain unknown. JSON rows may set `status` to `cancelled` or `postponed`. Local venues are required; remote events are excluded. Each selected date is a separate cache key. No fake event schedule or mock price fallback is enabled in production.

## Verification

```sh
pip install pytest
python -m pytest -q
```

Tests cover manual-price-to-grader behavior, GasBuddy link construction, NWS date boundaries/cold conversion, home-game schedules, canceled and recurring calendars, Thursday roadmap retention, profit math and offline Streamlit rendering.
