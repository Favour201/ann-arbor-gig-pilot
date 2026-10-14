"""Michigan NWS forecast-to-driving-outlook regressions (synthetic forecasts)."""
from datetime import datetime, timezone

from streamlit.testing.v1 import AppTest

import weather_outlook as outlook


def period(text, temp=60, unit="F", name="Friday evening"):
    return {"name": name, "shortForecast": text,
            "temperature": temp, "temperatureUnit": unit}


def weather(*forecasts, second=None):
    places = [{"place": "Ann Arbor", "periods": list(forecasts),
               "status": "Available" if forecasts else "NWS unavailable",
               "url": "https://api.weather.gov/gridpoints/DTX/1,1/forecast"}]
    if second is not None:
        places.append(second)
    return {"places": places, "hazards": [],
            "checked": datetime(2026, 10, 14, tzinfo=timezone.utc)}


def test_clear_day_is_green_with_forecast_temperature_and_range():
    result = outlook.summarize_weather_outlook(weather(
        period("Sunny", 63, name="Friday"),
        period("Mostly clear", 41, name="Friday Night"),
    ))
    assert result["severity"] == "good"
    assert result["temperature"] == 63
    assert result["high"] == 63 and result["low"] == 41
    assert result["pill"] == "LOW IMPACT"
    assert "guarantee" in result["note"]


def test_light_snow_michigan_winter_caution_not_always_red():
    result = outlook.summarize_weather_outlook(weather(
        period("Light Snow Showers", 34),
        period("Mostly cloudy", 25, name="Friday Night"),
    ))
    assert result["severity"] == "caution"
    assert result["icon"] == "🌨️"
    assert "slick roads" in result["note"]
    assert result["high"] == 34 and result["low"] == 25


def test_freezing_rain_ice_and_heavy_snow_are_high_impact():
    for forecast in ("Freezing Rain", "Ice Storm", "Black ice expected",
                     "Heavy Snow", "Blizzard Warning", "Snow squall"):
        result = outlook.summarize_weather_outlook(weather(period(forecast, 31)))
        assert result["severity"] == "high", forecast
        assert result["pill"] == "HIGH IMPACT"
        assert result["title"] == "Weather impact alert"


def test_thunderstorms_heavy_rain_and_extreme_cold_are_high_impact():
    for desc, temp in [
        ("Chance of Thunderstorms", 75),
        ("Heavy rain expected", 64),
        ("Cloudy", 9),
        ("Tornado possible", 70),
    ]:
        result = outlook.summarize_weather_outlook(weather(period(desc, temp)))
        assert result["severity"] == "high", desc


def test_regular_rain_breezy_or_cold_are_caution_not_surge_claim():
    for desc, temp in [("Scattered rain showers", 58),
                       ("Breezy", 70),
                       ("Mostly sunny", 29)]:
        result = outlook.summarize_weather_outlook(weather(period(desc, temp)))
        assert result["severity"] == "caution"
        assert "surge" not in result["note"].lower()
        assert "guarantee" not in result["forecast"].lower()


def test_celsius_conversion_and_invalid_temperatures():
    sample = period("Sunny", 0, unit="C")
    assert outlook.temperature_f(sample) == 32
    assert outlook.temperature_f(period("Cloudy", 30, unit="K")) is None
    assert outlook.temperature_f(period("Cloudy", float("nan"))) is None
    assert outlook.temperature_f(period("Cloudy", True)) is None
    r = outlook.summarize_weather_outlook(weather(sample))
    assert r["temperature"] == 32
    assert r["severity"] == "caution"


def test_missing_forecast_is_gray_not_misleading_green():
    result = outlook.summarize_weather_outlook({
        "places": [{"place": "Ann Arbor", "status": "NWS unavailable",
                    "periods": []},
                   {"place": "Ypsilanti",
                    "status": "Selected date outside available forecast",
                    "periods": []}],
        "hazards": [],
    })
    assert result["severity"] == "unknown"
    assert result["temperature"] is None
    assert "unverified" in result["note"]


def test_partial_city_coverage_does_not_claim_all_clear():
    missing = {"place": "Ypsilanti", "status": "NWS unavailable", "periods": []}
    no_risk = outlook.summarize_weather_outlook(weather(period("Clear", 66), second=missing))
    assert no_risk["severity"] == "unknown"
    assert no_risk["partial_coverage"]
    high_risk = outlook.summarize_weather_outlook(weather(
        period("Freezing drizzle", 28), second=missing,
    ))
    assert high_risk["severity"] == "high"
    assert high_risk["partial_coverage"]
    assert "unavailable" in high_risk["note"]


def test_riskiest_local_place_is_used_for_headline_even_when_ann_arbor_clear():
    ypsi = {"place": "Ypsilanti", "status": "Available",
            "periods": [period("Thunderstorms likely", 59)]}
    result = outlook.summarize_weather_outlook(weather(period("Sunny", 65), second=ypsi))
    assert result["severity"] == "high"
    assert result["place"] == "Ypsilanti"
    assert "Thunderstorms" in result["forecast"]


def test_graphite_weather_render_includes_accessible_status_and_escaped_forecast():
    at = AppTest.from_string(
        "from app import render_weather\n"
        "render_weather({'places': [{'place': 'Ann Arbor', 'status': 'Available', "
        "'periods': [{'shortForecast': '<script>alert(1)</script> Thunderstorms', "
        "'name': 'Friday', 'temperature': 61, 'temperatureUnit': 'F'},"
        "{'shortForecast': 'Cloudy', 'name':'Friday Night',"
        "'temperature':45, 'temperatureUnit':'F'}], 'url':''}],"
        "'hazards':['Storms']}, details=False)",
        default_timeout=25,
    ).run()
    assert not at.exception
    html = " ".join(markdown.value for markdown in at.markdown)
    assert "pilot-weather-high" in html
    assert "WEATHER DRIVING OUTLOOK" in html
    assert "Forecast 61°F" in html
    assert "high 61°" in html
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_graphite_theme_has_four_subtle_weather_states():
    from app import MIDNIGHT_CSS
    for name in ("good", "caution", "high", "unknown"):
        assert ".pilot-weather-" + name in MIDNIGHT_CSS
    assert "#f17078" in MIDNIGHT_CSS
    assert "Weather Premium Surge Warning" not in MIDNIGHT_CSS
