"""TikTok numbers - EXPERIMENTAL.

TikTok has no public API for this, so the numbers come from the same
public pages the TikTokLive libraries read:

  profile   https://www.tiktok.com/@<name>
            the page carries a JSON block (__UNIVERSAL_DATA_FOR_
            REHYDRATION__) with followerCount and heartCount
  live      https://www.tiktok.com/api-live/user/room/?uniqueId=<name>
            liveRoom.status 2 = live, liveRoomStats.userCount = viewers

TikTok changes these pages and blocks clients it does not like without
notice. Everything here fails quietly: a placeholder that cannot be read
stays empty, the reason goes to the log once, and the next try is a few
minutes later - never a traceback, never a stalled chatbox.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import re
import time
from urllib.parse import quote

from .netutil import request_text

PROFILE_URL = "https://www.tiktok.com/@{name}"
ROOM_URL = ("https://www.tiktok.com/api-live/user/room/"
            "?aid=1988&sourceType=54&uniqueId={name}")
# TikTok hands a browser the page and everything else a captcha
BROWSER = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/128.0 Safari/537.36"),
           "Accept-Language": "en-US,en;q=0.9"}
PROFILE_EVERY = 300.0         # followers / likes move slowly
LIVE_EVERY = 30.0
RETRY_AFTER = 600.0           # blocked or changed: back off

_RE_DATA = re.compile(
    r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.S)


def parse_profile(html):
    """(followers, likes) from the profile page, (None, None) if the
    block is not there (captcha, changed page)."""
    m = _RE_DATA.search(html or "")
    if not m:
        return None, None
    try:
        data = json.loads(m.group(1))
        stats = (data["__DEFAULT_SCOPE__"]["webapp.user-detail"]
                 ["userInfo"]["stats"])
    except (ValueError, KeyError, TypeError):
        return None, None
    followers = stats.get("followerCount")
    likes = stats.get("heartCount", stats.get("heart"))
    return (int(followers) if followers is not None else None,
            int(likes) if likes is not None else None)


def parse_room(text):
    """Viewer count while live, None while offline or unreadable."""
    try:
        room = (json.loads(text).get("data") or {}).get("liveRoom") or {}
    except (ValueError, AttributeError):
        return None
    if room.get("status") != 2:
        return None
    count = (room.get("liveRoomStats") or {}).get("userCount")
    return int(count) if count is not None else None


class TikTokStats:
    def __init__(self, log):
        self.log = log
        self.state = {"followers": None, "likes": None, "viewers": None}
        self._name = ""
        self._profile_at = 0.0
        self._live_at = 0.0
        self._warned = set()

    def _warn(self, key, msg):
        if key not in self._warned:
            self._warned.add(key)
            self.log(f"TikTok (experimental): {msg}")

    def poll(self, name, want_profile, want_live):
        """Called from the worker thread on every tick; does a request
        only when one is due."""
        name = (name or "").strip().lstrip("@")
        if name != self._name:
            self._name = name
            self._profile_at = self._live_at = 0.0
            self.state = {"followers": None, "likes": None, "viewers": None}
        if not name:
            return self.state
        now = time.time()
        if want_profile and now >= self._profile_at:
            self._profile_at = now + PROFILE_EVERY
            try:
                html = request_text(PROFILE_URL.format(
                    name=quote(name, safe="")), headers=BROWSER)
                followers, likes = parse_profile(html)
                if followers is None:
                    self._profile_at = now + RETRY_AFTER
                    self._warn("profile", "the profile page did not carry "
                                          "the numbers (blocked or changed)")
                self.state["followers"], self.state["likes"] = \
                    followers, likes
            except Exception as e:
                self._profile_at = now + RETRY_AFTER
                self._warn("profile-net", f"profile not reachable ({e})")
        if want_live and now >= self._live_at:
            self._live_at = now + LIVE_EVERY
            try:
                self.state["viewers"] = parse_room(request_text(
                    ROOM_URL.format(name=quote(name, safe="")),
                    headers=BROWSER))
            except Exception as e:
                self._live_at = now + RETRY_AFTER
                self.state["viewers"] = None
                self._warn("live-net", f"live info not reachable ({e})")
        return dict(self.state)
