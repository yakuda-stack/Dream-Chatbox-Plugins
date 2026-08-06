"""The background half of Social Media.

Only Discord needs a thread at all – TikTok, Spotify and Instagram are
strings the user typed in. The RPC connection lives here, main.py only
reads the snapshot it leaves behind, so a hanging socket can stall a
poll but never the chatbox.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import threading
import time

from .discordrpc import DiscordRPC, empty_state

TICK = 0.5
MIN_POLL = 2
# settings arrive character by character while someone pastes an ID,
# so wait for the typing to stop before opening a connection
SETTLE = 1.5


class SocialWorker(threading.Thread):
    def __init__(self, read_conf, log, store_path=""):
        super().__init__(name="social_media-worker", daemon=True)
        self._read = read_conf
        self.log = log
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state = empty_state()
        self.rpc = DiscordRPC(log, store_path)
        self._last_poll = 0.0
        self._sig = None
        self._changed_at = 0.0
        self._live = False

    # ----------------------------------------------------------- public
    def snapshot(self):
        """Always a copy, so the caller can never see a half-written
        poll."""
        with self._lock:
            return dict(self._state)

    def stop(self):
        self._stop.set()
        try:
            self.rpc.close()
        except Exception:
            pass

    # ------------------------------------------------------------- loop
    def run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:            # never let the thread die
                self.log(f"worker: {e}")
            self._stop.wait(TICK)

    def _tick(self):
        conf = self._read()["discord"]
        now = time.time()

        live = bool(conf["enabled"] and conf["live"] and conf["client_id"])
        if not live:
            # switched off or back to the plain name: hand the socket
            # back to the system instead of holding it open for nothing
            if self._live:
                self.rpc.close()
                self._live = False
                self._sig = None
                with self._lock:
                    self._state = empty_state()
            return
        self._live = True

        # credentials changed -> forget the connection and the token
        sig = (conf["client_id"], conf["client_secret"], conf["redirect"])
        if sig != self._sig:
            if self._sig is not None:
                self.rpc.reset()
            self._sig = sig
            self._changed_at = now

        settled = self._changed_at and (now - self._changed_at >= SETTLE)
        due = now >= self._last_poll + max(MIN_POLL, conf["poll"])
        if not (settled or due):
            return
        self._changed_at = 0.0
        self._last_poll = now

        state = self.rpc.poll(conf)
        with self._lock:
            self._state = state
