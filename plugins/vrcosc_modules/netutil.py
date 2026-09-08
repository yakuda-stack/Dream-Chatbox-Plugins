"""netutil.py - tiny HTTP helper on top of urllib.

The suite must not pull in `requests`; the chatbox ships with nothing but
the stdlib and a plugin has no business changing that. Everything here
returns plain data and raises normal exceptions - the Poller around each
module catches them.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "OSC-DreamChatbox/vrcosc_modules"
MAX_BYTES = 2 * 1024 * 1024        # nobody needs a 50 MB response here


def request(url, method="GET", headers=None, data=None, timeout=8.0,
            json_body=None):
    """Returns (status, text). Non-2xx comes back as a status, not an
    exception - a 404 is information, not a crash."""
    url = str(url or "").strip()
    if not url:
        raise ValueError("no url")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    head = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    head.update(headers or {})
    body = data
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        head.setdefault("Content-Type", "application/json")
    if isinstance(body, str):
        body = body.encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=head, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES)
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read(MAX_BYTES) if hasattr(e, "read") else b""
        return e.code, raw.decode("utf-8", errors="replace")


def get_json(url, headers=None, timeout=8.0):
    status, text = request(url, headers=headers, timeout=timeout)
    if status < 200 or status >= 300:
        raise RuntimeError(f"HTTP {status}")
    return json.loads(text)


def post_json(url, payload, headers=None, timeout=8.0):
    return request(url, method="POST", headers=headers,
                   json_body=payload, timeout=timeout)


def encode(params):
    return urllib.parse.urlencode(params)
