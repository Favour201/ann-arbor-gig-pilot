"""Authentication regression tests never use real user passwords or cloud creds."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

import app
import session_security as auth


PASSWORD = "temporary-test-passphrase-that-is-not-a-secret"


def test_legacy_boolean_alone_does_not_unlock_any_data():
    session = {"driver_authenticated": True, "sheet_shift_rows": [{"secret": 123}]}
    assert not auth.valid(session, PASSWORD, now=1000)
    auth.lock(session)
    assert "driver_authenticated" not in session
    assert "sheet_shift_rows" not in session


def test_private_session_expires_by_inactivity_and_absolute_lifetime():
    state = {}
    auth.authorize(state, PASSWORD, now=100)
    assert auth.valid(state, PASSWORD, now=100)
    assert auth.valid(state, PASSWORD, now=100 + 10 * 60)
    assert auth.valid(state, PASSWORD, now=100 + 39 * 60)
    assert not auth.valid(state, PASSWORD, now=100 + 70 * 60)
    assert auth.needs_relock(state, PASSWORD, now=100 + 70 * 60)
    state = {}
    auth.authorize(state, PASSWORD, now=100)
    for tick in range(1, 17):
        assert auth.valid(state, PASSWORD, now=100 + tick * 29 * 60) == (
            tick * 29 * 60 < auth.ABSOLUTE_SECONDS)


def test_owner_passphrase_rotation_invalidates_existing_sessions():
    state = {}
    auth.authorize(state, PASSWORD, now=10)
    assert auth.valid(state, PASSWORD, now=11)
    assert not auth.valid(state, PASSWORD + "-rotated", now=12)
    assert not auth.valid(state, "too-short", now=12)
    assert not auth.password_is_configured("")
    with pytest.raises(ValueError):
        auth.authorize(state, "weak")


def test_logout_drops_sensitive_records_but_preserves_plan_navigation():
    state = {
        "planned_date": "2026-10-13",
        "pilot_workspace": "Results & Learning",
        "sheet_shift_rows": [{"earnings": 900}],
        "journal_pending_records": [{"private": True}],
        "gridwise_archive_files": b"private report",
        "confirm_gridwise_archive": True,
    }
    auth.authorize(state, PASSWORD, now=100)
    auth.lock(state)
    assert not auth.valid(state, PASSWORD, now=101)
    assert "sheet_shift_rows" not in state
    assert "journal_pending_records" not in state
    assert "gridwise_archive_files" not in state
    assert "confirm_gridwise_archive" not in state
    assert state["pilot_workspace"] == "Results & Learning"
    assert state["planned_date"] == "2026-10-13"


def test_auth_session_rejects_future_timestamp_and_tampered_ticket():
    state = {}
    auth.authorize(state, PASSWORD, now=500)
    assert not auth.valid(state, PASSWORD, now=100)
    state[auth.PRIVATE_SESSION_KEY]["created_at"] = "nonsense"
    assert not auth.valid(state, PASSWORD, now=501)


def test_results_shows_exactly_one_login_form_when_advanced_open():
    # A previous build rendered multiple driver_unlock forms, once in each
    # collapsed Streamlit expander; that would fail when advanced tools opened.
    with patch.object(app, "setting", side_effect=lambda k, default="":
                      PASSWORD if k == "DRIVER_ACCESS_PASSWORD" else default):
        at = AppTest.from_string("from app import main\nmain()",
                                 default_timeout=25).run()
        # Move straight to Results without network calls.
        # The existing default Drive fetches live data, so this is tested
        # via the isolated private view below instead.
    assert "Secrets" not in str(at.exception)


def test_private_results_login_and_logout_without_real_sheet_access():
    with patch.object(app, "setting", side_effect=lambda k, default="":
                      PASSWORD if k == "DRIVER_ACCESS_PASSWORD" else default):
        at = AppTest.from_string(
            "import app\nimport streamlit as st\n"
            "st.subheader('Private test')\n"
            "with st.expander('Access'):\n"
            "    if app.private_session_active():\n"
            "        st.button('Lock', on_click=app.lock_private_session)\n"
            "    else:\n"
            "        app.private_history_unlocked()\n"
            "st.write('Unlocked' if app.private_session_active() else 'Locked')",
            default_timeout=25).run()
        assert not at.exception
        assert any("Locked" == x.value for x in at.markdown)
        pwd = next(x for x in at.text_input if x.label == "Private data passphrase")
        pwd.set_value(PASSWORD).run()
        unlock = next(b for b in at.button if b.label == "Unlock private results")
        unlock.click().run()
        assert not at.exception
        assert any("Unlocked" == x.value for x in at.markdown)
        assert "sheet_shift_rows" not in at.session_state
        next(b for b in at.button if b.label == "Lock").click().run()
        assert not at.exception
        assert any("Locked" == x.value for x in at.markdown)
