"""Twitch side of the Stream Stats plugin.

Two ways to the same numbers:

  keyless   DecAPI (decapi.me), a public plaintext proxy. No account, no
            keys, one plain GET per value. Cached on their side, so the
            viewer count can lag a few seconds behind.
  official  Twitch Helix with a client-credentials app token. Needs a
            Client-ID and a Client-Secret from dev.twitch.tv, gives
            everything in a single request and does not depend on a
            third party staying online.

Chat is independent of that choice: Twitch still allows anonymous IRC
logins (the well-known justinfan user), so reading a channel's chat
needs no account in either mode.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import random
import re
import socket
import ssl
import threading
import time
from urllib.parse import quote

from .netutil import HttpError, build_url, human_since, request_json, \
    request_text

DECAPI = "https://decapi.me/twitch"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"
# DecAPI answers offline channels with a sentence we would have to guess
# at, so we hand it our own marker instead of pattern-matching English
OFFLINE_MARK = "__dreamchatbox_offline__"

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
PRIVMSG_RE = re.compile(
    r"^(?:@[^ ]* )?:([^!]+)![^ ]* PRIVMSG #[^ ]+ :(.*)$")


def empty_state(name=""):
    return {"live": False, "name": name, "title": "", "game": "",
            "uptime": "", "viewers": None}


class TwitchSource:
    """Stateless per poll except for the cached app token."""

    def __init__(self, log):
        self.log = log
        self._token = ""
        self._token_until = 0.0
        self._warned = set()

    def reset(self):
        """Called when the user changed the credentials or the mode."""
        self._token = ""
        self._token_until = 0.0
        self._warned.clear()

    def _warn(self, key, msg):
        """One line per problem, not one per poll."""
        if key not in self._warned:
            self._warned.add(key)
            self.log(msg)

    # ------------------------------------------------------------ entry
    def fetch(self, conf, want):
        """conf: channel, mode, client_id, client_secret.
        want: set of the fields the placeholders actually need."""
        channel = (conf.get("channel") or "").strip().lstrip("@")
        if not channel:
            return empty_state()
        try:
            if conf.get("mode") == "official":
                return self._fetch_helix(channel, conf, want)
            return self._fetch_keyless(channel, want)
        except HttpError as e:
            self._warn(f"http{e.status}", f"Twitch: {e}")
        except Exception as e:
            self._warn("net", f"Twitch: not reachable ({e})")
        return empty_state(channel)

    # ---------------------------------------------------------- keyless
    def _fetch_keyless(self, channel, want):
        state = empty_state(channel)
        ch = quote(channel, safe="")
        uptime = request_text(
            build_url(f"{DECAPI}/uptime/{ch}", offline_msg=OFFLINE_MARK)
        ).strip()
        if not uptime or OFFLINE_MARK in uptime:
            return state
        low = uptime.lower()
        # DecAPI reports errors in plain english on a 200, so anything
        # that is not an uptime means "no live stream to talk about"
        if "offline" in low or "error" in low or "not found" in low:
            return state
        state["live"] = True
        state["uptime"] = uptime
        self._warned.discard("net")

        if "viewers" in want:
            raw = request_text(f"{DECAPI}/viewercount/{ch}").strip()
            digits = re.sub(r"[^\d]", "", raw)
            state["viewers"] = int(digits) if digits else None
        if "title" in want:
            state["title"] = request_text(f"{DECAPI}/title/{ch}").strip()
        if "game" in want:
            state["game"] = request_text(f"{DECAPI}/game/{ch}").strip()
        return state

    # --------------------------------------------------------- official
    def _token_for(self, conf, force=False):
        if not force and self._token and time.time() < self._token_until:
            return self._token
        data = request_json(TOKEN_URL, data={
            "client_id": conf.get("client_id", ""),
            "client_secret": conf.get("client_secret", ""),
            "grant_type": "client_credentials"})
        self._token = str(data.get("access_token") or "")
        # renew a minute early rather than racing the expiry
        self._token_until = time.time() + int(data.get("expires_in", 3600)) - 60
        if not self._token:
            raise HttpError(401, "Twitch returned no access token")
        return self._token

    def _fetch_helix(self, channel, conf, want, _retry=True):
        if not conf.get("client_id") or not conf.get("client_secret"):
            self._warn("creds", "Twitch: official mode needs a Client-ID "
                                "and a Client-Secret – falling back to the "
                                "keyless source.")
            return self._fetch_keyless(channel, want)
        token = self._token_for(conf)
        headers = {"Client-Id": conf["client_id"],
                   "Authorization": f"Bearer {token}"}
        try:
            data = request_json(
                build_url(f"{HELIX}/streams", user_login=channel),
                headers=headers)
        except HttpError as e:
            if e.status == 401 and _retry:
                # token revoked or expired early – one fresh try
                self._token_for(conf, force=True)
                return self._fetch_helix(channel, conf, want, _retry=False)
            raise
        items = data.get("data") or []
        state = empty_state(channel)
        if not items:
            return state
        item = items[0]
        state["live"] = True
        state["name"] = str(item.get("user_name") or channel)
        state["title"] = str(item.get("title") or "")
        state["game"] = str(item.get("game_name") or "")
        state["uptime"] = human_since(item.get("started_at"))
        try:
            state["viewers"] = int(item.get("viewer_count"))
        except (TypeError, ValueError):
            state["viewers"] = None
        self._warned.clear()
        return state


class TwitchChat:
    """Anonymous IRC reader – keeps the most recent chat line.

    Runs in its own thread and reconnects on its own; the worker only
    tells it which channel to sit in.
    """

    def __init__(self, log):
        self.log = log
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._channel = ""
        self._last = None

    # ----------------------------------------------------------- state
    def last(self):
        with self._lock:
            return self._last

    def _set_last(self, author, text):
        with self._lock:
            self._last = (author, text)

    # --------------------------------------------------------- control
    def ensure(self, channel):
        """Idempotent: connects, switches channel or stops, whatever the
        current settings need. Safe to call on every poll."""
        channel = (channel or "").strip().lstrip("#@").lower()
        if channel == self._channel and self._thread and \
                self._thread.is_alive():
            return
        self.stop()
        if not channel:
            return
        self._channel = channel
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(channel, self._stop),
            name="stream_stats-twitch-chat", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self._channel = ""
        with self._lock:
            self._last = None

    # ------------------------------------------------------------ loop
    def _run(self, channel, stop):
        backoff = 5
        while not stop.is_set():
            try:
                self._session(channel, stop)
                backoff = 5
            except Exception as e:
                if stop.is_set():
                    return
                self.log(f"Twitch chat: connection lost ({e}), retrying "
                         f"in {backoff}s")
            # sleep in slices so a disable takes effect immediately
            for _ in range(backoff * 2):
                if stop.is_set():
                    return
                time.sleep(0.5)
            backoff = min(60, backoff * 2)

    def _session(self, channel, stop):
        ctx = ssl.create_default_context()
        raw = socket.create_connection((IRC_HOST, IRC_PORT), timeout=10)
        with ctx.wrap_socket(raw, server_hostname=IRC_HOST) as sock:
            sock.settimeout(1.0)
            nick = f"justinfan{random.randint(10000, 99999)}"
            sock.sendall(f"NICK {nick}\r\n".encode())
            sock.sendall(f"JOIN #{channel}\r\n".encode())
            buf = ""
            while not stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("server closed the connection")
                buf += chunk.decode("utf-8", "replace")
                while "\r\n" in buf:
                    line, buf = buf.split("\r\n", 1)
                    self._handle(sock, line)

    def _handle(self, sock, line):
        if line.startswith("PING"):
            sock.sendall(b"PONG :tmi.twitch.tv\r\n")
            return
        match = PRIVMSG_RE.match(line)
        if match:
            author, text = match.group(1), match.group(2).strip()
            if text:
                self._set_last(author, text)
