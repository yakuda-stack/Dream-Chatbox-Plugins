"""
monado_worker.py – the ctypes half of the Monado battery backend,
and the only place in World Stats that dlopens a foreign C library.

This file is never imported for its ctypes side by the running app. It
is executed as a script, in its own interpreter, by monado.py:

    python3 monado_worker.py read [--no-controllers]
    python3 monado_worker.py probe

and answers with one JSON object on stdout. Success is exit 0, any
failure is exit 1 with {"ok": false, "error": ...} - so the caller can
tell "the runtime is not running" (exit 1, a normal state) apart from
"libmonado took the interpreter down with it" (a negative returncode,
i.e. a signal).

That separation is the entire point of this file existing. libmonado is
loaded into the process that calls it, and a segfault or a deadlock in a
C library is not an exception - it is the end of the process. Putting it
in a child means the worst case costs one dead child process and a line
in the log instead of the chatbox disappearing mid-session.

Nothing but the standard library is imported here, and the module has no
relative imports, so it runs both as `python3 monado_worker.py` and as
`from . import monado_worker` (which only ever touches the pure-python
library lookup, never CDLL).

The rest, unchanged from where it started life inside monado.py:

WiVRn and Monado are the same runtime underneath, and both ship
libmonado.so - the small C library their own tools talk to. Since
libmonado 1.4 it answers mnd_root_get_device_battery_status() for every
device the runtime tracks, which for WiVRn includes the headset battery
the client streams up from the Android side, plus the controllers, plus
trackers. It is the same source WayVR reads for its battery display and
for the hmdBattery OSC parameters, only through ctypes instead of Rust.

Finding the library follows libmonado's own auto_connect():

    LIBMONADO_PATH                        if the user set it
    XR_RUNTIME_JSON                       if the user set it
    XDG config .../openxr/1/active_runtime.json

and then the "libmonado_path" key inside that runtime json - absolute,
relative to the json, or a bare soname to resolve on the loader path.
So it lands on whatever runtime is currently active, whether that is
/usr/lib/wivrn, a Flatpak, an Envision build in ~/.local, or Monado
itself, without this file carrying a list of guesses.

mnd_root_create() connects to the IPC socket of a *running* service and
fails when nothing is there. It never starts a runtime, so probing this
while no headset is on costs one failed connect.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import ctypes
import json
import os
import platform
from ctypes.util import find_library
from pathlib import Path

MND_SUCCESS = 0

# xrt_device_name values Monado hands out for the tracker-ish devices.
# Anything in this window that is not on a hand role gets counted as a
# tracker rather than being dropped - same window WayVR uses.
_TRACKER_NAME_IDS = range(4, 9)

_ROLE_HEAD = "head"
_ROLE_LEFT = "left"
_ROLE_RIGHT = "right"
_ROLE_HAND_LEFT = "hand-tracking-left"
_ROLE_HAND_RIGHT = "hand-tracking-right"

_PROP_SERIAL_STRING = 1


class MonadoError(RuntimeError):
    pass


# ------------------------------------------------------------ discovery
def _config_dirs():
    """XDG config dirs, most specific first."""
    out = []
    home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    out.append(Path(home) if home else Path.home() / ".config")
    dirs = os.environ.get("XDG_CONFIG_DIRS", "").strip() or "/etc/xdg"
    out += [Path(d) for d in dirs.split(":") if d.strip()]
    return out


def _runtime_jsons():
    """Every active_runtime.json worth looking at, best first."""
    override = os.environ.get("XR_RUNTIME_JSON", "").strip()
    if override:
        yield Path(override)
    for base in _config_dirs():
        yield base / "openxr" / "1" / "active_runtime.json"
    for base in ("/usr/local/share", "/usr/share"):
        yield Path(base) / "openxr" / "1" / "active_runtime.json"


def _resolve(lib, json_path):
    """The libmonado_path out of a runtime json, made loadable.

    It may be absolute, relative to the json itself, or a bare soname
    that only the loader can find."""
    p = Path(lib)
    if p.is_absolute():
        return str(p) if p.is_file() else ""
    if len(p.parts) > 1:
        p = (json_path.parent / p).resolve()
        return str(p) if p.is_file() else ""
    # bare filename: libmonado.so -> ask the loader
    stem = p.name
    if stem.startswith("lib") and ".so" in stem:
        found = find_library(stem[3:].split(".so")[0])
        if found:
            return found
    return stem          # let CDLL try it against the search path


def library_path():
    """Path to libmonado, or "" – no connection attempt."""
    explicit = os.environ.get("LIBMONADO_PATH", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit

    for jp in _runtime_jsons():
        try:
            if not jp.is_file():
                continue
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        lib = ((data or {}).get("runtime") or {}).get("libmonado_path", "")
        if not lib:
            continue
        got = _resolve(str(lib), jp)
        if got:
            return got
    return ""


# --------------------------------------------------------------- client
class Monado:
    """A live libmonado connection. One per worker thread, please –
    nothing here is re-entrant, and the whole module is only ever
    touched from the battery poll thread."""

    def __init__(self, path=""):
        path = path or library_path()
        if not path:
            raise MonadoError("no OpenXR runtime active")
        try:
            self.lib = ctypes.CDLL(path)
        except OSError as e:
            raise MonadoError(f"{Path(path).name} not loadable: {e}")
        self.path = path
        self.root = ctypes.c_void_p()
        self._bind()

        self.version = self._version()
        if self.version < (1, 4):
            raise MonadoError(
                f"libmonado {self.version[0]}.{self.version[1]} is older "
                f"than the battery API (1.4)")
        if self._battery is None:
            raise MonadoError("this libmonado has no battery call")

        rc = self.lib.mnd_root_create(ctypes.byref(self.root))
        if rc != MND_SUCCESS or not self.root:
            self.root = ctypes.c_void_p()
            raise MonadoError(f"no runtime running (rc {rc})")

    # ------------------------------------------------------------ setup
    def _bind(self):
        L = self.lib
        u32p = ctypes.POINTER(ctypes.c_uint32)
        i32p = ctypes.POINTER(ctypes.c_int32)
        boolp = ctypes.POINTER(ctypes.c_bool)
        f32p = ctypes.POINTER(ctypes.c_float)
        strp = ctypes.POINTER(ctypes.c_char_p)
        void = ctypes.c_void_p

        L.mnd_api_get_version.argtypes = [u32p, u32p, u32p]
        L.mnd_api_get_version.restype = None
        L.mnd_root_create.argtypes = [ctypes.POINTER(void)]
        L.mnd_root_create.restype = ctypes.c_int
        L.mnd_root_destroy.argtypes = [ctypes.POINTER(void)]
        L.mnd_root_destroy.restype = None
        L.mnd_root_get_device_count.argtypes = [void, u32p]
        L.mnd_root_get_device_count.restype = ctypes.c_int
        L.mnd_root_get_device_info.argtypes = [void, ctypes.c_uint32,
                                               u32p, strp]
        L.mnd_root_get_device_info.restype = ctypes.c_int
        L.mnd_root_get_device_from_role.argtypes = [void, ctypes.c_char_p,
                                                    i32p]
        L.mnd_root_get_device_from_role.restype = ctypes.c_int

        # 1.4 and 1.2 additions – present or not, never fatal on bind
        self._battery = self._maybe(
            "mnd_root_get_device_battery_status",
            [void, ctypes.c_uint32, boolp, boolp, f32p])
        self._info_str = self._maybe(
            "mnd_root_get_device_info_string",
            [void, ctypes.c_uint32, ctypes.c_int, strp])

    def _maybe(self, name, argtypes):
        try:
            fn = getattr(self.lib, name)
        except AttributeError:
            return None
        fn.argtypes = argtypes
        fn.restype = ctypes.c_int
        return fn

    def _version(self):
        major = ctypes.c_uint32(0)
        minor = ctypes.c_uint32(0)
        patch = ctypes.c_uint32(0)
        self.lib.mnd_api_get_version(ctypes.byref(major), ctypes.byref(minor),
                                     ctypes.byref(patch))
        return (major.value, minor.value, patch.value)

    def close(self):
        if getattr(self, "root", None):
            try:
                self.lib.mnd_root_destroy(ctypes.byref(self.root))
            except Exception:
                pass
        self.root = ctypes.c_void_p()

    # ----------------------------------------------------------- queries
    def device_count(self):
        count = ctypes.c_uint32(0)
        if self.lib.mnd_root_get_device_count(
                self.root, ctypes.byref(count)) != MND_SUCCESS:
            raise MonadoError("device count refused")
        return int(count.value)

    def device_info(self, index):
        """(name_id, name) for a device index."""
        name_id = ctypes.c_uint32(0)
        name = ctypes.c_char_p()
        if self.lib.mnd_root_get_device_info(
                self.root, ctypes.c_uint32(index), ctypes.byref(name_id),
                ctypes.byref(name)) != MND_SUCCESS:
            return 0, ""
        return int(name_id.value), (name.value or b"").decode(
            "utf-8", "replace")

    def index_from_role(self, role):
        """Device index for a role name, or -1. A role nothing is bound
        to answers with -1 rather than an error, which is the normal
        case for hand tracking on a controller setup."""
        index = ctypes.c_int32(-1)
        rc = self.lib.mnd_root_get_device_from_role(
            self.root, role.encode("utf-8"), ctypes.byref(index))
        if rc != MND_SUCCESS:
            return -1
        return int(index.value)

    def battery(self, index):
        """(present, charging, pct) – pct is 0..100 or None."""
        if self._battery is None:
            return False, False, None
        present = ctypes.c_bool(False)
        charging = ctypes.c_bool(False)
        charge = ctypes.c_float(0.0)
        rc = self._battery(self.root, ctypes.c_uint32(index),
                           ctypes.byref(present), ctypes.byref(charging),
                           ctypes.byref(charge))
        if rc != MND_SUCCESS or not present.value:
            return False, False, None
        pct = max(0, min(100, int(round(float(charge.value) * 100.0))))
        return True, bool(charging.value), pct

    def serial(self, index):
        if self._info_str is None:
            return ""
        out = ctypes.c_char_p()
        rc = self._info_str(self.root, ctypes.c_uint32(index),
                            ctypes.c_int(_PROP_SERIAL_STRING),
                            ctypes.byref(out))
        if rc != MND_SUCCESS:
            return ""
        return (out.value or b"").decode("utf-8", "replace")

    # ------------------------------------------------------------ report
    def read(self, want_controllers=True):
        """The same dict shape battery.py's other backends return."""
        roles = {}
        for role in (_ROLE_HEAD, _ROLE_LEFT, _ROLE_RIGHT,
                     _ROLE_HAND_LEFT, _ROLE_HAND_RIGHT):
            idx = self.index_from_role(role)
            if idx >= 0:
                roles.setdefault(idx, role)

        hmd, controllers, trackers, device = None, [], [], ""
        for index in range(self.device_count()):
            name_id, name = self.device_info(index)
            role = roles.get(index)
            if role == _ROLE_HEAD and not device:
                device = name
            present, charging, pct = self.battery(index)
            if not present or pct is None:
                continue
            entry = {"pct": pct, "charging": charging, "name": name}
            if role == _ROLE_HEAD:
                hmd = entry
            elif role in (_ROLE_LEFT, _ROLE_HAND_LEFT):
                entry["role"] = "L"
                controllers.append(entry)
            elif role in (_ROLE_RIGHT, _ROLE_HAND_RIGHT):
                entry["role"] = "R"
                controllers.append(entry)
            elif name_id in _TRACKER_NAME_IDS:
                trackers.append(entry)
            else:
                # something with a battery that claims no role - a
                # tracker in all but name
                trackers.append(entry)

        if hmd is None and not controllers and not trackers:
            return None
        if not want_controllers:
            controllers, trackers = [], []
        controllers.sort(key=lambda c: c.get("role") or "z")
        return {"source": "monado", "device": device or "OpenXR",
                "hmd": hmd, "controllers": controllers, "trackers": trackers}


