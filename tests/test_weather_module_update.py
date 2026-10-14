"""Exercise an older imported module surviving a Streamlit code update."""
from datetime import date, datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import sys

from streamlit.testing.v1 import AppTest

import app
import weather_outlook


def test_stale_import_recovers_from_current_module_on_disk(monkeypatch):
    stale = ModuleType("weather_outlook")
    stale.summarize_weather_outlook = weather_outlook.summarize_weather_outlook
    monkeypatch.setitem(sys.modules, "weather_outlook", stale)
    monkeypatch.setattr(app, "weather_ui", stale)
    assert not hasattr(stale, "weather_for_work")
    day = date(2026, 10, 16)
    result = app.work_weather_for_plan({"places": []},
        app.generate_schedule([], app.BASE, 4), day,
        datetime(2026, 10, 14, tzinfo=app.TZ))
    assert result["window_label"] == "Fri 3:00 PM–12:00 AM"
    assert callable(stale.weather_for_work)


def test_current_module_is_not_reloaded():
    with patch.object(app.importlib, "reload") as reload_module:
        app.work_weather_for_plan({"places": []}, [], date(2026, 10, 16),
                                  datetime(2026, 10, 14, tzinfo=app.TZ))
        reload_module.assert_not_called()


def test_incomplete_update_renders_dashboard_without_using_old_forecast(monkeypatch):
    stale = SimpleNamespace(summarize_weather_outlook=weather_outlook.summarize_weather_outlook)
    monkeypatch.setattr(app, "weather_ui", stale)
    with patch.object(app.importlib, "reload", side_effect=ImportError("update in progress")):
        at = AppTest.from_string(
            "import app\nfrom datetime import datetime\n"
            "now=datetime(2026,10,16,16,tzinfo=app.TZ)\n"
            "app.render_drive_workspace(now.date(),now,{'events':[],'coverage':[]},"
            "{'places':[{'place':'Ann Arbor','status':'Available',"
            "'periods':[{'shortForecast':'OLD OVERNIGHT STORM','temperature':50,"
            "'temperatureUnit':'F'}]}],'hazards':['old storm']},app.BASE)",
            default_timeout=25).run()
        assert not at.exception
        rendered = " ".join(item.value for item in at.markdown)
        assert "Work-hour weather is updating" in rendered
        assert "pilot-weather-unknown" in rendered
        assert "OLD OVERNIGHT STORM" not in rendered
        assert "Dinner window starts at 5:00 PM" in rendered
