"""Tiny HTTP helper for the Stream Stats plugin.

Pure stdlib on purpose: the plugin has to work in the AppImage, in the
AUR package and in a plain venv, so it must not add a dependency the
host app does not already ship.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import urllib.error
import urllib.parse
import urllib.request

# YouTube serves a very different page to an unknown client, so we ask
# for the desktop HTML the way a browser would.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 "
              "OSC-DreamChatbox-stream_stats")
TIMEOUT = 8


class HttpError(Exception):
    """Non-2xx answer. Keeps the body around – the APIs we talk to put
    their real error message in there."""

    def __init__(self, status, body=""):
        super().__init__(f"HTTP {status}: {body[:200]}" if body
                         else f"HTTP {status}")
        self.status = status
        self.body = body


def build_url(base, **params):
    """base + query string, skipping empty values."""
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    if not clean:
        return base
    sep = "&" if "?" in base else "?"
    return base + sep + urllib.parse.urlencode(clean)


def request_text(url, headers=None, data=None, timeout=TIMEOUT):
    """GET (or POST when data is given) returning decoded text."""
    head = {"User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            # skips the EU consent interstitial on youtube.com
            "Cookie": "CONSENT=YES+1"}
    if headers:
        head.update(headers)
    body = None
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode("utf-8")
        head.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif isinstance(data, (bytes, bytearray)):
        body = bytes(data)

    req = urllib.request.Request(url, data=body, headers=head)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        raise HttpError(e.code, detail)
    return raw.decode("utf-8", "replace")


def request_json(url, headers=None, data=None, timeout=TIMEOUT):
    head = {"Accept": "application/json"}
    if headers:
        head.update(headers)
    text = request_text(url, head, data, timeout)
    try:
        return json.loads(text)
    except Exception as e:
        raise HttpError(200, f"answer was not JSON ({e})")


def human_since(started_iso):
    """'2026-08-03T10:00:00Z' -> '2h 14m'. Empty string when unparsable –
    an uptime is a nice-to-have, never a reason to fail a poll."""
    import time
    from datetime import datetime, timezone
    text = str(started_iso or "").strip()
    if not text:
        return ""
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        start = datetime.fromisoformat(text)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        secs = int(time.time() - start.timestamp())
    except Exception:
        return ""
    if secs < 0:
        return ""
    hours, rest = divmod(secs, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
