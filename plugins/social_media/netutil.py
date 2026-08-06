"""Tiny HTTP helper for the Social Media plugin.

Pure stdlib on purpose: the plugin has to work in the AppImage, in the
AUR package and in a plain venv, so it must not add a dependency the
host app does not already ship.

Only two things ever leave the machine: the one-time OAuth token
exchange with discord.com and the guild-name lookup that goes with it.
Everything else is either typed in by the user or read from the local
Discord socket.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "OSC-DreamChatbox-social_media/1.0 (+github.com/yakuda-stack)"
TIMEOUT = 8


class HttpError(Exception):
    """Non-2xx answer. Keeps the body around – Discord puts its real
    error message in there ('invalid_grant', 'invalid_client' …)."""

    def __init__(self, status, body=""):
        super().__init__(f"HTTP {status}: {body[:200]}" if body
                         else f"HTTP {status}")
        self.status = status
        self.body = body


def request_text(url, headers=None, data=None, timeout=TIMEOUT):
    """GET (or POST when data is given) returning decoded text."""
    head = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
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
