"""
fps.py - one frame rate out of whichever source can supply it.

Three backends, two platforms, one number:

    Linux    fpslayer     the Vulkan layer this plugin ships
             fpsmangohud  MangoHud's CSV log
    Windows  fpsrtss      RTSS shared memory

"auto" is the default and means "whatever answers first", in the order
that costs the user least: the layer needs nothing installed, so it goes
first on Linux; RTSS is the only option on Windows, so there is nothing
to order there.

CACHING
-------
get_values() can be called several times per chatbox frame and the
placeholders {fps}, {fps_raw} and {frametime} all come from the same
reading. Every backend here is cheap - an mmap, a directory listing, a
4 KB tail - but "cheap" times three times a poll is still work for
nothing, so one reading is held for MIN_INTERVAL and handed out again.

SMOOTHING
---------
Raw frame rate jitters by a few percent even in a locked-framerate VR
session, and a chatbox line that flickers between 89 and 91 reads as
broken. The value is rounded and only allowed to move when it has moved
by more than a frame or two, which is a display decision and not a
measurement one - {fps_raw} is not smoothed.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import sys
import time

from . import fpslayer, fpsmangohud, fpsrtss

IS_WINDOWS = sys.platform.startswith("win")

#: one reading is reused for this long
MIN_INTERVAL = 0.4

#: the displayed number ignores changes smaller than this, so a stable
#: 90 Hz session shows 90 rather than flickering to 89 and back
SMOOTH_STEP = 2.0

#: after this long with no reading the placeholders go empty rather than
#: keeping the last number on screen
HOLD_SEC = 4.0


class FpsMonitor:
    """Reads whichever backend the settings point at.

    Nothing here runs on a thread: every backend answers in well under a
    millisecond, and a thread would only add a lock to protect a number
    that changes twice a second.
    """

    def __init__(self, log=None):
        self.log = log
        self.source = "auto"
        self.process = "vrchat"
        self.mangohud_dir = ""
        self.rtss = fpsrtss.RtssReader(log)

        self._at = 0.0            # when the cache was filled
        self._hit = None          # (fps, frametime_ms, name, backend)
        self._shown = None        # the smoothed number
        self._last_ok = 0.0

    # --------------------------------------------------------- config
    def set_config(self, source="auto", process="vrchat", mangohud_dir=""):
        source = (source or "auto").strip().lower()
        if source not in ("auto", "layer", "mangohud", "rtss"):
            source = "auto"
        process = (process or "").strip()
        changed = (source != self.source or process != self.process
                   or str(mangohud_dir) != str(self.mangohud_dir))
        self.source = source
        self.process = process
        self.mangohud_dir = mangohud_dir or ""
        self.rtss.want = process.lower()
        if changed:
            # a reading taken under the old settings is not an answer to
            # the new ones
            self._at = 0.0
            self._hit = None
            self._shown = None

    # ----------------------------------------------------------- read
    def _backends(self):
        """Which backends to try, in order, for the current setting."""
        if IS_WINDOWS:
            # The layer is not offered here at all - see fpsrtss.py for
            # why a Vulkan layer cannot see VRChat on Windows.
            return ("rtss",)
        if self.source == "layer":
            return ("layer",)
        if self.source == "mangohud":
            return ("mangohud",)
        if self.source == "rtss":
            return ("rtss",)
        return ("layer", "mangohud")

    def _read_backend(self, name):
        try:
            if name == "layer":
                return fpslayer.read(self.process)
            if name == "mangohud":
                return fpsmangohud.read(self.mangohud_dir)
            if name == "rtss":
                return self.rtss.read()
        except Exception as e:                  # noqa: BLE001
            # A broken backend must never take the chatbox down with it -
            # the rest of this plugin has nothing to do with FPS.
            if callable(self.log):
                self.log(f"FPS: {name} backend failed ({e})")
        return None

    def read(self):
        """(fps, frametime_ms, name, backend) or None. Cached."""
        now = time.monotonic()
        if self._hit is not None and now - self._at < MIN_INTERVAL:
            return self._hit
        if self._hit is None and now - self._at < MIN_INTERVAL:
            return None

        self._at = now
        for name in self._backends():
            hit = self._read_backend(name)
            if hit:
                self._hit = (hit[0], hit[1], hit[2], name)
                self._last_ok = now
                return self._hit

        # Nothing answered. Hold the last reading briefly rather than
        # blinking out on a single missed poll - a game that alt-tabs for
        # a moment should not empty the line.
        if self._hit is not None and now - self._last_ok < HOLD_SEC:
            return self._hit
        self._hit = None
        self._shown = None
        return None

    # -------------------------------------------------------- display
    def value(self):
        """The number to print, smoothed. None when there is nothing."""
        hit = self.read()
        if hit is None:
            return None
        raw = hit[0]
        if self._shown is None or abs(raw - self._shown) >= SMOOTH_STEP:
            self._shown = raw
        return int(round(self._shown))

    def raw(self):
        """The unsmoothed number, for {fps_raw}."""
        hit = self.read()
        return None if hit is None else hit[0]

    def frametime(self):
        hit = self.read()
        if hit is None:
            return None
        # some backends report a frametime, others only a rate - deriving
        # it is exact either way, since one is the reciprocal of the other
        return hit[1] if hit[1] else (1000.0 / hit[0] if hit[0] else None)

    def backend(self):
        hit = self.read()
        return None if hit is None else hit[3]

    # --------------------------------------------------------- status
    def status_line(self):
        """What the Status row shows: which backend answered and what it
        reads, or - when none did - which step is missing."""
        hit = self.read()
        if hit:
            label = {"layer": "built-in layer", "mangohud": "MangoHud",
                     "rtss": "RTSS"}.get(hit[3], hit[3])
            return f"{label} · {hit[2] or 'a game'} · {hit[0]:.0f} fps"

        if IS_WINDOWS:
            return self.rtss.status_line()
        if self.source == "mangohud":
            return fpsmangohud.status_line(self.mangohud_dir)
        if self.source == "rtss":
            return self.rtss.status_line()
        # auto or layer: the layer is the one the user can act on
        note = fpslayer.status_line()
        if self.source == "auto" and self.mangohud_dir:
            return f"{note}  MangoHud: {fpsmangohud.status_line(self.mangohud_dir)}"
        return note
