"""Heart rate from Pulsoid or HypeRate.

  pulsoid    https://dev.pulsoid.net/api/v1/data/heart_rate/latest
             with the token from pulsoid.net -> Settings -> Tokens
             ("Manual token"). Polled every two seconds.
  hyperate   wss://app.hyperate.io/socket/websocket - a Phoenix channel
             "hr:<your id>". Needs an API key from HypeRate (they hand
             them out on request) plus the ID shown in the HypeRate app.

Both run on a background thread; get_values() reads a snapshot. The
statistics - average, minimum, maximum, trend - are kept here, over the
current session (they start fresh when the app starts).

The WebSocket client is a small stdlib one on purpose: the plugin has to
work in the AppImage and the AUR package without extra packages.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import base64
import collections
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.parse

from . import netutil

PULSOID_URL = "https://dev.pulsoid.net/api/v1/data/heart_rate/latest"
HYPERATE_URL = "wss://app.hyperate.io/socket/websocket"
PULSOID_EVERY = 2.0
STALE_AFTER = 30.0            # no new reading for this long -> hide it
TREND_WINDOW = 30.0           # compare the last 30 s with the 30 s before


class HeartRate:
    """Statistics over the readings of one session."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.last = None
            self.last_at = 0.0
            self.count = 0
            self.total = 0
            self.low = None
            self.high = None
            self.recent = collections.deque()       # (time, bpm)

    def add(self, bpm, at=None):
        try:
            bpm = int(round(float(bpm)))
        except (TypeError, ValueError):
            return
        if not 20 <= bpm <= 250:        # a sensor that lost contact
            return
        at = time.time() if at is None else at
        with self._lock:
            self.last, self.last_at = bpm, at
            self.count += 1
            self.total += bpm
            self.low = bpm if self.low is None else min(self.low, bpm)
            self.high = bpm if self.high is None else max(self.high, bpm)
            self.recent.append((at, bpm))
            while self.recent and at - self.recent[0][0] > 2 * TREND_WINDOW:
                self.recent.popleft()

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            if self.last is None or now - self.last_at > STALE_AFTER:
                return None
            newer = [b for t, b in self.recent if now - t <= TREND_WINDOW]
            older = [b for t, b in self.recent if now - t > TREND_WINDOW]
            trend = ""
            if newer and older:
                diff = sum(newer) / len(newer) - sum(older) / len(older)
                trend = "↗" if diff > 3 else "↘" if diff < -3 \
                    else "→"
            return {"bpm": self.last,
                    "avg": round(self.total / self.count),
                    "min": self.low, "max": self.high, "trend": trend}


