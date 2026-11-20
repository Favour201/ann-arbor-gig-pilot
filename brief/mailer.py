"""Send the brief by email through Gmail SMTP (second channel next to ntfy).

ntfy's iPhone app sometimes files a push without ever alerting, so the brief
also goes to an inbox. Configured by three repository secrets:

    GMAIL_ADDRESS       sending Gmail account (ideally a separate one for the bot)
    GMAIL_APP_PASSWORD  16-character app password for that account (needs 2-Step Verification)
    BRIEF_EMAIL_TO      where the brief goes

None set: email is simply off. Some set: that's a misconfiguration and is reported.
Errors never include the addresses or the password.
"""
from email.message import EmailMessage
import smtplib

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # implicit TLS
SECRETS = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "BRIEF_EMAIL_TO")


class EmailError(RuntimeError):
    """Raised with a message that never includes addresses or the password."""


def email_state(env):
    """'off' (nothing set), 'ready' (all set) or 'partial'."""
    present = [bool((env.get(name) or "").strip()) for name in SECRETS]
    if not any(present):
        return "off"
    return "ready" if all(present) else "partial"


def missing_secrets(env):
    return [name for name in SECRETS if not (env.get(name) or "").strip()]


def build_message(push, sender, to, notes=()):
    msg = EmailMessage()
    msg["Subject"] = push["title"]
    msg["From"] = f"Gig Pilot <{sender}>"
    msg["To"] = to
    parts = [push["message"]]
    if notes:
        parts.append("\n".join(f"⚠ {note}" for note in notes))
    if push.get("click"):
        parts.append(f"Full plan: {push['click']}")
    msg.set_content("\n\n".join(parts) + "\n")
    return msg


def send_email(push, env, notes=(), *, smtp=smtplib.SMTP_SSL):
    state = email_state(env)
    if state != "ready":
        raise EmailError("email not fully configured (missing " + ", ".join(missing_secrets(env)) + ")")
    sender = env["GMAIL_ADDRESS"].strip()
    password = env["GMAIL_APP_PASSWORD"].replace(" ", "").strip()  # Google shows it in groups of 4
    msg = build_message(push, sender, env["BRIEF_EMAIL_TO"].strip(), notes)
    try:
        with smtp(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.login(sender, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise EmailError("Gmail rejected the login (check GMAIL_ADDRESS / GMAIL_APP_PASSWORD)") from None
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"email send failed: {type(exc).__name__}") from None
