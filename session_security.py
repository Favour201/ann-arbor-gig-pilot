"""Session-scoped access controls for private Gig Pilot financial data.

Community Cloud private-app permissions are the primary external access control.
This passphrase gate adds owner-only protection inside an authorized app session;
it is NOT a distributed login rate limiter or a replacement for private hosting.
"""
import hashlib
import hmac
import time

PRIVATE_SESSION_KEY = "_pilot_private_session"
IDLE_SECONDS = 30 * 60
ABSOLUTE_SECONDS = 8 * 60 * 60
SENSITIVE_STATE_KEYS = (
    "sheet_shift_rows",
    "unsynced_shift_rows",
    "sheet_history_error",
    "journal_sheet_records",
    "journal_pending_records",
    "journal_sheet_error",
    "cached_gridwise_archive",
    "gridwise_archive_files",
    "confirm_gridwise_archive",
    "restore_csv",
)


def password_is_configured(password):
    return isinstance(password, str) and len(password) >= 12


def _fingerprint(password):
    """Only retained in Streamlit's server-side session; never rendered."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def authorize(state, password, *, now=None):
    """Start a fresh owner session only after a caller verified the password."""
    if not password_is_configured(password):
        raise ValueError("Owner passphrase must be configured")
    now = time.time() if now is None else float(now)
    state[PRIVATE_SESSION_KEY] = {
        "fingerprint": _fingerprint(password),
        "created_at": now,
        "last_active_at": now,
    }
    # Legacy boolean is never sufficient to read private records.
    state.pop("driver_authenticated", None)


def valid(state, password, *, now=None, touch=True):
    if not password_is_configured(password):
        return False
    ticket = state.get(PRIVATE_SESSION_KEY)
    if not isinstance(ticket, dict):
        return False
    now = time.time() if now is None else float(now)
    try:
        created = float(ticket["created_at"])
        last = float(ticket["last_active_at"])
        fingerprint = str(ticket["fingerprint"])
    except (KeyError, ValueError, TypeError):
        return False
    if not hmac.compare_digest(fingerprint, _fingerprint(password)):
        return False  # Invalidates existing sessions when password changes.
    if created > now or last > now:
        return False
    if now - created >= ABSOLUTE_SECONDS or now - last >= IDLE_SECONDS:
        return False
    if touch:
        ticket["last_active_at"] = now
    return True


def lock(state, *, clear_data=True):
    """Revoke owner authorization. Explicit logout also clears cached rows."""
    state.pop(PRIVATE_SESSION_KEY, None)
    state.pop("driver_authenticated", None)
    if clear_data:
        for key in SENSITIVE_STATE_KEYS:
            state.pop(key, None)


def needs_relock(state, password, *, now=None):
    """Return true only when a previously authorized session has expired."""
    return PRIVATE_SESSION_KEY in state and not valid(state, password, now=now, touch=False)
