"""The non-Qt half of the OSCLeash plugin.

One :class:`LeashInstance` owns exactly one OSCLeash process: its own
generated ``Config.json``, its own log ring buffer and its own parsed
state. :class:`LeashManager` owns the list of them, persists it and runs
the watchdog that notices a process dying.

Why several processes instead of one config with several leashes?
OSCLeash's ``DirectionalParameters`` are a single set per config – every
leash listed in ``PhysboneParameters`` shares the same six contacts. The
moment a second leash has its own compass (a tail, a second collar, a
hand-hold prop), it needs its own config, so it needs its own process.
That is what the + button in the panel is for.

Nothing in here imports Qt. The panel polls this module from the GUI
thread; the reader threads only ever touch a deque and a small dict
under a lock, so a stalled or crashing process can never reach Qt.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import math
import os
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .detect import build_command, is_executable, workdir_for
from .runtime import child_env, preflight

MAX_LOG_LINES = 800          # per instance, enough to see a whole startup
WATCH_TICK = 0.5
RESTART_DELAY = 3.0          # grace before a crashed instance comes back
IS_WINDOWS = os.name == "nt"

# terminal noise OSCLeash produces: colour codes, and the escape burst
# `clear` writes on every movement tick while Logging is off
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
                   r"|\x1b[()][B0]|\x1b[=>]|[\x00-\x08\x0b\x0c\x0e-\x1f]")
# OSCLeash prints one colour code as ESC + TAB + "[1;33;40m" instead of
# ESC + "[". Stripping the escape then leaves the "[1;33;40m" sitting in
# the log as text, so the two are glued back together first.
_ANSI_SPLIT = re.compile(r"\x1b\s*\[")
# "\tZ: 0.0,0.75 | X: 0.0,0.25 | Y: 0.0,0.0"  – printDirections()
_DIRS = re.compile(
    r"Z:\s*(-?[\d.]+)\s*,\s*(-?[\d.]+).*?X:\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)"
    r"(?:.*?Y:\s*(-?[\d.]+)\s*,\s*(-?[\d.]+))?", re.I)

DEFAULTS = {
    "name": "Leash",
    "autostart": False,
    "physbones": "Leash",        # comma separated, maps to PhysboneParameters
    "prefix": "",                # "" = derive the contacts from the first one
    "ip": "127.0.0.1",
    "listen_port": 9001,
    "send_port": 9000,
    "oscquery": False,
    # percentages / milliseconds, so the panel can use plain int widgets
    "walk_dz": 15,
    "run_dz": 70,
    "strength": 120,
    "updown_comp": 100,
    "updown_dz": 50,
    "turning": False,
    "turn_mult": 80,
    "turn_dz": 15,
    "turn_goal": 90,             # degrees
    "active_delay": 20,          # ms
    "inactive_delay": 500,       # ms
    "logging": True,             # on by default: it feeds the debug window
}


def new_instance(name="", port=9001):
    inst = dict(DEFAULTS)
    inst["id"] = uuid.uuid4().hex[:8]
    inst["name"] = name or DEFAULTS["name"]
    inst["listen_port"] = port
    return inst


def clean_line(text):
    """Strip escape sequences and trailing whitespace from one log line."""
    return _ANSI.sub("", _ANSI_SPLIT.sub("\x1b[", text)).replace("\r", "").rstrip()


def _compass(x, z):
    """Contact deltas -> an eight point direction, VRChat style: +Z is
    forward, +X is right."""
    if abs(x) < 0.02 and abs(z) < 0.02:
        return ""
    angle = math.degrees(math.atan2(x, z)) % 360
    names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return names[int((angle + 22.5) % 360 // 45)]


class LeashInstance:
    """One configured leash, and at most one process for it."""

    def __init__(self, data, base_dir, log):
        self.data = dict(DEFAULTS, **(data or {}))
        self.data.setdefault("id", uuid.uuid4().hex[:8])
        self.base_dir = Path(base_dir)
        self.log = log
        self.proc = None
        self.lines = deque(maxlen=MAX_LOG_LINES)
        self._lock = threading.Lock()
        self._reader = None
        self._last = ""            # for collapsing repeated lines
        self._repeat = 0
        self._state = {"ready": False, "moved_at": 0.0, "dir": "", "mag": 0.0}
        self.exit_code = None
        self.want_running = False  # what the user asked for, not what is
        self._restart_at = 0.0

    # ------------------------------------------------------------ ids
    @property
    def iid(self):
        return self.data["id"]

    @property
    def name(self):
        return str(self.data.get("name") or "Leash").strip() or "Leash"

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    @property
    def config_path(self):
        return self.base_dir / self.iid / "Config.json"

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value

    # --------------------------------------------------------- config
    def _contacts(self):
        prefix = str(self.get("prefix") or "").strip()
        if not prefix:
            first = self.physbone_list()
            prefix = first[0] if first else "Leash"
            # "Leash_North" is a turning leash – its contacts are still
            # named after the plain stem, so drop the direction suffix
            for tail in ("_North", "_East", "_South", "_West"):
                if prefix.endswith(tail):
                    prefix = prefix[:-len(tail)]
                    break
        return {
            "Z_Positive_Param": f"{prefix}_Z+",
            "Z_Negative_Param": f"{prefix}_Z-",
            "X_Positive_Param": f"{prefix}_X+",
            "X_Negative_Param": f"{prefix}_X-",
            "Y_Positive_Param": f"{prefix}_Y+",
            "Y_Negative_Param": f"{prefix}_Y-",
        }

    def physbone_list(self):
        raw = str(self.get("physbones") or "")
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]

    def build_config(self):
        """The Config.json OSCLeash reads. Written fresh on every start,
        so the panel is always the single source of truth."""
        pct = lambda key: round(float(self.get(key)) / 100.0, 4)  # noqa: E731
        return {
            "IP": str(self.get("ip") or "127.0.0.1"),
            "ListeningPort": int(self.get("listen_port")),
            "SendingPort": int(self.get("send_port")),
            "RunDeadzone": pct("run_dz"),
            "WalkDeadzone": pct("walk_dz"),
            "StrengthMultiplier": pct("strength"),
            "UpDownCompensation": pct("updown_comp"),
            "UpDownDeadzone": pct("updown_dz"),
            "TurningEnabled": bool(self.get("turning")),
            "TurningMultiplier": pct("turn_mult"),
            "TurningDeadzone": pct("turn_dz"),
            "TurningGoal": int(self.get("turn_goal")),
            "ActiveDelay": round(float(self.get("active_delay")) / 1000.0, 4),
            "InactiveDelay": round(float(self.get("inactive_delay")) / 1000.0, 4),
            "Logging": bool(self.get("logging")),
            "XboxJoystickMovement": False,
            "UseOSCQuery": bool(self.get("oscquery")),
            "PhysboneParameters": self.physbone_list() or ["Leash"],
            "DirectionalParameters": self._contacts(),
        }

    def write_config(self):
        path = self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.build_config(), indent=4),
                        encoding="utf-8")
        return path

    # ---------------------------------------------------------- output
    def push(self, text, own=False):
        """Add one line to the ring buffer. ``own`` marks lines the plugin
        wrote itself, so the debug window can tell them apart."""
        text = clean_line(text)
        if not text:
            return
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            # OSCLeash repeats "OSCLeash is Running" on every single tick;
            # collapsing keeps the window readable instead of scrolling a
            # thousand identical lines per minute
            if self.lines and text == self._last:
                self._repeat += 1
                self.lines[-1] = f"{stamp}  {text}   (x{self._repeat + 1})"
                return
            self._last = text
            self._repeat = 0
            self.lines.append(f"{stamp}  {'» ' if own else ''}{text}")

    def log_text(self):
        with self._lock:
            return "\n".join(self.lines)

    def clear_log(self):
        with self._lock:
            self.lines.clear()
            self._last = ""
            self._repeat = 0

    def _parse(self, text):
        """Turn a log line into movement state. Best effort by design: the
        OSC data itself belongs to OSCLeash, and a second listener on the
        same port would be worse than an approximation."""
        low = text.lower()
        if "awaiting input" in low or "oscleash is running" in low:
            self._state["ready"] = True
        if "oscleash is running" in low:
            self._state["moved_at"] = time.time()
            return
        match = _DIRS.search(text)
        if not match:
            return
        try:
            zp, zn, xp, xn = (float(match.group(i)) for i in range(1, 5))
        except (TypeError, ValueError):
            return
        z, x = zp - zn, xp - xn
        mag = min(1.0, math.hypot(z, x))
        self._state.update(ready=True, mag=mag)
        if mag >= float(self.get("walk_dz")) / 100.0:
            self._state["moved_at"] = time.time()
            self._state["dir"] = _compass(x, z)

    def state(self, idle_secs=3.0):
        """(ready, active, direction, magnitude) for the placeholders."""
        if not self.running:
            return False, False, "", 0.0
        s = self._state
        active = bool(s["moved_at"]) and (time.time() - s["moved_at"]) <= idle_secs
        return bool(s["ready"]), active, (s["dir"] if active else ""), s["mag"]

    # ----------------------------------------------------------- start
    def start(self, binary):
        """Spawn the process. Returns "" on success, else the reason."""
        if self.running:
            return ""
        # checked before the process instead of after: OSCLeash's own
        # error path raises a NameError before it can say what went
        # wrong, so a missing module would surface as a traceback about
        # something else entirely
        wants_query = bool(self.get("oscquery"))
        stopper = preflight(wants_query, 0 if wants_query
                            else self.get("listen_port"),
                            str(self.get("ip") or "127.0.0.1"))
        if stopper:
            return stopper
        cmd = build_command(binary)
        if cmd is None:
            return "OSCLeash was not found – set the path in the settings."
        if not is_executable(binary):
            return f"{binary} is not executable (chmod +x)."

        try:
            path = self.write_config()
        except OSError as e:
            return f"could not write the config: {e}"

        # the vendored pythonosc / tinyoscquery go in front of whatever
        # the machine has, so the bundle decides what OSCLeash imports
        env = child_env()
        env["OSCLEASH_CONFIG_PATH"] = str(path)
        # without this the child's stdout is block buffered behind a pipe
        # and the debug window stays empty until it exits
        env["PYTHONUNBUFFERED"] = "1"
        # OSCLeash calls `clear` on every tick while Logging is off; with
        # no TERM that spams an error line instead of an escape sequence
        env.setdefault("TERM", "xterm")

        kwargs = {}
        if IS_WINDOWS:
            # no console window popping up in front of the chatbox
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            # own session: `clear` and any shell child die with the parent
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=workdir_for(binary),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                errors="replace", env=env, **kwargs)
        except OSError as e:
            self.proc = None
            return f"start failed: {e}"

        self.exit_code = None
        self.want_running = True
        self._state = {"ready": False, "moved_at": 0.0, "dir": "", "mag": 0.0}
        self.push(f"started: {' '.join(cmd)}  (pid {self.proc.pid})", own=True)
        self.push(f"config: {path}", own=True)
        self._reader = threading.Thread(
            target=self._read, args=(self.proc,),
            name=f"oscleash-{self.iid}", daemon=True)
        self._reader.start()
        return ""

    def _read(self, proc):
        try:
            for raw in proc.stdout:
                text = clean_line(raw)
                if not text:
                    continue
                self._parse(text)
                self.push(text)
        except Exception as e:                     # pipe died mid-read
            self.push(f"log reader stopped: {e}", own=True)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    # ------------------------------------------------------------ stop
    def stop(self, quiet=False):
        self.want_running = False
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            except Exception:
                pass
        # a Stop the user pressed is not a crash: SIGTERM would show up as
        # "exited (-15)" and send people hunting for a problem that is
        # only them clicking the button
        self.exit_code = None
        self.proc = None
        self._state["ready"] = False
        if not quiet:
            self.push("stopped", own=True)

    def reap(self):
        """Called by the watchdog: notices a process that died on its own
        and returns True when it just now vanished."""
        proc = self.proc
        if proc is None or proc.poll() is None:
            return False
        self.exit_code = proc.poll()
        self.proc = None
        self._state["ready"] = False
        self.push(f"process exited with code {self.exit_code}", own=True)
        return True


class LeashManager:
    """The instance list, its json file and the watchdog thread."""

    def __init__(self, data_dir, log, binary_getter, restart_getter):
        self.dir = Path(data_dir)
        self.log = log
        self._binary = binary_getter        # callables: read at use time, so
        self._restart = restart_getter      # a settings change lands at once
        self.file = self.dir / "instances.json"
        self.instances = []
        self._stop = threading.Event()
        self._watch = None
        self.load()

    # ----------------------------------------------------- persistence
    def load(self):
        raw = []
        try:
            if self.file.is_file():
                raw = json.loads(self.file.read_text(encoding="utf-8"))
        except Exception as e:
            self.log(f"instances.json unreadable ({e}) – starting empty")
            raw = []
        if not isinstance(raw, list):
            raw = []
        base = self.dir / "instances"
        self.instances = [LeashInstance(d, base, self.log)
                          for d in raw if isinstance(d, dict)]
        if not self.instances:
            self.instances = [LeashInstance(new_instance(), base, self.log)]
            self.save()

    def save(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.file.write_text(
                json.dumps([i.data for i in self.instances], indent=2),
                encoding="utf-8")
        except OSError as e:
            self.log(f"could not save instances.json: {e}")

    # ------------------------------------------------------- list ops
    def add(self, name=""):
        used = {int(i.get("listen_port") or 0) for i in self.instances}
        port = 9001
        while port in used:
            port += 1
        inst = LeashInstance(
            new_instance(name or f"Leash {len(self.instances) + 1}", port),
            self.dir / "instances", self.log)
        # a second instance on the same machine can only get avatar data
        # through OSCQuery – VRChat sends to port 9001 exactly once
        if len(self.instances) >= 1:
            inst.set("oscquery", True)
        self.instances.append(inst)
        self.save()
        return inst

    def remove(self, iid):
        inst = self.by_id(iid)
        if inst is None:
            return False
        inst.stop(quiet=True)
        self.instances.remove(inst)
        self.save()
        return True

    def by_id(self, iid):
        for inst in self.instances:
            if inst.iid == iid:
                return inst
        return None

    # --------------------------------------------------------- control
    def start(self, iid):
        inst = self.by_id(iid)
        if inst is None:
            return "unknown instance"
        err = inst.start(self._binary())
        if err:
            inst.push(err, own=True)
            self.log(f"{inst.name}: {err}")
        return err

    def stop(self, iid):
        inst = self.by_id(iid)
        if inst is not None:
            inst.stop()

    def start_all(self):
        for inst in self.instances:
            if not inst.running:
                self.start(inst.iid)

    def stop_all(self):
        for inst in self.instances:
            inst.stop(quiet=True)

    def start_autostart(self):
        for inst in self.instances:
            if inst.get("autostart") and not inst.running:
                self.start(inst.iid)

    def running_count(self):
        return sum(1 for i in self.instances if i.running)

    def port_conflicts(self):
        """Instances that would fight over the same listening port. Only a
        warning: with OSCQuery on, the port in the config is irrelevant."""
        seen, bad = {}, set()
        for inst in self.instances:
            if not inst.running or inst.get("oscquery"):
                continue
            port = int(inst.get("listen_port") or 0)
            if port in seen:
                bad.add(port)
            seen[port] = inst.iid
        return sorted(bad)

    # -------------------------------------------------------- watchdog
    def start_watchdog(self):
        if self._watch is not None:
            return
        self._stop.clear()
        self._watch = threading.Thread(target=self._loop,
                                       name="oscleash-watchdog", daemon=True)
        self._watch.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                now = time.time()
                for inst in list(self.instances):
                    if inst.reap() and inst.want_running:
                        if self._restart():
                            inst._restart_at = now + RESTART_DELAY
                            inst.push("restarting in "
                                      f"{RESTART_DELAY:.0f}s", own=True)
                        else:
                            inst.want_running = False
                    elif (inst.want_running and not inst.running
                            and inst._restart_at and now >= inst._restart_at):
                        inst._restart_at = 0.0
                        err = inst.start(self._binary())
                        if err:
                            inst.push(err, own=True)
                            inst.want_running = False
            except Exception as e:
                self.log(f"watchdog: {e}")
            self._stop.wait(WATCH_TICK)

    def shutdown(self, stop_processes=True):
        self._stop.set()
        self._watch = None
        if stop_processes:
            self.stop_all()
        self.save()
