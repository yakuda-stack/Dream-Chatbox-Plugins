"""IRC Bridge - the newest message from an IRC channel.

Port of the read half of `IRCBridge` from Bluscream's VRCOSC-Modules. The
upstream module also sends, hashes nicknames and mirrors VRChat events
back into IRC; here the chatbox only listens, because a chatbox is an
output.

Plain socket, no library. It speaks enough IRC to be a well-behaved
client: PASS/NICK/USER, JOIN, PONG on every PING, and a reconnect with a
backoff so a dead server does not turn into a connection storm. Works
against Libera, a private ZNC or Twitch chat (`irc.chat.twitch.tv`,
password `oauth:…`, or `justinfan12345` with no password for read-only).
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import socket
import ssl
import threading
import time

from . import util

ID = "irc"
NAME = "IRC Bridge"

KEYS = ("irc_msg", "irc_author", "irc_channel", "irc_count", "irc_online")

_client = None


class _Client(threading.Thread):
    """One connection, kept alive until stop() is called."""

    def __init__(self, conf, log):
        super().__init__(name="irc", daemon=True)
        self.conf = conf
        self.log = log
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._sock = None
        self._online = False
        self._last = None            # (author, message, timestamp)
        self._count = 0
        self._logged = False

    # -- public ------------------------------------------------------
    def snapshot(self):
        with self._lock:
            return {"online": self._online, "last": self._last,
                    "count": self._count}

    def stop(self):
        self._stop.set()
        self._close()
        self.join(timeout=3.0)

    # -- socket ------------------------------------------------------
    def _close(self):
        sock, self._sock = self._sock, None
        with self._lock:
            self._online = False
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _send(self, text):
        if self._sock is not None:
            self._sock.sendall((text + "\r\n").encode("utf-8", "replace"))

    def run(self):
        backoff = 5
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 5
            except Exception as e:
                if not self._logged:      # log once per bad streak
                    self._logged = True
                    self.log(f"irc: {e}")
            finally:
                self._close()
            if self._stop.wait(backoff):
                return
            backoff = min(300, backoff * 2)

    def _session(self):
        conf = self.conf
        sock = socket.create_connection((conf["server"], conf["port"]),
                                        timeout=20)
        if conf["tls"]:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=conf["server"])
        sock.settimeout(300)              # servers ping well inside that
        self._sock = sock

        if conf["password"]:
            self._send(f"PASS {conf['password']}")
        self._send(f"NICK {conf['nick']}")
        self._send(f"USER {conf['nick']} 0 * :OSC-DreamChatbox")

        buffer = ""
        joined = False
        while not self._stop.is_set():
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed")
            buffer += chunk.decode("utf-8", errors="replace")
            while "\r\n" in buffer:
                raw, buffer = buffer.split("\r\n", 1)
                joined = self._handle(raw, joined)

    def _handle(self, raw, joined):
        if raw.startswith("PING"):
            self._send("PONG" + raw[4:])
            return joined
        parts = raw.split(" ")
        if len(parts) < 2:
            return joined
        command = parts[1]

        # 001 = welcome; only then is a JOIN allowed
        if command == "001" and not joined:
            self._send(f"JOIN {self.conf['channel']}")
            self._logged = False
            with self._lock:
                self._online = True
            return True
        if command in ("433", "432"):
            raise RuntimeError("nickname rejected by the server")
        if command == "PRIVMSG" and len(parts) >= 4:
            author = parts[0].lstrip(":").split("!", 1)[0]
            message = raw.split(" :", 1)[-1] if " :" in raw else ""
            with self._lock:
                self._last = (author, message, time.time())
                self._count += 1
        return joined


# ---------------------------------------------------------------- setup
def _conf(ctx):
    channel = ctx.text("irc_channel")
    if channel and not channel.startswith(("#", "&")):
        channel = "#" + channel
    return {
        "server": ctx.text("irc_server", "irc.libera.chat"),
        "port": ctx.num("irc_port", 6697, 1, 65535),
        "tls": ctx.flag("irc_tls", True),
        "nick": ctx.text("irc_nick") or f"dreambox{int(time.time()) % 10000}",
        "password": ctx.text("irc_pass"),
        "channel": channel,
    }


def start(ctx):
    global _client
    stop()
    conf = _conf(ctx)
    if not conf["server"] or not conf["channel"]:
        ctx.log("irc: server or channel missing - staying idle")
        return
    _client = _Client(conf, ctx.log)
    _client.start()


def stop():
    global _client
    if _client is not None:
        try:
            _client.stop()
        except Exception:
            pass
    _client = None


def on_settings(ctx):
    """Server, nick and channel live in the handshake, so a change means
    a fresh connection."""
    if _client is None:
        return
    if _conf(ctx) != _client.conf:
        start(ctx)


def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _client is None:
        return vals
    snap = _client.snapshot()
    if snap["online"]:
        vals["irc_online"] = ctx.text("irc_icon", "💬") or None
        vals["irc_channel"] = _client.conf["channel"]
    vals["irc_count"] = str(snap["count"]) if snap["count"] else None

    last = snap["last"]
    if not last:
        return vals
    author, message, stamp = last
    hold = ctx.num("irc_hold", 0, 0, 3600)
    if hold and (time.time() - stamp) > hold:
        return vals                        # message expired, drop it
    vals["irc_author"] = author
    if ctx.flag("irc_author", True):
        message = f"{author}: {message}"
    vals["irc_msg"] = util.join(
        ctx.text("irc_prefix"),
        util.cut(message, ctx.num("irc_max", 60, 10, 140))) or None
    return vals


def line(vals):
    return [vals["irc_msg"]]
