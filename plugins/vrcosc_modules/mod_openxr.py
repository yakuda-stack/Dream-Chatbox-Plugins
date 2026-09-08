"""OpenXR - which runtime is up, and are you actually in VR.

Bluscream ships three OpenXR modules: Statistics, Gesture Extensions and
Haptic Control. All three load `openxr_loader.dll` and join the running
XR session as an app, which a chatbox side process cannot do on Linux -
there is no second session to join, and hand tracking and haptics are the
headset app's business. What *is* readable from outside is what this
module reports: the configured runtime, whether a compositor is up,
whether a headset session is actually running, and for how long.

Sources, all of them plain files:
    ~/.config/openxr/1/active_runtime.json   the selected runtime
    process list                             vrserver / monado / wivrn
    /run/user/<uid>/wivrn/comp_ipc           WiVRn's session socket
    /proc/<pid>/stat                         how long it has been up
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import subprocess
from pathlib import Path

from . import util

ID = "xr"
NAME = "OpenXR"

KEYS = ("xr_vr", "xr_runtime", "xr_mode", "xr_session", "xr_uptime")

_poller = None

# library_path -> a name a human recognises
_RUNTIMES = (
    ("wivrn", "WiVRn"),
    ("monado", "Monado"),
    ("steamxr", "SteamVR"),
    ("opencomposite", "OpenComposite"),
    ("oxr_", "OpenXR"),
)


def start(ctx):
    global _poller
    stop()
    _poller = util.Poller(_tick, ctx.log, ctx.num("xr_interval", 10, 2, 300),
                          "openxr")
    _poller.start()


def stop():
    global _poller
    if _poller is not None:
        _poller.stop()
    _poller = None


def on_settings(ctx):
    if _poller is not None:
        _poller.set_interval(ctx.num("xr_interval", 10, 2, 300))
        _poller.poke()


# ---------------------------------------------------------------- probe
def _runtime_name():
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    for path in (Path(base) / "openxr/1/active_runtime.json",
                 Path("/etc/xdg/openxr/1/active_runtime.json"),
                 Path("/usr/share/openxr/1/openxr_runtime.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runtime = data.get("runtime") or {}
        name = str(runtime.get("name") or "").strip()
        library = str(runtime.get("library_path") or "").lower()
        for needle, pretty in _RUNTIMES:
            if needle in library or needle in name.lower():
                return pretty
        if name:
            return name
    return ""


def _pid_of(*names):
    for name in names:
        try:
            proc = subprocess.run(["pgrep", "-x", name],
                                  capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        if proc.returncode == 0:
            first = proc.stdout.split()
            if first:
                return util.to_int(first[0]), name
    return None, ""


def _uptime_of(pid):
    """Seconds since the process started - field 22 of /proc/<pid>/stat
    is in clock ticks since boot."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        boot = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, IndexError, ValueError):
        return 0
    # the comm field may contain spaces and brackets, so cut past it
    tail = stat.rsplit(")", 1)[-1].split()
    if len(tail) < 20:
        return 0
    ticks = util.to_float(tail[19])
    hertz = os.sysconf("SC_CLK_TCK") or 100
    return max(0, int(boot - ticks / hertz))


def _wivrn_session():
    """WiVRn idles with its server running; only an established client on
    the compositor socket means a headset is actually in a session."""
    socket_path = f"/run/user/{os.getuid()}/wivrn/comp_ipc"
    if not Path(socket_path).exists():
        return False
    try:
        proc = subprocess.run(["ss", "-xn"], capture_output=True, text=True,
                              timeout=5)
    except Exception:
        return False
    return any(socket_path in row and "ESTAB" in row
               for row in proc.stdout.splitlines())


def _tick():
    pid, process = _pid_of("vrserver", "monado-service", "monado",
                           "wivrn-server")
    if process == "vrserver":
        mode, session = "SteamVR", True
    elif process.startswith("monado"):
        mode, session = "Monado", True
    elif process == "wivrn-server":
        mode, session = "WiVRn", _wivrn_session()
    else:
        mode, session = "Desktop", False
    return {"runtime": _runtime_name(), "mode": mode, "session": session,
            "uptime": _uptime_of(pid) if pid and session else 0}


# --------------------------------------------------------------- values
def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _poller is None:
        return vals
    snap = _poller.snapshot(max_age=max(60.0,
                                        ctx.num("xr_interval", 10, 2, 300) * 4))
    if not snap:
        return vals

    in_vr = snap["session"]
    icon = ctx.text("xr_icon", "🥽") if in_vr else ""

    if ctx.flag("xr_show_runtime", True) and snap["runtime"]:
        vals["xr_runtime"] = snap["runtime"]
    vals["xr_mode"] = snap["mode"] if in_vr or ctx.flag("xr_show_desktop") \
        else None
    if ctx.flag("xr_show_session", True):
        vals["xr_session"] = (ctx.text("xr_in_text", "in VR") if in_vr
                              else ctx.text("xr_out_text")) or None
    if ctx.flag("xr_show_uptime") and snap["uptime"]:
        vals["xr_uptime"] = util.mmss(snap["uptime"])

    vals["xr_vr"] = util.join(icon, vals["xr_mode"], vals["xr_uptime"])
    return vals


def line(vals):
    return [vals["xr_vr"]]
