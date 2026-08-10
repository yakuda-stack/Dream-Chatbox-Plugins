"""Starting and stopping one program – the part that touches the system.

A target is one of three things:

    path        a file the user picked: .sh, an AppImage, a binary, a
                .py, on Windows an .exe, .bat, .cmd or a .lnk shortcut
    command     a line the user typed, exactly as a terminal would take
                it, shell syntax included
    oscleash    the OSCLeash plugin next door, started through its own
                manager instead of as a second process

Two things are done deliberately and are easy to get wrong the other way:

* every child gets its own session (Linux) or process group (Windows), so
  stopping it takes the whole tree with it. A ``.sh`` that execs the real
  program is the normal case, and terminating only the shell leaves the
  program running forever.
* stdout goes nowhere. These are games and overlays, not services; they
  print megabytes, and a pipe nobody reads fills up and blocks the child.
  Whoever needs the output starts it from a terminal.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import procs

IS_WINDOWS = os.name == "nt"
SHELL_CHARS = "|&;<>$`*?~\n"
TERM_WAIT = 6.0          # how long a polite terminate gets before the kill

DEFAULTS = {
    "name": "Program",
    "kind": "path",          # path | command | oscleash
    "path": "",
    "args": "",
    "workdir": "",
    "delay": 0,              # seconds after the trigger fired
    "stop_with": True,       # stop again when the trigger is gone
    "stop_cmd": "",          # instead of terminating the process
    "skip_if_running": True,
    "enabled": True,
}


def new_target(name="", kind="path"):
    data = dict(DEFAULTS)
    data["id"] = uuid.uuid4().hex[:8]
    data["name"] = name or DEFAULTS["name"]
    data["kind"] = kind
    return data


# ------------------------------------------------------------ argv
def python_exe():
    """A real interpreter for a .py target.

    ``sys.executable`` is the chatbox itself in a PyInstaller build, and
    handing that a script argument opens a second chatbox instead of
    running the script.
    """
    if not getattr(sys, "frozen", False) and sys.executable \
            and "python" in Path(sys.executable).name.lower():
        return sys.executable
    for name in ("python3", "python", "py"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def base_name(path):
    """The file name of a path, whichever slash it was written with.

    ``Path("C:\\Games\\VRChat.exe").name`` is the whole string on Linux,
    because a backslash is a perfectly ordinary character in a POSIX file
    name. Rules are stored as json and travel between machines, so this
    splits on both separators rather than trusting the host.
    """
    raw = str(path or "").strip().rstrip("/\\")
    for sep in ("\\", "/"):
        raw = raw.rsplit(sep, 1)[-1]
    return raw


def split_args(text):
    text = str(text or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=not IS_WINDOWS)
    except ValueError:               # unbalanced quote – take it as typed
        return text.split()


def build_argv(kind, path, args=""):
    """(argv, needs_shell, error). ``error`` is a sentence for the user."""
    raw = str(path or "").strip()
    if not raw:
        return None, False, "nothing to start – pick a file or type a command"

    if kind == "command":
        line = raw
        extra = str(args or "").strip()
        if extra:
            line = f"{line} {extra}"
        if any(ch in line for ch in SHELL_CHARS):
            # pipes, &&, variables: that is a shell line, so give it one
            return (["cmd", "/c", line] if IS_WINDOWS
                    else ["/bin/sh", "-c", line]), True, ""
        argv = split_args(line)
        if not argv:
            return None, False, "the command is empty"
        exe = argv[0]
        if not Path(exe).is_absolute() and shutil.which(exe) is None:
            return None, False, f"'{exe}' was not found on PATH"
        return argv, False, ""

    p = Path(raw).expanduser()
    if not p.is_file():
        return None, False, f"{p} does not exist"
    suffix = p.suffix.lower()
    tail = split_args(args)

    if suffix == ".py":
        exe = python_exe()
        if not exe:
            return None, False, ("no python interpreter found – needed to "
                                 "run a .py file")
        return [exe, str(p)] + tail, False, ""
    if IS_WINDOWS:
        if suffix in (".bat", ".cmd"):
            return ["cmd", "/c", str(p)] + tail, True, ""
        if suffix == ".lnk":
            # a shortcut is not an executable; the shell resolves it, and
            # `start` is the only thing that does so without COM
            return ["cmd", "/c", "start", "", str(p)] + tail, True, ""
        return [str(p)] + tail, False, ""

    if not os.access(str(p), os.X_OK):
        if suffix in (".sh", ".bash", ""):
            # a script without +x is normal – an AppImage without it is
            # a mistake worth naming, because it silently never starts
            return ["/bin/sh", str(p)] + tail, False, ""
        return None, False, (f"{p.name} is not executable – "
                             f"chmod +x it first")
    return [str(p)] + tail, False, ""


class Target:
    """One program, and at most one process started by this plugin."""

    def __init__(self, data, log=None):
        self.data = dict(DEFAULTS, **(data or {}))
        self.data.setdefault("id", uuid.uuid4().hex[:8])
        self.log = log or (lambda _m: None)
        self.proc = None
        self.pid = 0
        self.kids = []            # tree at start time, for the kill sweep
        self.error = ""
        self.started_at = 0.0
        self.due_at = 0.0         # set while a delayed start is pending
        self._recent = set()      # pids just stopped, see external_pids()
        self._recent_until = 0.0
        self.link = None          # set for kind == "oscleash"

    # ------------------------------------------------------------ ids
    @property
    def tid(self):
        return self.data["id"]

    @property
    def name(self):
        return str(self.data.get("name") or "").strip() or "Program"

    @property
    def kind(self):
        return str(self.data.get("kind") or "path")

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value

    # --------------------------------------------------------- status
    @property
    def running(self):
        if self.kind == "oscleash":
            return bool(self.link and self.link.running_count())
        return self.proc is not None and self.proc.poll() is None

    def external_pids(self, snap=None):
        """The program running without this plugin having started it.

        Someone who launched SteamVR by hand does not want a second copy
        five seconds later, so this is what ``skip_if_running`` asks.
        """
        if self.kind == "oscleash":
            return []
        pattern = self.match_pattern()
        if not pattern:
            return []
        mine = set(self.kids)
        if self.pid:
            mine.add(self.pid)
        # the snapshot is cached for a moment, so a process this plugin
        # killed a second ago is still in it – without this the panel says
        # "running (started elsewhere)" right after a Stop
        if time.time() < self._recent_until:
            mine |= self._recent
        return procs.find(pattern, ignore_pids=mine, snap=snap)

    def match_pattern(self):
        """What "this program is already running" means for this target.

        For a picked file the file name is specific enough – nothing else
        on the machine is called ``wlx-overlay-s``. For a typed command it
        is not: the first word of "flatpak run com.example.App" is
        ``flatpak``, and matching on that would call every Flatpak on the
        system "already running" and quietly start nothing. So a command
        matches on the whole line instead, minus any shell syntax.
        """
        raw = str(self.get("path") or "").strip()
        if not raw:
            return ""
        if self.kind != "command":
            return base_name(raw).lower()
        if any(ch in raw for ch in SHELL_CHARS):
            # a shell line runs as `sh -c "…"`, so its own text is what
            # shows up in the process list
            argv = split_args(raw)
            return (base_name(argv[0]).lower() if argv else raw.lower())
        argv = split_args(raw)
        if not argv:
            return ""
        argv[0] = base_name(argv[0])
        return " ".join(argv).lower()

    def state_text(self):
        if self.kind == "oscleash":
            count = self.link.running_count() if self.link else 0
            if not self.link or not self.link.available():
                return "OSCLeash plugin not loaded"
            return f"{count} leash(es) running" if count else "stopped"
        if self.running:
            return f"running (pid {self.pid})"
        if self.due_at:
            left = max(0, int(self.due_at - time.time()))
            return f"starting in {left}s"
        if self.error:
            return self.error
        if self.external_pids():
            return "running (started elsewhere)"
        return "stopped"

    # ---------------------------------------------------------- start
    def start(self, force=False):
        """Returns "" on success, otherwise the reason. ``force`` is the
        manual button: it ignores ``skip_if_running``."""
        self.due_at = 0.0
        if not self.get("enabled") and not force:
            return ""
        if self.kind == "oscleash":
            if self.link is None or not self.link.available():
                self.error = ("the OSCLeash plugin is not loaded – switch it "
                              "on in the plugin list")
                return self.error
            self.error = self.link.start() or ""
            return self.error
        if self.running:
            return ""
        if self.get("skip_if_running") and not force and self.external_pids():
            self.error = ""
            self.log(f"{self.name}: already running, not started again")
            return ""

        argv, _shell, err = build_argv(self.kind, self.get("path"),
                                       self.get("args"))
        if err:
            self.error = err
            return err

        workdir = str(self.get("workdir") or "").strip()
        if not workdir and self.kind == "path":
            workdir = str(Path(str(self.get("path"))).expanduser().parent)
        if workdir and not Path(workdir).is_dir():
            workdir = None

        kwargs = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0))
        else:
            # own session: stopping the target takes its whole tree, and
            # a wrapper script cannot outlive the program it started
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(
                argv, cwd=workdir or None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **kwargs)
        except OSError as e:
            self.proc = None
            self.error = f"start failed: {e}"
            return self.error
        self.pid = self.proc.pid
        self.kids = []
        self.started_at = time.time()
        self.error = ""
        self.log(f"{self.name}: started – {' '.join(argv)} (pid {self.pid})")
        return ""

    def note_children(self):
        """Remember the tree once it exists, so the pids are still known
        after a wrapper script exited and left the real program behind."""
        if self.pid and not IS_WINDOWS and self.running:
            found = procs.children_of(self.pid)
            if found:
                self.kids = found

    # ----------------------------------------------------------- stop
    def stop(self):
        if self.kind == "oscleash":
            if self.link is not None and self.link.available():
                self.link.stop()
            return

        custom = str(self.get("stop_cmd") or "").strip()
        if custom:
            self._run_stop_cmd(custom)

        proc, self.proc = self.proc, None
        pid = self.pid
        self.pid = 0
        self._recent = {p for p in ([pid] + list(self.kids)) if p}
        self._recent_until = time.time() + 5.0
        if proc is None or proc.poll() is not None:
            self._sweep(pid)
            return

        try:
            if IS_WINDOWS:
                # /T for the tree: an .exe that spawned a launcher child
                # would otherwise survive its parent
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               timeout=10, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess,
                                                     "CREATE_NO_WINDOW", 0))
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=TERM_WAIT)
        except Exception:
            try:
                if not IS_WINDOWS:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        self._sweep(pid)
        self.log(f"{self.name}: stopped")

    def _sweep(self, pid):
        """Anything left of the tree, after the direct child is gone."""
        leftovers = [p for p in self.kids if p and p != pid]
        self.kids = []
        if not leftovers or IS_WINDOWS:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            alive = []
            for child in leftovers:
                try:
                    os.kill(child, sig)
                    alive.append(child)
                except OSError:
                    pass
            if not alive:
                return
            time.sleep(0.4)

    def _run_stop_cmd(self, line):
        argv = (["cmd", "/c", line] if IS_WINDOWS else ["/bin/sh", "-c", line])
        try:
            subprocess.run(argv, timeout=20, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0)
                           if IS_WINDOWS else 0)
            self.log(f"{self.name}: stop command ran")
        except Exception as e:
            self.log(f"{self.name}: stop command failed – {e}")

    def reap(self):
        """True the moment a process we started has ended by itself."""
        if self.proc is None or self.kind == "oscleash":
            return False
        if self.proc.poll() is None:
            return False
        self.proc = None
        self.pid = 0
        self.kids = []
        return True