class HeartRateWorker(threading.Thread):
    """read_conf() returns (enabled, source, pulsoid_token, hyperate_id,
    hyperate_key)."""

    def __init__(self, read_conf, log):
        super().__init__(name="life_stats-heartrate", daemon=True)
        self._read = read_conf
        self.log = log
        self.stats = HeartRate()
        self._stop = threading.Event()
        self._sig = None
        self._ws = None
        self.status = "off"
        self._warned = None
        self._measured = None
        self._beat = 0.0

    def stop(self):
        self._stop.set()
        self._close_ws()

    def run(self):
        while not self._stop.is_set():
            wait = PULSOID_EVERY
            try:
                wait = self._tick()
            except Exception as e:
                self._say(f"error: {e}")
                self._close_ws()
                wait = 10.0
            self._stop.wait(wait)

    def _say(self, text):
        self.status = text
        if text != self._warned and text.startswith("error"):
            self._warned = text
            self.log(f"heart rate: {text}")

    def _tick(self):
        enabled, source, token, hr_id, hr_key = self._read()
        sig = (enabled, source, token, hr_id, hr_key)
        if sig != self._sig:
            self._sig = sig
            self._close_ws()
            self.stats.reset()
        if not enabled:
            self.status = "off"
            return 1.0
        if source == "hyperate":
            return self._hyperate(hr_id, hr_key)
        return self._pulsoid(token)

    # ------------------------------------------------------------ pulsoid
    def _pulsoid(self, token):
        token = (token or "").strip()
        if not token:
            self.status = "paste your Pulsoid token"
            return 2.0
        try:
            data = netutil.request_json(
                PULSOID_URL, headers={"Authorization": f"Bearer {token}"})
        except netutil.HttpError as e:
            if e.status == 412:
                # Pulsoid's "no data yet": the sensor is not sending
                self.status = "connected - waiting for the sensor"
                return 5.0
            if e.status in (401, 403):
                self._say("error: Pulsoid refused the token")
                return 30.0
            raise
        bpm = (data.get("data") or {}).get("heart_rate")
        measured = data.get("measured_at")
        if bpm is not None and measured != self._measured:
            # Pulsoid hands out the last value forever: count each
            # measurement once (or the average drifts towards whatever
            # was last seen), and date it by the sensor, so an old one
            # ages out like a missing one
            self._measured = measured
            at = float(measured) / 1000.0 if measured else None
            self.stats.add(bpm, at if at and at <= time.time() + 5 else None)
            self.status = f"Pulsoid: {bpm} bpm"
        return PULSOID_EVERY

    # ----------------------------------------------------------- hyperate
    def _hyperate(self, hr_id, key):
        hr_id, key = (hr_id or "").strip(), (key or "").strip()
        if not hr_id or not key:
            self.status = "HypeRate needs your ID and an API key"
            return 2.0
        if self._ws is None:
            self._ws = _WebSocket(HYPERATE_URL + "?" + urllib.parse.urlencode(
                {"token": key}))
            self._ws.send_json({"topic": f"hr:{hr_id}", "event": "phx_join",
                                "payload": {}, "ref": 1})
            self._beat = time.time()
            self.status = "HypeRate: connected"
        if time.time() - self._beat > 10:
            self._ws.send_json({"topic": "phoenix", "event": "heartbeat",
                                "payload": {}, "ref": 0})
            self._beat = time.time()
        msg = self._ws.recv_json(timeout=1.0)
        if msg and msg.get("event") == "hr_update":
            bpm = (msg.get("payload") or {}).get("hr")
            self.stats.add(bpm)
            self.status = f"HypeRate: {bpm} bpm"
        elif msg and msg.get("event") == "phx_reply" and \
                (msg.get("payload") or {}).get("status") == "error":
            raise RuntimeError("HypeRate refused the ID or the key")
        return 0.0

    def _close_ws(self):
        ws, self._ws = self._ws, None
        if ws is not None:
            ws.close()


class _WebSocket:
    """Just enough RFC 6455 for a JSON text channel over TLS."""

    def __init__(self, url, timeout=8.0):
        parts = urllib.parse.urlsplit(url)
        host, port = parts.hostname, parts.port or 443
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(
            raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parts.path + ("?" + parts.query if parts.query else "")
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"User-Agent: {netutil.USER_AGENT}\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("closed during handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(
                "latin-1", "replace"))
        self._buf = head.split(b"\r\n\r\n", 1)[1]

    def send_json(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        head = bytes([0x81])
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(head + mask + body)

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv_json(self, timeout=1.0):
        """One text frame as JSON, or None when nothing arrived."""
        self.sock.settimeout(timeout)
        try:
            b1, b2 = self._read(2)
        except (socket.timeout, ssl.SSLWantReadError):
            return None
        self.sock.settimeout(8.0)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        payload = self._read(n)
        opcode = b1 & 0x0F
        if opcode == 0x8:
            raise ConnectionError("server closed the connection")
        if opcode != 0x1:
            return None
        try:
            return json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            return None

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def values(get, vals, snap):
    if not get("heartrate", False) or not snap:
        return
    icon = str(get("heartrate_icon", "") or "").strip()
    unit = " bpm" if get("heartrate_unit", False) else ""
    vals["heartrate"] = f"{icon} {snap['bpm']}{unit}".strip()
    vals["heartrate_avg"] = f"{snap['avg']}{unit}"
    vals["heartrate_min"] = f"{snap['min']}{unit}"
    vals["heartrate_max"] = f"{snap['max']}{unit}"
    vals["heartrate_trend"] = snap["trend"] or None
