"""Who is running right now – the only thing a trigger needs to know.

No psutil: the app does not ship it and a plugin that only works when an
optional dependency happens to be installed is a plugin that works on the
developer's machine. psutil is *used* when it is there, because it is the
cheapest of the three paths, but everything below works without it.

    psutil      used when importable
    Linux       /proc/<pid>/comm and /proc/<pid>/cmdline
    Windows     CreateToolhelp32Snapshot through ctypes, tasklist as fallback

A snapshot is (pid, name, cmdline) per process, all lower case, and it is
cached for a moment: the engine asks once per tick, the panel asks for
every trigger row it paints, and walking /proc a dozen times per second
for the same answer would be silly.

Matching is deliberately dumb – a case-insensitive substring, with ``|``
for alternatives. "vrchat" finds VRChat.exe under Proton, wine's own
helper processes and a Flatpak wrapper, and that is exactly what someone
typing "vrchat" meant. ``comm`` is truncated to 15 characters by the
kernel, which is why the command line is searched as well.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import socket
import subprocess
import time

IS_WINDOWS = os.name == "nt"
CACHE_MS = 0.75

_cache = {"at": 0.0, "data": []}

try:
    import psutil                                   # noqa: F401
    HAVE_PSUTIL = True
except Exception:
    HAVE_PSUTIL = False


# ------------------------------------------------------------- backends
def _snapshot_psutil():
    out = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            cmd = " ".join(info.get("cmdline") or []).lower()
            out.append((int(info["pid"]), name, cmd or name))
        except Exception:
            continue
    return out


def _snapshot_proc():
    """/proc, read with as few syscalls as the answer allows."""
    out = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for entry in entries:
        if not entry.isdigit():
            continue
        base = "/proc/" + entry
        try:
            with open(base + "/comm", "rb") as fh:
                name = fh.read(64).decode("utf-8", "replace").strip().lower()
        except OSError:                # died between listdir and open
            continue
        cmd = ""
        try:
            with open(base + "/cmdline", "rb") as fh:
                raw = fh.read(4096)
            cmd = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
            cmd = cmd.strip().lower()
        except OSError:
            pass
        out.append((int(entry), name, cmd or name))
    return out


def _snapshot_toolhelp():
    """The Windows process list, without psutil.

    The declared restypes are not decoration. ``CreateToolhelp32Snapshot``
    returns a HANDLE, and ctypes defaults every return value to C ``int``
    – on 64 bit that truncates the handle to its lower 32 bits, which
    then either fails or, worse, closes something else. Same for the
    argument types: a python int handed to a HANDLE parameter without
    argtypes is passed as 32 bit.
    """
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    entry_p = ctypes.POINTER(PROCESSENTRY32)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,
                                                  wintypes.DWORD]
    kernel32.Process32First.restype = wintypes.BOOL
    kernel32.Process32First.argtypes = [wintypes.HANDLE, entry_p]
    kernel32.Process32Next.restype = wintypes.BOOL
    kernel32.Process32Next.argtypes = [wintypes.HANDLE, entry_p]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    invalid = ctypes.c_void_p(-1).value
    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snap or snap == invalid:
        raise OSError(f"CreateToolhelp32Snapshot failed "
                      f"({ctypes.get_last_error()})")
    out = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32First(snap, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile.decode("utf-8", "replace").lower()
            out.append((int(entry.th32ProcessID), name, name))
            ok = kernel32.Process32Next(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return out


def _snapshot_tasklist():
    """Last resort on Windows. Slow, spawns a process – but it answers."""
    try:
        res = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"], timeout=10,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return []
    out = []
    for line in res.stdout.splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) < 2:
            continue
        name = parts[0].lower()
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        out.append((pid, name, name))
    return out


# -------------------------------------------------------------- public
def snapshot(force=False):
    """[(pid, name, cmdline)] – lower case, cached for CACHE_MS."""
    now = time.time()
    if not force and (now - _cache["at"]) < CACHE_MS:
        return _cache["data"]
    data = []
    try:
        if HAVE_PSUTIL:
            data = _snapshot_psutil()
        elif IS_WINDOWS:
            try:
                data = _snapshot_toolhelp()
            except Exception:
                data = _snapshot_tasklist()
        else:
            data = _snapshot_proc()
    except Exception:
        data = []
    _cache["at"] = now
    _cache["data"] = data
    return data


def backend_name():
    if HAVE_PSUTIL:
        return "psutil"
    return "toolhelp" if IS_WINDOWS else "/proc"


# --------------------------------------------------------- known things
# A trigger may be written as ``@key``. That is not sugar for a process
# name: several of these cannot be answered by a process list alone.
# WiVRn is the obvious one – it runs as a systemd user unit as often as
# it runs from a terminal, and its process is called different things
# depending on whether it came from the AUR, a Flatpak or a build. So a
# ``@key`` asks in the order the answers are cheap: process list first,
# then the runtime's own IPC socket, then systemd.
#
# ``names`` matches the process name, ``cmd`` the whole command line –
# needed for anything under Proton, where the process is called
# VRChat.exe but only the command line says which game it is.
SMART = {
    "vrchat": {
        "label": "VRChat",
        "names": ("vrchat.exe",),
        "cmd": ("steamapps/common/vrchat", "438100", "vrchat.exe"),
    },
    "wivrn": {
        "label": "WiVRn server",
        "names": ("wivrn-server", "wivrn-dashboard"),
        "cmd": ("wivrn-server", "io.github.wivrn.wivrn"),
        "sockets": ("$XDG_RUNTIME_DIR/wivrn_comp_ipc",
                    "$XDG_RUNTIME_DIR/wivrn/comp_ipc"),
        "units": ("wivrn-server.service", "wivrn.service",
                  "wivrn-application.service"),
    },
    "monado": {
        "label": "Monado",
        "names": ("monado-service",),
        "cmd": ("monado-service",),
        "sockets": ("$XDG_RUNTIME_DIR/monado_comp_ipc",),
        "units": ("monado.service", "monado-service.service"),
    },
    "steamvr": {
        "label": "SteamVR",
        "names": ("vrmonitor", "vrserver", "vrcompositor"),
        "cmd": ("steamapps/common/steamvr",),
    },
    "alvr": {"label": "ALVR", "names": ("alvr_dashboard", "alvr_server"),
             "cmd": ("alvr",)},
    "slimevr": {"label": "SlimeVR", "names": ("slimevr",),
                "cmd": ("slimevr",)},
    "wlx": {"label": "WlxOverlay-S", "names": ("wlx-overlay-s",),
            "cmd": ("wlx-overlay-s",)},
    "wayvr": {"label": "WayVR Dashboard", "names": ("wayvr-dashboard",),
              "cmd": ("wayvr",)},
    "vrcx": {"label": "VRCX", "names": ("vrcx",), "cmd": ("vrcx",)},
    "steam": {"label": "Steam", "names": ("steam.exe", "steam"),
              "cmd": ("/steam ", "/steam\n")},
    "resonite": {"label": "Resonite", "names": ("resonite.exe",),
                 "cmd": ("steamapps/common/resonite", "2519830")},
    "chilloutvr": {"label": "ChilloutVR", "names": ("chilloutvr.exe",),
                   "cmd": ("steamapps/common/chilloutvr", "661130")},
    "vrchat_osc": {"label": "VRChat (OSC port 9000)", "names": (),
                   "cmd": ()},
}

PROBE_TTL = 15.0
_probe_cache = {}          # key -> (checked_at, value)


def smart_label(key):
    return SMART.get(key, {}).get("label", key)


def _expand(path):
    return os.path.expandvars(os.path.expanduser(str(path)))


def _socket_alive(paths):
    """A runtime's IPC socket, connected to rather than stat'ed.

    A crashed WiVRn leaves its socket file behind, so "the file exists"
    means nothing. A connect that is refused is a dead socket; one that
    succeeds is a running compositor.
    """
    if IS_WINDOWS or not hasattr(socket, "AF_UNIX"):
        # unix sockets are what WiVRn and Monado use, and both are Linux
        # only – on Windows this question simply does not arise
        return ""
    import stat as stat_mod
    for raw in paths or ():
        path = _expand(raw)
        try:
            if not stat_mod.S_ISSOCK(os.stat(path).st_mode):
                continue
        except OSError:
            continue
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.4)
        try:
            sock.connect(path)
            return path
        except OSError:
            continue
        finally:
            sock.close()
    return ""


def _systemd_active(units, allow_run):
    """Which of ``units`` is active, cached.

    ``allow_run`` is what keeps this off the GUI thread: only the
    watcher thread is allowed to spawn systemctl, the panel reads
    whatever the last tick found. A status LED that is two seconds
    behind is fine; a window that freezes for two seconds is not.
    """
    if IS_WINDOWS or not units:
        return ""
    key = ("systemd",) + tuple(units)
    entry = _probe_cache.get(key)
    fresh = entry is not None and (time.time() - entry[0]) < PROBE_TTL
    if fresh or not allow_run:
        return entry[1] if entry else ""
    found = ""
    for unit in units:
        try:
            res = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", unit],
                timeout=4, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                found = unit
                break
        except Exception:
            continue
    _probe_cache[key] = (time.time(), found)
    return found


def check_command(command, allow_run=False):
    """A terminal command as a trigger: exit code 0 means running.

    ``pgrep -f something``, ``systemctl --user is-active foo``, ``pidof
    bar`` – for everything the process list cannot answer. Cached and,
    like systemd above, only ever run from the watcher thread.
    """
    command = str(command or "").strip()
    if not command:
        return False, ""
    key = ("check", command)
    entry = _probe_cache.get(key)
    fresh = entry is not None and (time.time() - entry[0]) < PROBE_TTL
    if fresh or not allow_run:
        if entry is None:
            return False, "not checked yet"
        return bool(entry[1]), ("exit 0" if entry[1] else "exit code not 0")
    argv = (["cmd", "/c", command] if IS_WINDOWS
            else ["/bin/sh", "-c", command])
    try:
        res = subprocess.run(
            argv, timeout=8, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if IS_WINDOWS else 0)
        ok = res.returncode == 0
    except Exception:
        ok = False
    _probe_cache[key] = (time.time(), ok)
    return ok, ("exit 0" if ok else "exit code not 0")


def forget_probes():
    """Drop every cached answer – for a "check again" after something
    was installed or started outside the app."""
    _probe_cache.clear()


def _match_plain(terms, skip, snap):
    hits = []
    for pid, name, cmd in snap:
        if pid in skip:
            continue
        for term in terms:
            if term in name or term in cmd:
                hits.append(pid)
                break
    return hits


def probe_smart(key, skip, snap, allow_run=False):
    """(pids, how it was found) for one ``@key``."""
    spec = SMART.get(key)
    if spec is None:
        return [], ""
    terms = [t.lower() for t in
             tuple(spec.get("names", ())) + tuple(spec.get("cmd", ()))]
    pids = _match_plain(terms, skip, snap)
    if pids:
        return pids, f"process (pid {pids[0]})"
    path = _socket_alive(spec.get("sockets", ()))
    if path:
        return [-1], "IPC socket " + os.path.basename(path)
    unit = _systemd_active(spec.get("units", ()), allow_run)
    if unit:
        return [-1], f"systemd unit {unit}"
    return [], ""


def _terms(pattern):
    raw = str(pattern or "").lower()
    return [t.strip() for t in raw.split("|") if t.strip()]


def probe(pattern, ignore_pids=(), snap=None, allow_run=False):
    """(running, how) for one trigger, whatever shape it has:

        @wivrn              a known program, asked in several ways
        check:<command>     a terminal command, exit code 0 = running
        anything else       a piece of the process name or command line
    """
    raw = str(pattern or "").strip()
    if not raw:
        return False, ""
    if raw.lower().startswith("check:"):
        ok, how = check_command(raw[6:], allow_run)
        return ok, how
    skip = set(ignore_pids or ())
    skip.add(os.getpid())
    data = snap if snap is not None else snapshot()
    plain, details = [], []
    for term in _terms(raw):
        if term.startswith("@"):
            pids, how = probe_smart(term[1:], skip, data, allow_run)
            if pids:
                plain.extend(pids)
                details.append(how)
        else:
            hits = _match_plain([term], skip, data)
            if hits:
                plain.extend(hits)
                details.append(f"process (pid {hits[0]})")
    if not plain:
        return False, ""
    return True, details[0]


def find(pattern, ignore_pids=(), snap=None):
    """The pids matching ``pattern``. Empty list means: not running.

    ``ignore_pids`` keeps the plugin out of its own mirror – a target
    started from here must not be able to satisfy the trigger that
    started it.
    """
    raw = str(pattern or "").strip()
    if not raw or raw.lower().startswith("check:") or "@" in raw:
        running, _how = probe(raw, ignore_pids, snap)
        return [-1] if running else []
    skip = set(ignore_pids or ())
    skip.add(os.getpid())
    return _match_plain(_terms(raw), skip,
                        snap if snap is not None else snapshot())


def is_running(pattern, ignore_pids=(), snap=None, allow_run=False):
    return probe(pattern, ignore_pids, snap, allow_run)[0]


def children_of(pid, snap=None):
    """Descendant pids of ``pid`` – Linux only, used when a launcher
    process exits and leaves the real program behind (a .sh wrapper, a
    Steam shim). Windows kills the whole tree through taskkill instead."""
    if IS_WINDOWS:
        return []
    found, frontier = set(), {int(pid)}
    for _ in range(6):                 # depth guard, trees are shallow
        step = set()
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/stat", "rb") as fh:
                    parts = fh.read(512).decode("utf-8", "replace").rsplit(")", 1)
                ppid = int(parts[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            if ppid in frontier and int(entry) not in found:
                step.add(int(entry))
        if not step:
            break
        found |= step
        frontier = step
    return sorted(found)
