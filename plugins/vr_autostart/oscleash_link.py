"""Talking to the OSCLeash plugin instead of to a process.

OSCLeash already has a supervisor: its plugin owns the instance list, the
generated configs, the restart-on-crash watchdog and the debug consoles.
Starting ``OSCLeash.py`` from here a second time would give the user two
processes fighting over port 9001 and one of them invisible.

So this asks the neighbour instead. Both plugins are imported into the
same interpreter, so its manager object is simply *there* – the module is
found by looking through ``sys.modules`` for the one that owns a leash
manager. That is a loose coupling on purpose: no import, no dependency in
the manifest, and when the OSCLeash plugin is switched off or was never
installed, everything here answers "not available" and the rest of the
plugin carries on.

Nothing in here assumes a version. Every call is guarded, because the
neighbour is allowed to change without asking this plugin first.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import sys

# what a module must have to be the OSCLeash plugin
_MANAGER_API = ("start_all", "stop_all", "running_count", "instances")


class OSCLeashLink:
    """Read-through handle on the OSCLeash plugin's manager."""

    def __init__(self, log=None, plugin_id="oscleash"):
        self.log = log or (lambda _m: None)
        self.plugin_id = plugin_id
        self._said = ""            # last message, so the log says it once

    # ------------------------------------------------------- discovery
    def manager(self):
        """The live LeashManager, or None.

        Looked up every time rather than cached: the user can switch the
        OSCLeash plugin off and on again while this one keeps running,
        and a cached manager would then be a dead object whose stop_all()
        stops nothing.
        """
        for name, mod in list(sys.modules.items()):
            if mod is None or self.plugin_id not in name.lower():
                continue
            man = getattr(mod, "_manager", None)
            if man is None:
                continue
            if all(hasattr(man, attr) for attr in _MANAGER_API):
                return man
        return None

    def available(self):
        return self.manager() is not None

    def running_count(self):
        man = self.manager()
        if man is None:
            return 0
        try:
            return int(man.running_count())
        except Exception:
            return 0

    def total_count(self):
        man = self.manager()
        if man is None:
            return 0
        try:
            return len(man.instances)
        except Exception:
            return 0

    # --------------------------------------------------------- control
    def start(self):
        """Start every configured leash. Returns "" or a reason."""
        man = self.manager()
        if man is None:
            return ("the OSCLeash plugin is not loaded – install it and "
                    "switch it on")
        try:
            man.start_all()
        except Exception as e:
            return f"OSCLeash could not be started: {e}"
        self._say(f"OSCLeash: {self.running_count()}/{self.total_count()} "
                  f"running")
        return ""

    def stop(self):
        man = self.manager()
        if man is None:
            return ""
        try:
            man.stop_all()
        except Exception as e:
            return f"OSCLeash could not be stopped: {e}"
        self._say("OSCLeash: stopped")
        return ""

    def describe(self):
        man = self.manager()
        if man is None:
            return "OSCLeash plugin not loaded"
        return (f"OSCLeash plugin \u00b7 {self.running_count()}/"
                f"{self.total_count()} leashes running")

    def _say(self, text):
        if text != self._said:
            self._said = text
            self.log(text)
