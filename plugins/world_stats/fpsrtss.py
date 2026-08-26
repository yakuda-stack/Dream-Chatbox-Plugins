"""
fpsrtss.py - the FPS source on Windows.

WHY NOT THE VULKAN LAYER HERE
-----------------------------
The Linux half of this plugin ships a Vulkan layer and loads it into the
game. The same trick does not carry over, and the reason is not effort:
VRChat on Windows renders D3D11 natively. On Linux that D3D11 goes
through DXVK, which is Vulkan, which is why a Vulkan layer sees it there.
On Windows there is no translation - a Vulkan layer would sit next to the
game and never be called.

The Windows-native equivalent would be hooking IDXGISwapChain::Present,
which means injecting a DLL into VRChat.exe. VRChat runs EAC on Windows.
That is not a thing this plugin is going to do to somebody's account.

WHAT IT DOES INSTEAD
--------------------
RTSS (RivaTuner Statistics Server, the thing that ships with MSI
Afterburner) already does the injection, has done for twenty years, and
is on the anti-cheat allowlists precisely because everybody uses it. It
publishes what it measures in a named shared memory block. Reading that
block is a read-only mmap of about a hundred bytes per process, no
subprocess, no admin rights, no injection of our own.

The other candidate was Intel's PresentMon, which uses ETW and needs no
injection at all - but an ETW session needs Administrator or membership
in "Performance Log Users", and asking somebody to run a chatbox as
admin to print a number is worse than asking them to install Afterburner.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import struct
import sys
import time

IS_WINDOWS = sys.platform.startswith("win")

MAP_NAME = "RTSSSharedMemoryV2"

#: RTSS_SHARED_MEMORY header: signature, version, appEntrySize,
#: appArrOffset, appArrSize, osdEntrySize, osdArrOffset, osdArrSize
_HEADER = struct.Struct("<8I")

#: per-app entry head: dwProcessID, szName[260], dwFlags, dwTime0,
#: dwTime1, dwFrames, dwFrameTime
_ENTRY = struct.Struct("<I260sIIIII")

#: b"RTSS" read either way round, because the field has been written as
#: both over the years
_SIGNATURES = (0x53535452, 0x52545353)

#: how long to wait before probing again after RTSS was not there. It is
#: a cheap call, but not one worth making every second for somebody who
#: has never installed it.
RETRY_SEC = 10.0

#: RTSS keeps entries for processes that have stopped rendering. An
#: entry whose window has not moved is a game that closed.
STALE_SEC = 5.0


class RtssReader:
    """FPS from RTSS's shared memory.

    ``want`` is a substring of the executable name to prefer - "vrchat"
    by default, so a browser playing a video in the background cannot
    outrank the game.
    """

    def __init__(self, log=None, want="vrchat"):
        self.log = log
        self.want = (want or "").strip().lower()
        #: None = never looked, True/False = what we found last time
        self.ok = None
        self.error = ""
        self._next_try = 0.0
        self._seen = {}          # pid -> (frames, monotonic seen)

    # ------------------------------------------------------------ read
    def read(self):
        """(fps, frametime_ms, process name) or None."""
        if not IS_WINDOWS:
            self.error = "RTSS is Windows-only."
            return None
        now = time.monotonic()
        if self.ok is False and now < self._next_try:
            return None
        try:
            import mmap
            block = mmap.mmap(-1, 0, tagname=MAP_NAME,
                              access=mmap.ACCESS_READ)
        except Exception:                       # noqa: BLE001
            if self.ok is not False and callable(self.log):
                self.log("FPS: RTSS is not running - install RivaTuner "
                         "Statistics Server (it ships with MSI "
                         "Afterburner) and leave it running.")
            self.ok = False
            self.error = "RTSS is not running."
            self._next_try = now + RETRY_SEC
            return None
        try:
            return self._parse(block)
        except Exception as e:                  # noqa: BLE001
            self.error = f"RTSS shared memory unreadable ({e})."
            return None
        finally:
            try:
                block.close()
            except Exception:                   # noqa: BLE001
                pass

    def _parse(self, block):
        (sig, _ver, entry_size, arr_off, arr_size,
         _oe, _oo, _os) = _HEADER.unpack(block[:_HEADER.size])
        if sig not in _SIGNATURES or not entry_size or not arr_size:
            self.error = "RTSS shared memory has an unexpected layout."
            return None
        if self.ok is not True:
            self.ok = True
            self.error = ""
            if callable(self.log):
                self.log("FPS: RTSS found.")

        now = time.monotonic()
        best = None
        wanted = None
        for i in range(min(arr_size, 256)):
            off = arr_off + i * entry_size
            if off + _ENTRY.size > len(block):
                break
            (pid, raw_name, _flags, t0, t1, frames,
             ftime) = _ENTRY.unpack(block[off:off + _ENTRY.size])
            if not pid or t1 <= t0 or not frames:
                continue

            # RTSS leaves an entry behind when a game closes, frozen at
            # its last values. Without this check the chatbox would keep
            # printing the frame rate of a session that ended.
            last = self._seen.get(pid)
            if last and last[0] == frames:
                if now - last[1] > STALE_SEC:
                    continue
            else:
                self._seen[pid] = (frames, now)

            value = frames * 1000.0 / (t1 - t0)
            if not (0.0 < value < 10000.0):
                continue
            name = raw_name.split(b"\x00", 1)[0].decode("utf-8", "replace")
            name = os.path.basename(name.replace("\\", "/"))
            item = (value, ftime / 1000.0 if ftime else 0.0, name)
            if self.want and self.want in name.lower():
                # the process the user asked for wins outright
                wanted = item
                break
            if best is None or value > best[0]:
                best = item

        # forget processes that are no longer listed, so the dict cannot
        # grow for the lifetime of the app
        if len(self._seen) > 64:
            self._seen.clear()

        chosen = wanted or best
        if chosen is None:
            self.error = ("RTSS is running but no game is reporting - "
                          "start the game, or check that RTSS is set to "
                          "monitor it.")
        else:
            self.error = ""
        return chosen

    # ---------------------------------------------------------- status
    def status_line(self):
        if not IS_WINDOWS:
            return "RTSS is Windows-only."
        if self.ok is None:
            return "Not checked yet."
        if self.ok is False:
            return (self.error or "RTSS is not running.") + \
                " It ships with MSI Afterburner."
        hit = self.read()
        if hit:
            return f"Active - reading {hit[2]} ({hit[0]:.0f} fps)."
        return self.error or "RTSS is running, waiting for a game."


def running():
    """Is RTSS's shared memory there at all? Used by the Check button."""
    if not IS_WINDOWS:
        return False
    try:
        import mmap
        block = mmap.mmap(-1, 0, tagname=MAP_NAME, access=mmap.ACCESS_READ)
        block.close()
        return True
    except Exception:                           # noqa: BLE001
        return False
