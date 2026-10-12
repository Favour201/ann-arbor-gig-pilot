# Washtenaw Gig Pilot Pro

Mobile-first Streamlit driver dashboard for a 16 MPG Kia Telluride in the Ann Arbor / Ypsilanti corridor. Application logic stays in `app.py`.

## Run

Python 3.10+:

```sh
pip install -r requirements.txt
streamlit run app.py
```

## Live systems and actual coverage

- **Fuel:** unreliable public station-price scraping is disabled (including the suspect Costco quote). BP at 3031 Washtenaw, Costco at 771 Airport, Sam’s Club at 5450 Carpenter and other corridor stations are maintained for easy manual checks. Enter a checked regular unleaded price in the dashboard; the optional browser-local log stores its timestamp and your paid/observed flag. Driver entries are **unverified**, and both entries and provider prices expire from the active ranking after 24 hours. A connected timestamped fuel provider can still update prices automatically. Without a dated price the profit grader asks for a manual override. Detour comparison subtracts extra fuel burned at 16 MPG (time, depreciation and maintenance excluded). No dependable free, working US station-level API was verified.
- **Events:** official Michigan and EMU athletics schedule parsers filter home games and local venues, retain kickoff minutes and TBD times, and exclude canceled/postponed events. Michigan's public calendar adds campus / Hill Auditorium coverage. Configurable JSON, iCalendar (including recurring events), and JSON-LD adapters support Michigan Theater, neighborhood and high-school sources. Those additional feeds are **not preconnected**. Source failures are visible; no-match results never imply clear traffic. Published athletics seasons limit date coverage.
- **Weather:** resolves both Ann Arbor and Ypsilanti NWS `/points` grids, retrieves forecasts and intersects forecast periods with the selected Eastern-time calendar date, including overnight periods. Rain, storms, snow/ice or temperature ≤15°F adds an amber **Weather Premium Surge Warning** in the roadmap. That is an app planning label, not an official weather alert or guaranteed rideshare surge. Dates beyond the forecast horizon and outages display an unknown state.
- **Profit:** uses the lowest current (≤24 hours old) regular quote across the returned stations, including membership stations clearly labeled. Optional actual-paid-price override. Results recalculate when the live price changes. Earnings after fuel exclude tax, wear, insurance and depreciation.

Fuel/weather cache: 15 minutes. Driver price history is scoped to the browser (local storage best effort, otherwise current session) and is not shared across devices. Events: 30 minutes. Requests are bounded and independent sources run concurrently. Refresh happens on rerun/interaction or via **Refresh live sources**, not through a background scheduler.

## Connect a fuel provider

Set these environment variables or Streamlit secrets (never commit credentials):

```toml
FUEL_FEED_URL = "https://your-provider.example/normalized-prices"
FUEL_FEED_TOKEN = "your-optional-token"
```

This is a normalized adapter contract, **not** an existing vendor endpoint. A vendor-specific adapter must map its response to this shape. The request sends `latitude=42.256`, `longitude=-83.680` and the selected `radius_miles` (5–30). A fixed-response endpoint also works; additional stations are radius-filtered locally.

```json
{
  "prices": [
    {
      "station_id": "bp_washtenaw",
      "regular": 3.49,
      "observed_at": "2026-10-12T14:00:00-04:00",
      "currency": "USD",
      "unit": "gallon"
    },
    {
      "station_id": "provider-station-id",
      "name": "Another station",
      "address": "Example address, Ann Arbor",
      "latitude": 42.26,
      "longitude": -83.68,
      "member": false,
      "regular": 3.59,
      "observed_at": "2026-10-12T14:00:00-04:00"
    }
  ]
}
```

Example values are documentation fixtures, not current prices. Priority IDs: `bp_washtenaw`, `sams_carpenter`, `costco_airport`, `bp_east`, `marathon_washtenaw`. Those fixed corridor stops remain in the matrix. Additional stations require coordinates, name and address. Timestamp must include a timezone; old, future, nonfinite, wrong-currency/unit and out-of-radius reports are rejected. The newest quote wins for duplicate station IDs. Prices are USD per US gallon for regular unleaded; do not map regional averages or premium prices into this feed.

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

Tests cover fuel validation and regional expansion, NWS date boundaries/horizon/cold conversion, local home schedules, canceled and recurring calendars, Thursday roadmap retention, profit math and offline Streamlit rendering.