# ------------------------------------------------------------- helpers
def available():
    """(usable, note) for the status row – cheap, no connect."""
    if platform.system() != "Linux":
        return False, "Linux only – Monado/WiVRn does not run here"
    path = library_path()
    if not path:
        return False, ("no active OpenXR runtime – start WiVRn or Monado "
                       "once so it writes active_runtime.json")
    return True, f"found {path}"


# ---------------------------------------------------------------- main
def _probe():
    """Load, connect, disconnect. Answers the question "would a read
    work right now" without producing a device list."""
    mnd = Monado()
    try:
        return {"ok": True, "path": mnd.path, "version": list(mnd.version),
                "devices": mnd.device_count()}
    finally:
        mnd.close()


def _read(want_controllers):
    mnd = Monado()
    try:
        return {"ok": True, "data": mnd.read(want_controllers)}
    finally:
        mnd.close()


def main(argv):
    mode = argv[1] if len(argv) > 1 else "read"
    want_controllers = "--no-controllers" not in argv[2:]
    try:
        if mode == "probe":
            out = _probe()
        elif mode == "read":
            out = _read(want_controllers)
        else:
            raise MonadoError(f"unknown mode {mode!r}")
    except Exception as e:
        # every python-side failure leaves through here: no runtime
        # running, libmonado too old, a bad runtime json. stdout stays
        # machine readable in all of them.
        print(json.dumps({"ok": False,
                          "error": f"{type(e).__name__}: {e}"
                          if not isinstance(e, MonadoError) else str(e)}),
              flush=True)
        return 1
    print(json.dumps(out), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
