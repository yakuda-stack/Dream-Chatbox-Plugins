"""The background half of Stream Stats.

Everything that touches the network lives in this thread. get_values()
in main.py only ever reads the snapshot it leaves behind, so a slow API
or a dead connection can stall a poll but never the chatbox.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import threading
import time

from .twitch import TwitchChat, TwitchSource, empty_state
from .youtube import YouTubeSource

TICK = 0.5
MIN_POLL = 10
# a settings change lands here character by character while someone types
# a channel name, so wait for the typing to stop before firing a request
SETTLE = 1.5


class StreamWorker(threading.Thread):
    def __init__(self, read_conf, log):
        super().__init__(name="stream_stats-worker", daemon=True)
        self._read = read_conf
        self.log = log
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state = {"twitch": empty_state(), "youtube": empty_state()}
        self.twitch = TwitchSource(log)
        self.youtube = YouTubeSource(log)
        self.chat = TwitchChat(log)
        self._last_poll = 0.0
        self._sig = None
        self._changed_at = 0.0

    # ----------------------------------------------------------- public
    def snapshot(self):
        """What the placeholders are built from. Always a copy, so the
        caller can never see a half-written poll."""
        with self._lock:
            state = {k: dict(v) for k, v in self._state.items()}
        state["twitch"]["chat"] = self.chat.last()
        state["youtube"]["chat"] = self.youtube.last_chat()
        return state

    def stop(self):
        self._stop.set()
        self.chat.stop()

    # ------------------------------------------------------------- loop
    def run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:            # never let the thread die
                self.log(f"worker: {e}")
            self._stop.wait(TICK)

    def _tick(self):
        conf = self._read()
        now = time.time()

        # credentials or channel changed -> drop cached tokens and ids
        sig = self._signature(conf)
        if sig != self._sig:
            if self._sig is not None:
                self.twitch.reset()
                self.youtube.reset()
            self._sig = sig
            self._changed_at = now

        # Twitch chat is a standing connection, not a poll
        tw = conf["twitch"]
        if tw["enabled"] and tw["chat"] and tw["channel"]:
            self.chat.ensure(tw["channel"])
        else:
            self.chat.stop()

        settled = self._changed_at and (now - self._changed_at >= SETTLE)
        due = now >= self._last_poll + max(MIN_POLL, conf["poll"])
        if settled or due:
            self._changed_at = 0.0
            self._last_poll = now
            self._poll(conf)

        # YouTube chat runs on the interval the API asks for, which is a
        # lot shorter than the stats poll
        yt = conf["youtube"]
        if yt["enabled"] and yt["chat"]:
            self.youtube.poll_chat(yt)

    def _poll(self, conf):
        want = conf["want"]
        for key, source in (("twitch", self.twitch),
                            ("youtube", self.youtube)):
            part = conf[key]
            if not part["enabled"] or not part["channel"]:
                state = empty_state()
            else:
                state = source.fetch(part, want)
            with self._lock:
                self._state[key] = state

    @staticmethod
    def _signature(conf):
        tw, yt = conf["twitch"], conf["youtube"]
        return (tw["channel"], tw["mode"], tw["client_id"],
                tw["client_secret"], tw["enabled"],
                yt["channel"], yt["mode"], yt["api_key"], yt["enabled"])
