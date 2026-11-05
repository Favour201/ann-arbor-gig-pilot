"""Send a push through ntfy.sh.

Publishes as JSON to the server root, so the topic travels in the request body,
never in a URL that could end up in an error message or log. The topic itself
comes from the NTFY_TOPIC secret; on the free tier the topic name is the only
thing protecting the channel, so treat it like a password.
"""
import requests

NTFY_SERVER = "https://ntfy.sh"


class PushError(RuntimeError):
    """Raised with a message that never includes the topic."""


def send_ntfy(push, topic, *, server=NTFY_SERVER, post=requests.post):
    topic = (topic or "").strip()
    if not topic or "/" in topic or " " in topic:
        raise PushError("NTFY_TOPIC is missing or malformed (set it as a repository secret)")
    payload = {"topic": topic, "title": push["title"], "message": push["message"],
               "priority": push["priority"], "tags": push["tags"]}
    if push.get("click"):
        payload["click"] = push["click"]
    try:
        response = post(server, json=payload, timeout=(4, 10))
    except requests.RequestException as exc:
        raise PushError(f"ntfy request failed: {type(exc).__name__}") from None
    if response.status_code >= 300:
        raise PushError(f"ntfy answered HTTP {response.status_code}")
    return response.status_code
