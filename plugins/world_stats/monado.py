"""
monado.py – battery out of the running OpenXR runtime (WiVRn / Monado),
read through a child process so a C crash cannot take the app with it.

Everything that touches libmonado lives in monado_worker.py and runs in
its own interpreter. This file only ever spawns it, waits at most two
seconds, and reads one JSON line back:

    python3 monado_worker.py probe          -> path, version, device count
    python3 monado_worker.py read           -> the device list

Why the split: a python exception is recoverable, a segfault in a
dlopened C library is not. libmonado talks to an IPC socket of a runtime
that can be killed, restarted or updated underneath us mid-call, and the
chatbox is a long-running app people leave open for a whole session.
In-process that is a small chance of the app vanishing without a log
line; out of process the same event is a returncode of -11 and a
warning. adb already costs a process per poll, so this is not a new kind
of expense - it is the same one, for the same reason.

Three outcomes, kept apart on purpose:

    exit 0    a real answer, JSON on stdout
    exit 1    a python-side failure the worker itself reported - most
              often "no runtime running". Raised as MonadoError, which
              makes battery.py drop the handle and back off for 30s.
    anything  the worker died on a signal or ran past the timeout.
    else      Logged as a warning and answered with None, never raised:
              the poll simply produces nothing and the next backend in
              the chain gets its turn. A short cooldown follows so a
              reproducible hang costs one two-second wait, not one per
              poll.

The library lookup itself (LIBMONADO_PATH, XR_RUNTIME_JSON, the XDG
active_runtime.json chain) stays in the worker and is imported from
there for the status row - it is pure python and reads two files, it
never calls CDLL.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# The worker gets two seconds. A healthy answer is a dlopen and a
# handful of IPC round trips - single digit milliseconds. Anything past
# this is a runtime that is not answering, and waiting longer would only
# make the battery poll thread sit on a dead socket.
TIMEOUT = 2.0

# How long to stop spawning after a crash or a timeout. The same length
# as battery.py's own backend cooldown, so a permanently broken
# libmonado costs one attempt every 30s at worst.
COOLDOWN = 30.0

IS_WINDOWS = platform.system() == "Windows"
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

_WORKER = str(Path(__file__).resolve().parent / "monado_worker.py")


class MonadoError(RuntimeError):
    """A failure the worker reported about itself - exit 1 with a
    reason. Distinct from a crash, which never raises."""


# ------------------------------------------------------- the interpreter
def python_exe():
    """An interpreter that can run a script.

    sys.executable is the answer in a normal install and in a venv. In a
    frozen build - PyInstaller, or an AppImage that bundles its own
    python - sys.executable is the app itself, and handing it a script
    path just starts a second copy of the chatbox. Those fall back to
    whatever python is on PATH; if there is none, this backend reports
    itself unavailable instead of guessing."""
    if not getattr(sys, "frozen", False):
        exe = sys.executable
        if exe and Path(exe).is_file():
            return exe
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    return ""


# --------------------------------------------------------- the transport
def _run_worker(args):
    """(payload, error, crashed).

    payload is the parsed JSON on success, error a human sentence, and
    crashed says whether the failure was the worker dying rather than
    the worker reporting."""
    exe = python_exe()
    if not exe:
        return None, ("no python interpreter to run the isolated worker "
                      "with"), False

    cmd = [exe, "-I", "-B", _WORKER] + list(args)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    try:
        kwargs = {"capture_output": True, "text": True, "timeout": TIMEOUT,
                  "env": env}
        if IS_WINDOWS:
            kwargs["creationflags"] = _NO_WINDOW
        proc = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        # run() has already killed it by the time this arrives
        return None, f"no answer within {TIMEOUT:g}s", True
    except Exception as e:
        return None, f"could not start the worker: {e}", True

    rc = proc.returncode
    payload = _parse(proc.stdout)

    if rc == 0:
        if payload is None:
            return None, "the worker answered with something that is not "\
                         "JSON", True
        return payload, "", False

    if rc == 1 and payload is not None and not payload.get("ok", False):
        # the expected, boring failure: the worker looked and found no
        # runtime, or a libmonado without the battery call
        return None, str(payload.get("error") or "unknown worker error"), \
            False

    # negative -> killed by a signal: -11 is the segfault this whole file
    # exists for. Positive and unexpected -> the interpreter itself gave
    # up, which is the same class of problem from here.
    if rc < 0:
        why = f"crashed with signal {-rc}"
    else:
        why = f"exited with code {rc}"
    tail = (proc.stderr or "").strip().splitlines()
    if tail:
        why += f" ({tail[-1][:160]})"
    return None, why, True


def _parse(stdout):
    """The last JSON object on stdout, or None.

    Last, not first: libmonado and the runtimes it talks to have been
    known to write a line or two of their own before python gets a word
    in, and losing the answer to somebody else's banner would be a silly
    way to fail."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            got = json.loads(line)
        except Exception:
            continue
        if isinstance(got, dict):
            return got
    return None


