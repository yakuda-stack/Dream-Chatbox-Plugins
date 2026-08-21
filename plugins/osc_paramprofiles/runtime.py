"""All shared state, in a module that is never the entry point.

This file exists because of a bug that produced "The plugin is not
running" while the plugin was plainly running.

``plugin.json`` says ``"main": "main.py"``, and the loader makes that
file *be* the package - that is what lets ``from .panel import …`` work
inside it. So main.py is registered in sys.modules as
``osc_paramprofiles``. When panel.py then did ``from . import main``,
python imported main.py a *second* time, as ``osc_paramprofiles.main``:
a separate module object, with its own globals, all still None. The
panel was reading a completely different copy of the plugin than the one
the app had called setup() on.

The template avoids this by passing state into build_widget() as
arguments rather than importing it back. Doing that here would mean
threading half a dozen objects through, so instead everything lives in
this module - a real submodule, imported the same way from both sides,
so there is exactly one of it either way.

Nothing here imports Qt.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import threading
import time

from . import discovery as vrcq
from . import oscio, store

#: How long to wait for VRChat to notice our announcement before
#: withdrawing and re-registering.
ANNOUNCE_GRACE = 20.0
MAX_REANNOUNCE = 3


class Runtime:
    """One of these, created by main.setup(). Everything the panel needs
    hangs off it, so the panel never touches module globals it might not
    share."""

    def __init__(self):
        self.api = None
        self.bridge = None
        self.finder = None
        self.store = None

        #: "off" | "starting" | "running" | "error" - the panel says
        #: something different for each, because "not running" while a
        #: background thread is still registering with mDNS is a lie.
        self.phase = "off"
        self.startup_note = ""

        self.last_profile = ""
        self.last_profile_at = 0.0

        self._apply_lock = threading.Lock()
        self._apply = {"active": False, "done": 0, "total": 0, "name": "",
                       "failed": 0}
        self._apply_thread = None

        self._watch_thread = None
        self._watch_stop = threading.Event()
        self._watch_lock = threading.Lock()
        self._watch = {"polls": 0, "last_poll": 0.0, "merged": 0,
                       "retries": 0, "note": ""}
        self._start_thread = None
        self._published_tree = False

    # --------------------------------------------------------- helpers
    def log(self, text):
        if self.api is not None:
            try:
                self.api.log(str(text))
            except Exception:
                pass

    def get(self, key, default=None):
        if self.api is None:
            return default
        try:
            return self.api.get(key, default)
        except Exception:
            return default

    def set(self, key, value):
        if self.api is not None:
            try:
                if self.api.supports("api.set"):
                    self.api.set(key, value)
            except Exception:
                pass

    def data_dir(self):
        if self.api is None:
            return "."
        try:
            self.api.ensure_data_dir()
        except Exception:
            pass
        folder = getattr(self.api, "data_dir", None)
        if not folder:
            folder = os.path.join(os.path.expanduser("~"), ".config",
                                  "osc-dreamchatbox", "osc_paramprofiles")
            os.makedirs(folder, exist_ok=True)
        return folder

    # ------------------------------------------------------- lifecycle
    def setup(self, api):
        """Must return in milliseconds.

        Everything that talks to the network is started on a thread.
        setup() runs on the GUI thread, and registering an mDNS service
        blocks for seconds while zeroconf probes the network for a name
        conflict - which is exactly the freeze that showed up when the
        plugin was switched on, and again on every reconnect.
        """
        self.api = api
        self.log(f"OSC Parameter Profiles starting on {api.app_name} "
                 f"{api.app_version}")

        folder = self.data_dir()
        self.store = store.Store(folder, log=self.log)
        self.log(f"{len(self.store.profiles())} profile(s) in {folder}")

        self.bridge = oscio.Bridge(log=self.log)
        self.finder = vrcq.Discovery(log=self.log)

        if not vrcq.zeroconf_available():
            self.phase = "error"
            self.startup_note = "python-zeroconf is not installed"
            self.set("status", "⚠ python-zeroconf is not installed")
            self.log("python-zeroconf is missing - the plugin cannot work "
                     "without it")
            return

        # instant: bind the socket so we can already receive and send
        self.bridge.open_socket()
        self.start_network()

    def start_network(self, reset=False):
        """Announce and browse, on a thread. Safe to call again.

        ``reset`` tears the old registration down first. That teardown is
        as slow as the setup - joining the watchdog, cancelling the mDNS
        browser and sending goodbye packets all block - so it belongs on
        the same thread, not on the click that asked for it.
        """
        if self._start_thread is not None and self._start_thread.is_alive():
            return
        self.phase = "starting"
        self.startup_note = ("reconnecting…" if reset
                             else "announcing over mDNS…")
        self.set("status", self.startup_note)
        self._start_thread = threading.Thread(
            target=self._start_network, args=(reset,),
            name="paramprofiles-start", daemon=True)
        self._start_thread.start()

    def _start_network(self, reset=False):
        try:
            if reset:
                self._stop_watchdog()
                if self.finder is not None:
                    self.finder.stop()
                if self.bridge is not None:
                    self.bridge.withdraw(keep_announcing=True)
            name = str(self.get("service_name", "") or
                       "OSC-DreamChatbox ParamProfiles")
            self.startup_note = "registering the OSCQuery service…"
            ok = self.bridge.announce(name)
            self.startup_note = "looking for VRChat…"
            self.finder.start()
            self.phase = "running" if ok else "error"
            self.startup_note = "" if ok else (self.bridge.error or
                                               "could not announce")
            self._published_tree = False
            self._start_watchdog()
            self.publish_status()
        except Exception as exc:      # a thread that dies takes the
            self.phase = "error"      # feature with it and says nothing
            self.startup_note = f"startup failed: {exc}"
            self.log(self.startup_note)

    def teardown(self):
        self.cancel_apply()
        if self.store is not None:
            self.store.close()   # let queued writes land before we go
        self._stop_watchdog()
        if self.finder is not None:
            self.finder.stop()
        if self.bridge is not None:
            self.bridge.stop()
        self.finder = None
        self.bridge = None
        self.phase = "off"
        self.set("status", "stopped")

    def restart(self):
        """Reconnect, without blocking the click that asked for it."""
        if self.bridge is None:
            return "not running"
        self.start_network(reset=True)
        return "reconnecting…"

    # ---------------------------------------------------------- status
    def publish_status(self):
        if self.bridge is None:
            self.set("status", "stopped")
            return
        info = self.bridge.status()
        found = self.finder.status() if self.finder else {}
        if self.phase == "starting":
            self.set("status", self.startup_note or "starting…")
        elif info["error"]:
            self.set("status", f"⚠ {info['error']}")
        elif not found.get("found"):
            self.set("status", f"announced on tcp/{info['http']} · "
                               "waiting for VRChat")
        else:
            self.set("status", f"VRChat found · sending to "
                               f"{self.bridge.send_port} · "
                               f"{info['params']} parameters")

    def watch_state(self):
        with self._watch_lock:
            return dict(self._watch)

    def note(self, text):
        with self._watch_lock:
            self._watch["note"] = str(text)
        self.log(text)

    # -------------------------------------------------------- watchdog
    def _start_watchdog(self):
        self._stop_watchdog()
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._watchdog, name="paramprofiles-watch", daemon=True)
        self._watch_thread.start()

    def _stop_watchdog(self):
        self._watch_stop.set()
        thread, self._watch_thread = self._watch_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _watchdog(self):
        """Poll, retarget, re-announce - the three things that used to be
        the user's job."""
        retries = 0
        last_poll = 0.0
        while not self._watch_stop.is_set():
            self._watch_stop.wait(1.0)
            if self._watch_stop.is_set() or self.bridge is None:
                continue
            if self.finder is None:
                continue

            found = self.finder.service is not None

            if (not found and self.bridge.announced
                    and retries < MAX_REANNOUNCE
                    and time.monotonic() - self.bridge.announced_at
                    > ANNOUNCE_GRACE):
                retries += 1
                self.note(f"VRChat did not answer, re-announcing "
                          f"({retries}/{MAX_REANNOUNCE})")
                self.bridge.reannounce()
                with self._watch_lock:
                    self._watch["retries"] = retries
                continue
            if found and retries:
                retries = 0
                with self._watch_lock:
                    self._watch["retries"] = 0

            if found:
                host, port, discovered = self.finder.send_target()
                if port != self.bridge.send_port or \
                        host != self.bridge.send_host:
                    self.bridge.set_target(host, port, discovered)
                    self.note(f"sending to {host}:{port}")

            interval = self.poll_interval()
            if found and interval and \
                    time.monotonic() - last_poll >= interval:
                last_poll = time.monotonic()
                self.poll_once()

    def poll_interval(self):
        try:
            return max(0.0, float(self.get("poll_seconds", 5) or 0))
        except (TypeError, ValueError):
            return 5.0

    def poll_once(self):
        """Fetch VRChat's tree and merge it. Any thread. -1 on failure."""
        if self.finder is None or self.bridge is None:
            return -1
        if self.finder.service is None:
            return -1
        started = time.monotonic()
        if not self.finder.service.osc_port:
            self.finder.read_host_info()

        avatar = self.finder.fetch_avatar()
        if avatar and avatar != self.bridge.avatar:
            self.bridge.ingest("/avatar/change", [avatar])
            self.note(f"avatar is now {avatar}")

        parameters = self.finder.fetch_parameters()
        if parameters is None:
            self.note("VRChat's OSCQuery server did not answer")
            return -1
        merged = self.bridge.ingest_tree(parameters, started)
        with self._watch_lock:
            self._watch["polls"] += 1
            self._watch["last_poll"] = time.time()
            self._watch["merged"] = merged

        # The first pull is what breaks the chicken-and-egg: our own
        # advertised tree started empty, so VRChat had no parameter paths
        # of ours to push to. Now that we know the names, re-announce
        # once with a populated tree.
        if parameters and not self._published_tree:
            self._published_tree = True
            self.note(f"publishing {len(parameters)} parameter paths so "
                      "VRChat can push updates")
            self.bridge.reannounce()
        return merged

    def refresh_now(self):
        if self.finder is None or self.bridge is None:
            return "not running"
        if self.finder.service is None:
            threading.Thread(target=self.bridge.reannounce, daemon=True
                             ).start()
            return ("VRChat has not been discovered yet - re-announced, give "
                    "it a few seconds")
        threading.Thread(target=self.poll_once, name="paramprofiles-poll",
                         daemon=True).start()
        return "asking VRChat for the full parameter list…"

    def open_folder(self):
        import subprocess
        folder = self.data_dir()
        try:
            if os.name == "nt":
                os.startfile(folder)  # noqa: S606 - windows only
            else:
                subprocess.Popen(["xdg-open", folder],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            return folder
        except Exception as exc:
            return f"{folder} ({exc})"

    # --------------------------------------------------------- capture
    def capture(self, skip_builtin=None, skip_driven=None):
        if self.bridge is None:
            return {}
        if skip_builtin is None:
            skip_builtin = bool(self.get("skip_builtin", True))
        if skip_driven is None:
            skip_driven = bool(self.get("skip_driven", True))
        out = {}
        for name, (value, tag, _w) in self.bridge.detailed().items():
            if not self.bridge.is_writable(name, skip_builtin, skip_driven):
                continue
            out[name] = {"t": tag or oscio.type_of(value), "v": value}
        return out

    # ----------------------------------------------------------- apply
    def apply_profile(self, profile, on_done=None):
        if self.bridge is None or not profile:
            return False
        with self._apply_lock:
            if self._apply["active"]:
                return False
            self._apply.update({"active": True, "done": 0, "failed": 0,
                                "name": profile["name"],
                                "total": len(profile.get("params") or {})})

        delay = max(0, int(self.get("send_delay_ms", 8) or 0)) / 1000.0
        repeat = max(1, min(3, int(self.get("send_repeat", 1) or 1)))
        verify = bool(self.get("verify_after_send", True))

        def worker():
            failed = 0
            items = sorted((profile.get("params") or {}).items())
            for round_no in range(repeat):
                for index, (name, entry) in enumerate(items, 1):
                    with self._apply_lock:
                        if not self._apply["active"]:
                            return
                    value = oscio.cast(entry.get("v"), entry.get("t", "f"))
                    if not self.bridge.send(name, value):
                        failed += 1
                    if round_no == 0:
                        with self._apply_lock:
                            self._apply["done"] = index
                    if delay:
                        time.sleep(delay)

            mismatched = []
            if verify:
                time.sleep(0.4)
                mismatched = self.verify(profile)

            self.last_profile = profile["name"]
            self.last_profile_at = time.time()
            with self._apply_lock:
                self._apply.update({"active": False, "failed": failed})
            extra = f", {len(mismatched)} did not take" if mismatched else ""
            self.log(f"applied '{profile['name']}' ({len(items)} parameters, "
                     f"{failed} send errors{extra})")
            if self.api is not None:
                try:
                    self.api.refresh()
                except Exception:
                    pass
            if on_done is not None:
                try:
                    on_done(failed, mismatched)
                except Exception:
                    pass

        self._apply_thread = threading.Thread(
            target=worker, name="paramprofiles-apply", daemon=True)
        self._apply_thread.start()
        return True

    def verify(self, profile):
        """Read the tree back and return the names that still differ."""
        if self.finder is None or self.finder.service is None:
            return []
        current = self.finder.fetch_parameters()
        if not current:
            return []
        off = []
        for name, entry in (profile.get("params") or {}).items():
            if name not in current:
                continue
            wanted = oscio.cast(entry.get("v"), entry.get("t", "f"))
            if not _same(wanted, current[name][0]):
                off.append(name)
        return off

    def cancel_apply(self):
        with self._apply_lock:
            self._apply["active"] = False

    def apply_state(self):
        with self._apply_lock:
            return dict(self._apply)

    # ---------------------------------------------------- placeholders
    def values(self):
        out = {"profile": None, "count": None, "avatar": None,
               "category": None}
        if self.bridge is None:
            return out
        try:
            hold = float(self.get("show_seconds", 0) or 0)
        except (TypeError, ValueError):
            hold = 0.0
        if self.last_profile and (hold <= 0 or
                                  time.time() - self.last_profile_at <= hold):
            out["profile"] = self.last_profile
            if self.store is not None:
                match = next((p for p in self.store.profiles()
                              if p["name"] == self.last_profile), None)
                if match and match["category"]:
                    out["category"] = match["category"]
        if self.bridge.count:
            out["count"] = str(self.bridge.count)
        if self.bridge.avatar:
            out["avatar"] = self.bridge.avatar
        return out

    def text(self):
        if not self.get("show_in_chatbox", False):
            return ""
        values = self.values()
        if not values["profile"]:
            return ""
        icon = str(self.get("icon", "") or "").strip()
        return " ".join(p for p in (icon, values["profile"]) if p)


def _same(wanted, got):
    """Floats need a tolerance - a value that made the round trip through
    a 32-bit OSC float and back is never bit-identical."""
    if isinstance(wanted, bool) or isinstance(got, bool):
        return bool(wanted) == bool(got)
    if isinstance(wanted, float) or isinstance(got, float):
        try:
            return abs(float(wanted) - float(got)) <= 1e-3
        except (TypeError, ValueError):
            return False
    return wanted == got


#: The one instance. Both main.py and panel.py reach it through this
#: module, so it does not matter which of them the loader treats as the
#: package - there is only ever one Runtime.
state = Runtime()
