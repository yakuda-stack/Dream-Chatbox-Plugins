"""util.py - shared plumbing for every module of the suite.

Three things live here so the modules stay short:

    Poller      a daemon thread that calls a function on an interval and
                keeps the result behind a lock. Nothing in this plugin is
                allowed to block the GUI thread, so every module that
                talks to the disk, the network or a subprocess owns one.
    deploy()    copies a bundled shell script into a writable cache dir
                and patches lines in it (the plugin folder is read-only
                inside an AppImage).
    small formatting helpers shared by all modules.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path


# ------------------------------------------------------------- paths
def cache_dir(name=""):
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    d = Path(base) / "osc-dreamchatbox" / "vrcosc_modules"
    if name:
        d = d / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def plugin_dir():
    return Path(__file__).resolve().parent


# ------------------------------------------------------------ poller
class Poller:
    """Calls `tick()` every `interval` seconds in a daemon thread.

    `tick` returns the new snapshot (any object) or None to keep the
    previous one. Exceptions are swallowed and logged once, so a broken
    network or a missing binary can never take the chatbox down with it.
    """

    def __init__(self, tick, log=print, interval=5.0, name="poller"):
        self._tick = tick
        self.log = log
        self._name = name
        self._interval = max(0.5, float(interval))
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._data = None
        self._stamp = 0.0
        self._failed = False

    # -- config ------------------------------------------------------
    def set_interval(self, seconds):
        try:
            seconds = max(0.5, float(seconds))
        except (TypeError, ValueError):
            return
        with self._lock:
            changed = seconds != self._interval
            self._interval = seconds
        if changed:
            self._wake.set()

    def poke(self):
        """Run the next tick immediately instead of waiting out the sleep."""
        self._wake.set()

    # -- lifecycle ---------------------------------------------------
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self._name,
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3.0)
        with self._lock:
            self._data = None
            self._stamp = 0.0

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    # -- data --------------------------------------------------------
    def snapshot(self, max_age=None):
        """Latest result, or None when there is none or it went stale."""
        with self._lock:
            data, stamp = self._data, self._stamp
        if data is None:
            return None
        if max_age is not None and (time.time() - stamp) > max_age:
            return None
        return data

    def age(self):
        with self._lock:
            return time.time() - self._stamp if self._stamp else None

    def push(self, data):
        """For modules that receive data instead of polling for it."""
        with self._lock:
            self._data = data
            self._stamp = time.time()

    # -- loop --------------------------------------------------------
    def _loop(self):
        while not self._stop.is_set():
            try:
                result = self._tick()
                if result is not None:
                    self.push(result)
                self._failed = False
            except Exception as e:
                if not self._failed:          # log once, not every tick
                    self._failed = True
                    self.log(f"{self._name}: {e}")
            with self._lock:
                delay = self._interval
            self._wake.wait(delay)
            self._wake.clear()


# ------------------------------------------------------------ script
def deploy(script_name, folder, patches=(), override=""):
    """Put a bundled shell script somewhere writable and patch it.

    `patches` is a list of (regex, replacement) applied once each with
    re.MULTILINE - that is how the upstream scripts take their settings:
    plain assignments at the top of the file that VRCOSC rewrites at
    deploy time. Returns the Path of the runnable copy, or None.
    """
    if shutil.which("bash") is None:
        raise RuntimeError("bash not found")
    src = Path(override).expanduser() if override else None
    if src is None or not src.is_file():
        src = plugin_dir() / script_name
    text = src.read_text(encoding="utf-8", errors="replace")
    for pattern, replacement in patches:
        text = re.sub(pattern, replacement.replace("\\", "\\\\"),
                      text, count=1, flags=re.M)
    target = cache_dir(folder) / script_name
    target.write_text(text, encoding="utf-8")
    target.chmod(0o755)
    return target


def run_script(path, timeout=15.0, args=()):
    env = dict(os.environ)
    env.setdefault("LC_ALL", "C")            # keep the decimal point a dot
    subprocess.run(["bash", str(path), *args],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=timeout, env=env, check=False)


def read_lines(path, minimum=1):
    try:
        lines = Path(path).read_text(encoding="utf-8",
                                     errors="replace").splitlines()
    except OSError:
        return None
    return lines if len(lines) >= minimum else None


def which(*names):
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def pgrep(pattern, exact=True):
    """True when a process matches. Cheap enough for a 3 s interval."""
    args = ["pgrep", "-x" if exact else "-f", pattern]
    try:
        proc = subprocess.run(args, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


# -------------------------------------------------------- formatting
def cut(text, limit):
    """Hard cut, no ellipsis - chatbox characters are scarce."""
    text = str(text or "").strip()
    limit = to_int(limit, 0)
    if limit > 0 and len(text) > limit:
        text = text[:limit].rstrip()
    return text


def join(*parts):
    text = " ".join(str(p) for p in parts if p not in (None, ""))
    return text or None


def to_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value, low, high, default=None):
    value = to_int(value, default if default is not None else low)
    return max(low, min(high, value))


def mmss(seconds):
    seconds = max(0, to_int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def bar(fraction, width=10, full="█", empty="░"):
    width = clamp(width, 4, 20)
    fraction = max(0.0, min(1.0, to_float(fraction)))
    done = int(round(fraction * width))
    return full * done + empty * (width - done)


def dig(data, path, default=None):
    """Walk a dotted path through nested dicts/lists: 'a.b.0.c'."""
    if not path:
        return data
    node = data
    for part in str(path).split("."):
        if isinstance(node, dict):
            if part not in node:
                return default
            node = node[part]
        elif isinstance(node, list):
            idx = to_int(part, -1)
            if idx < 0 or idx >= len(node):
                return default
            node = node[idx]
        else:
            return default
    return node
