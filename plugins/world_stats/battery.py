"""
battery.py – headset / controller / tracker battery for World Stats
(bundled with the plugin; the app itself carries none of this)

There is no single place a VR headset reports its charge on Linux, so
this file has three backends and picks whichever answers:

  monado  the running OpenXR runtime itself, through libmonado - see
          monado.py and monado_worker.py next to this file. The library
          is dlopened in a child process, never in the app, so a crash
          in it costs a log line instead of the session. This is the one for WiVRn and
          Monado: the headset battery the WiVRn client streams up from
          the Android side, plus controllers and trackers, with no adb
          connection to set up and no SteamVR involved. Costs one
          library call per poll and never starts a runtime.

  adb     the headset itself, over `adb shell dumpsys battery`.
          Works for every Android based standalone – Quest 1/2/3/Pro,
          Pico, Vive Focus, Vive XR Elite – and it does not care how the
          picture gets there: WiVRn, ALVR, Virtual Desktop, Link, or no
          streaming at all. USB or `adb connect <ip>:5555`. It reports
          the headset only; Android knows nothing about the controllers.

  openvr  SteamVR's own device list, through the `openvr` pip package.
          This one also covers controllers and trackers (Index, Vive,
          Tundra, SlimeVR-as-OpenVR …) and it is the only backend that
          works for a wired headset with a battery pack. Requires
          SteamVR to be running – we init as a BACKGROUND app, which
          never starts SteamVR by itself, so polling costs nothing when
          it is not there.

Auto order on Linux is monado, then openvr, then adb: the runtime is
the cheapest and the most likely to answer, adb the most work for the
user. On Windows monado is skipped entirely.

Everything happens in a daemon thread. adb is a subprocess and openvr
is a C library; neither belongs anywhere near the GUI thread. The GUI
reads snapshot(), which is a dict copy under a lock.

No hard dependencies – adb is looked up on PATH, openvr is imported in
a try block. When neither is available the plugin says so once and the
battery placeholders simply stay empty.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"

# keep adb's console window from flashing up on every poll
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

_RE_LEVEL = re.compile(r"^\s*level:\s*(\d+)", re.M)
_RE_SCALE = re.compile(r"^\s*scale:\s*(\d+)", re.M)
_RE_STATUS = re.compile(r"^\s*status:\s*(\d+)", re.M)

# android BatteryManager.BATTERY_STATUS_*
_CHARGING = {2, 5}

# openvr ETrackedDeviceProperty ids – hard-coded so this module stays
# importable (and testable) without the package installed
_PROP_HAS_BATTERY = 1028
_PROP_BATTERY_PCT = 1029
_PROP_IS_CHARGING = 1030
_PROP_ROLE_HINT = 3007
_PROP_MODEL_NUMBER = 1001


def _run(cmd, timeout=6.0):
    """A subprocess that can never take the app down with it."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, creationflags=_NO_WINDOW) \
            if IS_WINDOWS else \
            subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except Exception as e:
        return 1, "", str(e)


