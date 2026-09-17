"""Optional success ping to an independently hosted dead-man monitor."""

import os
from urllib.parse import urlparse

import requests


def ping_heartbeat() -> bool | None:
    url = os.environ.get("TRACKER_HEARTBEAT_URL", "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        print("Heartbeat: FAILED (configure an HTTPS endpoint without URL credentials)")
        return False
    try:
        response = requests.get(url, timeout=10, allow_redirects=False)
        if not 200 <= response.status_code < 300:
            print("Heartbeat: FAILED (non-success response)")
            return False
    except requests.RequestException:
        # Ping URLs commonly contain secret tokens. Never log the URL or exception.
        print("Heartbeat: FAILED (network error)")
        return False
    print("Heartbeat: SENT")
    return True