# ------------------------------------------------------------- the client
class Monado:
    """Stands in for the old in-process connection.

    Same surface battery.py already uses - version, path, read(),
    close() - so nothing above it had to learn about the subprocess.
    There is no long-lived connection behind it any more: constructing
    this runs one probe, and every read() is its own short-lived child.
    """

    def __init__(self, path="", log_fn=None):
        self.log = log_fn if callable(log_fn) else (lambda _m: None)
        self._cooldown_until = 0.0
        self._warned = ""
        # Why the last read came back empty, for whoever has to put a
        # sentence in front of the user. Cleared by a good read.
        self.last_error = ""

        payload, err, crashed = _run_worker(["probe"])
        if payload is None:
            if crashed:
                # a probe that crashes is still a failed probe: raise, so
                # battery.py treats it like an unavailable backend rather
                # than a working one that answers nothing
                self.log(f"battery: monado worker {err}")
            raise MonadoError(err)

        self.path = str(payload.get("path") or "")
        version = payload.get("version") or [0, 0, 0]
        self.version = tuple(int(v) for v in version[:3])

    # ------------------------------------------------------------- read
    def read(self, want_controllers=True):
        """The device dict, or None.

        None covers both "nothing has a battery" and "the worker could
        not be trusted this time". MonadoError is raised only for the
        clean, reported failures - that is the signal battery.py uses to
        drop this backend for a while."""
        now = time.time()
        if now < self._cooldown_until:
            return None                      # last_error still stands

        args = ["read"]
        if not want_controllers:
            args.append("--no-controllers")

        payload, err, crashed = _run_worker(args)

        if crashed:
            self._cooldown_until = time.time() + COOLDOWN
            self.last_error = err
            if err != self._warned:      # not once per poll
                self._warned = err
                self.log(f"battery: monado worker {err} – skipping the "
                         f"runtime backend for {COOLDOWN:g}s. The app is "
                         f"unaffected; this is why the read runs in its "
                         f"own process.")
            return None

        if payload is None:
            raise MonadoError(err)

        self._warned = ""
        self.last_error = ""
        return payload.get("data") or None

    def close(self):
        """Nothing to hold open - the child is already gone. Here so the
        object keeps the shape battery.py expects."""
        self._cooldown_until = 0.0


# ------------------------------------------------------------- helpers
def library_path():
    """Where libmonado would be loaded from – pure python, no dlopen."""
    try:
        from . import monado_worker
    except Exception:
        return ""
    try:
        return monado_worker.library_path()
    except Exception:
        return ""


def available():
    """(usable, note) for the status row. Reads files, starts nothing."""
    if platform.system() != "Linux":
        return False, "Linux only – Monado/WiVRn does not run here"
    if not python_exe():
        return False, ("no python interpreter found to run the isolated "
                       "worker – the runtime backend stays off")
    path = library_path()
    if not path:
        return False, ("no active OpenXR runtime – start WiVRn or Monado "
                       "once so it writes active_runtime.json")
    return True, f"found {path} (read in a separate process)"
