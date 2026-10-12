import json
from datetime import date, datetime, timedelta
from unittest.mock import patch
import pytest
import app

NOW = datetime(2026, 10, 12, 10, tzinfo=app.TZ)


def quote(sid='bp_washtenaw', **kw):
    return dict(station_id=sid, regular=3.5, observed_at=NOW.isoformat(), **kw)


def test_fuel_freshness_currency_and_latest():
    rows = [quote(), dict(quote(), regular=2, observed_at=(NOW-timedelta(days=2)).isoformat()),
            dict(quote('costco_airport'), currency='CAD'),
            dict(quote('bp_east'), observed_at='2026-10-12T10:00:00'),
            dict(quote('marathon_washtenaw'), regular=float('nan'))]
    data = app.parse_fuel_feed({'prices': rows}, NOW)
    assert list(data) == ['bp_washtenaw']
    assert data['bp_washtenaw']['price'] == 3.5


def test_fuel_expansion_and_radius():
    near = quote('new', name='New', address='Nearby', latitude=42.256, longitude=-83.68)
    far = quote('far', name='Far', address='Detroit', latitude=42.33, longitude=-83.05)
    data = app.parse_fuel_feed({'prices': [near, far]}, NOW, radius=5)
    assert list(data) == ['new']
    assert data['new']['name'] == 'New'


def test_public_price_never_uses_average():
    html = '<div class="gaspriceboxdetailp"><div class="oilpriceaveragehd">Regular</div><div class="stationprice">N/A</div></div><h4>Regular Gas</h4><div>$2.00</div>'
    assert app.parse_public_fuel(html) is None
    assert app.parse_public_fuel(html.replace('N/A', '$3.499')) == 3.499


def test_minimum_filters_expired_cache_entry():
    fresh = dict(id='bp', price=3.5, observed_at=NOW)
    old = dict(id='costco', price=2, observed_at=NOW-timedelta(hours=25))
    assert app.current_quotes({'stations': [old, fresh]}, NOW) == [fresh]


def test_weather_overnight_and_horizon():
    period = dict(startTime='2026-10-12T18:00:00-04:00', endTime='2026-10-13T06:00:00-04:00',
                  shortForecast='Rain and snow showers', temperature=-10, temperatureUnit='C')
    matched, hazards = app.weather_for_day([period], date(2026, 10, 13))
    assert len(matched) == 1
    assert {'Rain', 'Snow / ice', 'Extreme cold (≤15°F)'} <= set(hazards)
    assert app.weather_for_day([period], date(2026, 10, 14)) == ([], [])


def test_weather_midnight_exclusive():
    p = dict(startTime='2026-10-12T18:00:00-04:00', endTime='2026-10-13T00:00:00-04:00', shortForecast='Storms')
    assert app.weather_for_day([p], date(2026, 10, 13)) == ([], [])


def test_home_schedule_and_tbd():
    table = [None, {'date': 2, 'opponent': 3, 'location_indicator': 4, 'location': 5, 'time': 6, 'tbd': 7},
             '2026-10-17T15:30:00', {'title': 8}, 'H', 'Ann Arbor, Mich.', '3:30 PM', False, 'Penn State']
    html = '<script id="__NUXT_DATA__">'+json.dumps(table)+'</script>'
    events = app.parse_sidearm(html, date(2026, 10, 17), 'Michigan', 'football', 'https://mgoblue.com/')
    assert events[0]['tier'] == 'CRITICAL IMPACT'
    assert events[0]['start'].minute == 30
    table[4] = 'A'
    assert not app.parse_sidearm('<script id="__NUXT_DATA__">'+json.dumps(table)+'</script>', date(2026, 10, 17), 'Michigan', 'football', '')


def test_calendar_all_categories_and_cancellation():
    venues = ['Michigan Stadium', 'Rynearson Stadium', 'Crisler Center', 'Hill Auditorium', 'Pioneer High School']
    rows = [dict(name='Football' if i == 0 else 'Event', venue=v, start='2026-10-17T19:30:00-04:00') for i,v in enumerate(venues)]
    rows += [dict(rows[0], status='cancelled'), dict(rows[0], venue='Detroit')]
    events = app.parse_extra(json.dumps({'events': rows}), 'json', date(2026, 10, 17), 'Test', '')
    assert {e['tier'] for e in events} == set(app.TIERS)
    assert len(events) == 5


def test_recurring_ics():
    text = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTART;TZID=America/New_York:20261001T190000\r\nDTEND;TZID=America/New_York:20261001T210000\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:School athletics\r\nLOCATION:Pioneer High School\r\nEND:VEVENT\r\nEND:VCALENDAR'
    events = app.parse_extra(text, 'ics', date(2026, 10, 8), 'School', '')
    assert len(events) == 1
    assert events[0]['start'].day == 8


def test_thursday_keeps_football_and_minutes():
    ev = app.event_record('Michigan Football', 'Michigan Stadium', '2026-10-15T15:30:00-04:00', None, 'Test')
    slots = app.generate_schedule([ev], app.BASE, 3)
    assert any(s['time'] == '12:30 PM – 3:30 PM' for s in slots)
    assert any(s['app'] == 'XL / Comfort' for s in slots)


def test_profit():
    metrics = app.calculate_metrics(100, 4, 160, 3)
    assert metrics['fuel_cost'] == 30
    assert metrics['net_profit'] == 70
    assert app.calculate_metrics(100, 0, 20, 3) is None
    assert app.calculate_metrics(100, 4, 20, None) is None


def test_offline_ui_and_grader():
    from streamlit.testing.v1 import AppTest
    fuel = {'stations': [], 'notes': [], 'checked': NOW}
    events = {'events': [], 'notes': ['Additional feeds not connected'], 'coverage': [], 'checked': NOW}
    weather = {'places': [{'place':'Ann Arbor', 'status':'NWS unavailable', 'periods':[], 'hazards':[], 'url':''}], 'hazards':[], 'checked':NOW}
    with patch.object(app, 'fetch_live_fuel', return_value=fuel), patch.object(app, 'fetch_live_events', return_value=events), patch.object(app, 'fetch_weather', return_value=weather):
        at = AppTest.from_string('from app import main\nmain()', default_timeout=20).run()
        assert not at.exception
        assert next(b for b in at.button if b.label == 'Grade my week').disabled
        at.checkbox[0].check().run()
        next(b for b in at.button if b.label == 'Grade my week').click().run()
        assert not at.exception
        assert any('After-fuel earnings' in x.value for x in at.success)
