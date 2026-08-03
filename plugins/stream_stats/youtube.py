"""YouTube side of the Stream Stats plugin.

  keyless   Loads youtube.com/<channel>/live like a browser would and
            reads the values out of the embedded JSON. No key, no quota,
            but it depends on markers YouTube can rename any day – so
            every regex here fails softly into "offline" instead of
            raising.
  official  Data API v3 with an API key. Stable, and the only way to
            {s_chat}, because the live chat is not part of the page.

The API key costs quota, and the one call that finds a running broadcast
(search) is the expensive one at 100 units of the 10.000 per day. So the
video id is cached for as long as the stream runs and only re-searched
after it ended – that turns a day of streaming into a handful of
searches instead of one per poll.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import html
import json
import re
import time

from .netutil import HttpError, build_url, human_since, request_json, \
    request_text

API = "https://www.googleapis.com/youtube/v3"
WATCH = "https://www.youtube.com"
# never search more often than this, whatever the poll interval says
SEARCH_MIN_GAP = 90.0

_RE_LIVE = re.compile(r'"isLive(?:Now)?"\s*:\s*true')
_RE_VIEWS = re.compile(r'"originalViewCount"\s*:\s*"(\d+)"')
_RE_VIEWS_ALT = re.compile(r'"concurrentViewers"\s*:\s*"(\d+)"')
_RE_TITLE = re.compile(r'<meta\s+name="title"\s+content="([^"]*)"')
_RE_OWNER = re.compile(r'"ownerChannelName"\s*:\s*"([^"]{1,80})"')
_RE_START = re.compile(r'"startTimestamp"\s*:\s*"([^"]+)"')
_RE_VIDEO = re.compile(r'"videoId"\s*:\s*"([\w-]{11})"')


def empty_state(name=""):
    return {"live": False, "name": name, "title": "", "game": "",
            "uptime": "", "viewers": None}


def _channel_path(channel):
    """Turns whatever the user typed into the /live URL for it."""
    value = str(channel or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value.rstrip("/") + "/live"
    if value.startswith("UC") and len(value) == 24:
        return f"{WATCH}/channel/{value}/live"
    if not value.startswith("@"):
        value = "@" + value
    return f"{WATCH}/{value}/live"


class YouTubeSource:
    def __init__(self, log):
        self.log = log
        self._warned = set()
        self._channel_id = ""
        self._channel_key = ""      # what the id was resolved from
        self._video_id = ""
        self._chat_id = ""
        self._next_search = 0.0
        self._chat_token = ""
        self._chat_due = 0.0
        self._chat_last = None

    def reset(self):
        self._warned.clear()
        self._channel_id = ""
        self._channel_key = ""
        self._video_id = ""
        self._chat_id = ""
        self._next_search = 0.0
        self._chat_token = ""
        self._chat_last = None

    def _warn(self, key, msg):
        if key not in self._warned:
            self._warned.add(key)
            self.log(msg)

    def last_chat(self):
        return self._chat_last

    # ------------------------------------------------------------ entry
    def fetch(self, conf, want):
        channel = (conf.get("channel") or "").strip()
        if not channel:
            return empty_state()
        try:
            if conf.get("mode") == "official":
                if conf.get("api_key"):
                    return self._fetch_api(channel, conf, want)
                self._warn("key", "YouTube: official mode needs an API key – "
                                  "using the keyless source instead.")
            return self._fetch_keyless(channel)
        except HttpError as e:
            self._warn(f"http{e.status}", f"YouTube: {e}")
        except Exception as e:
            self._warn("net", f"YouTube: not reachable ({e})")
        return empty_state(channel)

    # ---------------------------------------------------------- keyless
    def _fetch_keyless(self, channel):
        state = empty_state(channel.lstrip("@"))
        page = request_text(_channel_path(channel))
        if not _RE_LIVE.search(page):
            return state
        state["live"] = True
        match = _RE_VIEWS.search(page) or _RE_VIEWS_ALT.search(page)
        if match:
            try:
                state["viewers"] = int(match.group(1))
            except ValueError:
                state["viewers"] = None
        match = _RE_TITLE.search(page)
        if match:
            state["title"] = html.unescape(match.group(1)).strip()
        match = _RE_OWNER.search(page)
        if match:
            try:
                state["name"] = json.loads(f'"{match.group(1)}"')
            except Exception:
                state["name"] = match.group(1)
        match = _RE_START.search(page)
        if match:
            state["uptime"] = human_since(match.group(1))
        self._warned.discard("net")
        return state

    # --------------------------------------------------------- official
    def _resolve_channel(self, channel, key):
        """@handle or channel id -> channel id. Cached, one unit."""
        if channel.startswith("UC") and len(channel) == 24:
            return channel
        if self._channel_id and self._channel_key == channel:
            return self._channel_id
        handle = channel if channel.startswith("@") else "@" + channel
        data = request_json(build_url(f"{API}/channels", part="id",
                                      forHandle=handle, key=key))
        items = data.get("items") or []
        if not items:
            raise HttpError(404, f"no channel found for {handle}")
        self._channel_id = str(items[0].get("id") or "")
        self._channel_key = channel
        return self._channel_id

    def _fetch_api(self, channel, conf, want):
        key = conf["api_key"]
        state = empty_state(channel.lstrip("@"))

        # 1) do we already know the running broadcast? Checking a known
        #    id costs one unit, searching for it costs a hundred.
        details = self._video_details(self._video_id, key) \
            if self._video_id else None
        if details is None:
            self._video_id = ""
            self._chat_id = ""
            self._chat_token = ""
            now = time.time()
            if now < self._next_search:
                return state            # still cooling down, stay offline
            self._next_search = now + SEARCH_MIN_GAP
            channel_id = self._resolve_channel(channel, key)
            found = request_json(build_url(
                f"{API}/search", part="id", channelId=channel_id,
                eventType="live", type="video", maxResults=1, key=key))
            items = found.get("items") or []
            if not items:
                return state
            self._video_id = str(items[0].get("id", {}).get("videoId") or "")
            if not self._video_id:
                return state
            details = self._video_details(self._video_id, key)
            if details is None:
                return state

        snippet = details.get("snippet") or {}
        live = details.get("liveStreamingDetails") or {}
        state["live"] = True
        state["name"] = str(snippet.get("channelTitle") or state["name"])
        state["title"] = str(snippet.get("title") or "")
        state["uptime"] = human_since(live.get("actualStartTime"))
        try:
            state["viewers"] = int(live.get("concurrentViewers"))
        except (TypeError, ValueError):
            state["viewers"] = None
        self._chat_id = str(live.get("activeLiveChatId") or "")
        self._warned.clear()
        return state

    def _video_details(self, video_id, key):
        """The video's live block, or None when it is not live (any more)."""
        if not video_id:
            return None
        data = request_json(build_url(
            f"{API}/videos", part="snippet,liveStreamingDetails",
            id=video_id, key=key))
        items = data.get("items") or []
        if not items:
            return None
        item = items[0]
        live = item.get("liveStreamingDetails") or {}
        if not live or live.get("actualEndTime"):
            return None
        snippet = item.get("snippet") or {}
        if snippet.get("liveBroadcastContent") not in ("live", None, ""):
            return None
        return item

    # ------------------------------------------------------------ chat
    def poll_chat(self, conf):
        """Fetches new live chat messages and keeps the newest one.

        Only possible with an API key: the chat is not part of the page
        the keyless mode reads. Honours the polling interval the API
        asks for, so this stays well inside the quota.
        """
        if conf.get("mode") != "official" or not conf.get("api_key"):
            self._chat_last = None
            return
        if not self._chat_id or time.time() < self._chat_due:
            return
        try:
            data = request_json(build_url(
                f"{API}/liveChat/messages", liveChatId=self._chat_id,
                part="snippet,authorDetails", maxResults=200,
                pageToken=self._chat_token, key=conf["api_key"]))
        except HttpError as e:
            # a finished chat answers 403/404 – forget it and let the
            # next stats poll hand us a new chat id
            self._chat_id = ""
            self._chat_token = ""
            self._warn(f"chat{e.status}", f"YouTube chat: {e}")
            return
        except Exception as e:
            self._warn("chatnet", f"YouTube chat: not reachable ({e})")
            return
        self._chat_token = str(data.get("nextPageToken") or "")
        wait = data.get("pollingIntervalMillis") or 5000
        try:
            self._chat_due = time.time() + max(2.0, float(wait) / 1000.0)
        except (TypeError, ValueError):
            self._chat_due = time.time() + 5.0
        items = data.get("items") or []
        if not items:
            return
        newest = items[-1]
        author = str((newest.get("authorDetails") or {})
                     .get("displayName") or "")
        text = str((newest.get("snippet") or {})
                   .get("displayMessage") or "").strip()
        if text:
            self._chat_last = (author, text)