def find_adb(explicit=""):
    """The adb binary: explicit setting, PATH, then the usual corners."""
    explicit = (explicit or "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        return str(p) if p.is_file() else ""
    found = shutil.which("adb")
    if found:
        return found
    guesses = []
    home = Path.home()
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            guesses.append(Path(local) / "Android" / "Sdk" / "platform-tools"
                           / "adb.exe")
        guesses.append(home / "platform-tools" / "adb.exe")
    else:
        guesses += [
            home / "Android" / "Sdk" / "platform-tools" / "adb",
            home / ".local" / "share" / "android-sdk" / "platform-tools"
            / "adb",
            Path("/opt/android-sdk/platform-tools/adb"),
            Path("/usr/lib/android-sdk/platform-tools/adb"),
        ]
    for g in guesses:
        try:
            if g.is_file():
                return str(g)
        except Exception:
            continue
    return ""


def adb_devices(adb=""):
    """[(serial, state)] – state is 'device', 'unauthorized', 'offline'."""
    adb = adb or find_adb()
    if not adb:
        return []
    rc, out, _ = _run([adb, "devices"], timeout=8.0)
    if rc != 0:
        return []
    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


def adb_connect(adb, target):
    """`adb connect <ip[:port]>` – returns adb's own answer."""
    target = (target or "").strip()
    if not target:
        return "no address given"
    if ":" not in target:
        target += ":5555"
    adb = adb or find_adb()
    if not adb:
        return "adb not found"
    rc, out, err = _run([adb, "connect", target], timeout=12.0)
    return (out or err or "no answer").strip().splitlines()[-1]


class BatteryMonitor:
    """Polls whichever backend is available, in its own thread.

    start() once, read snapshot() as often as you like, stop() when the
    plugin goes away. set_config() may be called at any time; the next
    poll picks the new values up."""

    def __init__(self, log_fn=print):
        self.log = log_fn
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()

        # config (guarded by _lock)
        self._source = "auto"
        self._interval = 60.0
        self._adb_path = ""
        self._adb_serial = ""
        self._want_controllers = True

        # state (guarded by _lock)
        self._state = {
            "ok": False, "source": "", "device": "", "error": "",
            "hmd": None, "controllers": [], "trackers": [], "at": 0.0,
        }

        # backend bookkeeping – thread-local in practice, only the
        # worker touches these
        self._vr = None
        self._vr_failed_at = 0.0
        self._mnd = None
        self._mnd_failed_at = 0.0
        self._mnd_note = ""
        self._adb_models = {}
        self._said_missing = False

    # ----------------------------------------------------------- config
    def set_config(self, source="auto", interval=60, adb_path="",
                   adb_serial="", controllers=True):
        with self._lock:
            self._source = (source or "auto").strip().lower()
            self._interval = max(10.0, float(interval or 60))
            self._adb_path = (adb_path or "").strip()
            self._adb_serial = (adb_serial or "").strip()
            self._want_controllers = bool(controllers)
        self._wake.set()

    def _cfg(self):
        with self._lock:
            return (self._source, self._interval, self._adb_path,
                    self._adb_serial, self._want_controllers)

    # -------------------------------------------------------- lifecycle
    def start(self):
        if self._thread and self._thread.is_alive():
            self._wake.set()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        self._shutdown_openvr()
        self._shutdown_monado()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def poll_now(self):
        """Ask for a poll on the next loop pass instead of waiting out
        the interval. Returns immediately – the caller reads snapshot()
        a moment later."""
        self._wake.set()

    def snapshot(self):
        with self._lock:
            s = dict(self._state)
        s["controllers"] = list(s.get("controllers") or [])
        s["trackers"] = list(s.get("trackers") or [])
        return s

    # --------------------------------------------------------------- run
    def _run(self):
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as e:                       # never die
                self._set_error(f"battery poll failed: {e}")
            _, interval, _, _, _ = self._cfg()
            self._wake.wait(interval)
            self._wake.clear()

    def _poll(self):
        source, _, adb_path, adb_serial, want_ctl = self._cfg()

        default = ("openvr", "adb") if IS_WINDOWS else \
            ("monado", "openvr", "adb")
        order = {"adb": ("adb",), "openvr": ("openvr",),
                 "monado": ("monado",)}.get(source, default)

        errors = []
        for backend in order:
            if self._stop.is_set():
                return
            try:
                if backend == "openvr":
                    got = self._read_openvr(want_ctl)
                elif backend == "monado":
                    got = self._read_monado(want_ctl)
                else:
                    got = self._read_adb(adb_path, adb_serial)
            except Exception as e:
                got, errors = None, errors + [f"{backend}: {e}"]
                continue
            if got:
                got["at"] = time.time()
                got["ok"] = True
                got["error"] = ""
                with self._lock:
                    self._state = got
                self._said_missing = False
                return
            errors.append(f"{backend}: nothing")

        self._set_error("; ".join(errors) or "no battery source")

    def _set_error(self, msg):
        with self._lock:
            self._state = {"ok": False, "source": "", "device": "",
                           "error": msg, "hmd": None, "controllers": [],
                           "trackers": [], "at": time.time()}
        if not self._said_missing:
            self._said_missing = True
            self.log(f"battery: {msg}")

    # ------------------------------------------------------------ monado
    def _monado(self):
        """The live libmonado connection, or None.

        Same shape as _openvr(): a failed connect is throttled for 30s so
        polling while no runtime is up stays free. A runtime that gets
        restarted invalidates the root, which shows up as an exception on
        the next call and drops us back here."""
        if self._mnd is not None:
            return self._mnd
        if time.time() - self._mnd_failed_at < 30.0:
            return None
        try:
            from .monado import Monado
        except Exception as e:
            self._mnd_failed_at = time.time()
            self._mnd_note = f"monado.py not importable: {e}"
            return None
        try:
            self._mnd = Monado(log_fn=self.log)
        except Exception as e:
            self._mnd_failed_at = time.time()
            self._mnd_note = str(e)
            return None
        self._mnd_note = f"libmonado {self._mnd.version[0]}." \
                         f"{self._mnd.version[1]}"
        return self._mnd

    def _shutdown_monado(self):
        mnd, self._mnd = self._mnd, None
        if mnd is None:
            return
        try:
            mnd.close()
        except Exception:
            pass

    def _read_monado(self, want_controllers):
        """None when there is nothing to report *or* when the worker
        misbehaved - monado.py logs that case itself and holds its own
        cooldown, so it must not look like a backend failure here.
        A reported failure (no runtime, libmonado too old) does raise,
        and that drops the handle for 30s."""
        if IS_WINDOWS:
            return None
        mnd = self._monado()
        if mnd is None:
            # surface why rather than a bare "nothing" - "no runtime
            # running" and "libmonado too old" are different problems
            # and the Status row is where people read them
            raise RuntimeError(self._mnd_note or "not available")
        try:
            return mnd.read(want_controllers)
        except Exception as e:
            # the runtime went away mid-read - drop the handle rather
            # than keep asking a socket that is not there
            self._shutdown_monado()
            self._mnd_failed_at = time.time()
            raise RuntimeError(str(e))

    # --------------------------------------------------------------- adb
    def _read_adb(self, adb_path, wanted_serial):
        adb = find_adb(adb_path)
        if not adb:
            return None
        devices = [s for s, state in adb_devices(adb) if state == "device"]
        if not devices:
            return None
        serial = wanted_serial if wanted_serial in devices else devices[0]

        rc, out, _ = _run([adb, "-s", serial, "shell", "dumpsys", "battery"])
        level = scale = status = None
        if rc == 0 and out:
            m = _RE_LEVEL.search(out)
            level = int(m.group(1)) if m else None
            m = _RE_SCALE.search(out)
            scale = int(m.group(1)) if m else 100
            m = _RE_STATUS.search(out)
            status = int(m.group(1)) if m else None
        if level is None:
            # some ROMs keep dumpsys behind a permission; the sysfs node
            # is world readable on every headset I have seen
            rc, out, _ = _run([adb, "-s", serial, "shell", "cat",
                               "/sys/class/power_supply/battery/capacity"])
            out = (out or "").strip()
            if rc != 0 or not out.isdigit():
                return None
            level, scale = int(out), 100

        pct = int(round(level * 100.0 / max(1, scale or 100)))
        pct = max(0, min(100, pct))

        model = self._adb_models.get(serial)
        if model is None:
            rc, out, _ = _run([adb, "-s", serial, "shell", "getprop",
                               "ro.product.model"], timeout=5.0)
            model = (out or "").strip() if rc == 0 else ""
            model = model.replace("_", " ") or serial
            self._adb_models[serial] = model

        return {"source": "adb", "device": model,
                "hmd": {"pct": pct, "charging": status in _CHARGING,
                        "name": model},
                "controllers": [], "trackers": []}

    # ------------------------------------------------------------ openvr
    def _openvr(self):
        """The live openvr session, or None. Retries at most every 30s
        so a poll costs nothing while SteamVR is not running."""
        if self._vr is not None:
            return self._vr
        if time.time() - self._vr_failed_at < 30.0:
            return None
        try:
            import openvr
        except Exception:
            self._vr_failed_at = time.time()
            return None
        try:
            # BACKGROUND never launches SteamVR, it only attaches to a
            # running one – that is what makes this safe to poll
            openvr.init(openvr.VRApplication_Background)
        except Exception:
            self._vr_failed_at = time.time()
            return None
        self._vr = openvr
        return openvr

    def _shutdown_openvr(self):
        vr, self._vr = self._vr, None
        if vr is None:
            return
        try:
            vr.shutdown()
        except Exception:
            pass

    def _read_openvr(self, want_controllers):
        vr = self._openvr()
        if vr is None:
            return None
        try:
            system = vr.VRSystem()
            if system is None:
                raise RuntimeError("no VRSystem")
            hmd, controllers, trackers, device = None, [], [], ""
            for idx in range(vr.k_unMaxTrackedDeviceCount):
                cls = system.getTrackedDeviceClass(idx)
                if cls == vr.TrackedDeviceClass_Invalid:
                    continue
                name = self._vr_string(system, idx, _PROP_MODEL_NUMBER)
                if cls == vr.TrackedDeviceClass_HMD and not device:
                    device = name
                if not self._vr_bool(system, idx, _PROP_HAS_BATTERY):
                    continue
                raw = system.getFloatTrackedDeviceProperty(
                    idx, _PROP_BATTERY_PCT)
                pct = max(0, min(100, int(round(float(raw) * 100.0))))
                entry = {"pct": pct,
                         "charging": self._vr_bool(system, idx,
                                                   _PROP_IS_CHARGING),
                         "name": name}
                if cls == vr.TrackedDeviceClass_HMD:
                    hmd = entry
                elif cls == vr.TrackedDeviceClass_Controller:
                    role = 0
                    try:
                        role = int(system.getInt32TrackedDeviceProperty(
                            idx, _PROP_ROLE_HINT))
                    except Exception:
                        role = 0
                    entry["role"] = {1: "L", 2: "R"}.get(role, "")
                    controllers.append(entry)
                else:
                    trackers.append(entry)
        except Exception as e:
            self._shutdown_openvr()
            self._vr_failed_at = time.time()
            raise RuntimeError(str(e))

        if hmd is None and not controllers and not trackers:
            return None
        if not want_controllers:
            controllers, trackers = [], []
        controllers.sort(key=lambda c: c.get("role") or "z")
        return {"source": "openvr", "device": device or "SteamVR",
                "hmd": hmd, "controllers": controllers, "trackers": trackers}

    @staticmethod
    def _vr_bool(system, idx, prop):
        try:
            return bool(system.getBoolTrackedDeviceProperty(idx, prop))
        except Exception:
            return False

    @staticmethod
    def _vr_string(system, idx, prop):
        try:
            return str(system.getStringTrackedDeviceProperty(idx, prop) or "")
        except Exception:
            return ""


# ------------------------------------------------------------- helpers
def openvr_available():
    """(installed, note) – for the status row in the settings."""
    try:
        import openvr                                     # noqa: F401
    except Exception:
        return False, "the openvr package is not installed"
    return True, "installed – needs SteamVR running"


def monado_available():
    """(usable, note) – for the status row in the settings."""
    try:
        from .monado import available
    except Exception as e:
        return False, f"monado.py not importable: {e}"
    return available()


def install_openvr(log_fn=print):
    """pip install openvr, in the interpreter the app runs on.

    Blocking; call it from a thread. Returns a short human sentence."""
    import sys
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "openvr"]
    if not getattr(sys, "frozen", False) and sys.prefix == sys.base_prefix:
        cmd.insert(4, "--user")        # not a venv -> keep out of /usr
    log_fn("battery: " + " ".join(cmd))
    rc, out, err = _run(cmd, timeout=300.0)
    if rc == 0:
        return "openvr installed – restart the app"
    tail = (err or out or "").strip().splitlines()
    return "install failed: " + (tail[-1] if tail else f"exit {rc}")
