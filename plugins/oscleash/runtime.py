"""Where the bundled OSCLeash lives and what starts it.

OSCLeash is python and this plugin is python, so the plugin simply
carries it: ``vendor/OSCLeash/`` next to this file. No download, no AUR,
no AppImage, no chmod - installing the plugin installs OSCLeash, and the
Start button runs the script that is already there. Same on Windows and
on Linux.

``vendor/`` also holds the two libraries OSCLeash imports, so a plain
python with nothing installed can run it:

    pythonosc       python-osc, public domain
    tinyoscquery    MIT, only reached when OSCQuery is switched on

See VENDOR.md for versions, origins and the one modification.

The interpreter is the part that needs care. ``sys.executable`` is
python when the chatbox runs from source and it is *the chatbox itself*
when it runs as a PyInstaller build - handing that a script argument
would open a second chatbox instead of starting OSCLeash. So a frozen
build looks for a real python on PATH instead.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
LEASH_DIR = VENDOR_DIR / "OSCLeash"
LEASH_SCRIPT = LEASH_DIR / "OSCLeash.py"
# module -> what it is needed for, checked before a start that needs it
OPTIONAL_MODULES = {"zeroconf": "OSCQuery"}

_module_cache = {}


# ------------------------------------------------------------ bundle
def bundled_script():
    """The OSCLeash that ships with this plugin, or "" when the vendor
    folder did not survive whatever unpacked the plugin."""
    return str(LEASH_SCRIPT) if LEASH_SCRIPT.is_file() else ""


def bundle_ok():
    return LEASH_SCRIPT.is_file() and (LEASH_DIR / "Controllers").is_dir()


def child_env(base=None):
    """Environment additions for a child process: the vendored libraries
    go in front of whatever the system has, so the bundle decides which
    python-osc is used and not the machine."""
    env = dict(base or os.environ)
    if VENDOR_DIR.is_dir():
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (str(VENDOR_DIR) + os.pathsep + current
                             if current else str(VENDOR_DIR))
    return env


# ------------------------------------------------------- interpreter
def _looks_like_python(path):
    return "python" in Path(str(path)).name.lower()


def python_candidates():
    """Interpreters worth trying, best first. The one running the chatbox
    comes first when it is a real python: it is the one that definitely
    exists and definitely works."""
    out = []
    if not getattr(sys, "frozen", False) and sys.executable \
            and _looks_like_python(sys.executable):
        out.append(sys.executable)
    for name in ("python3", "python", "py"):
        found = shutil.which(name)
        if found and found not in out:
            out.append(found)
    return out


def _run(cmd, timeout=30, env=None):
    kwargs = {}
    if IS_WINDOWS:
        # no console window flashing up in front of the chatbox
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        cmd, timeout=timeout, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        errors="replace", **kwargs)


def python_exe():
    """The interpreter that will run OSCLeash, or "" when there is none.

    Cached: this spawns a process, and the panel asks often.
    """
    if "exe" in _module_cache:
        return _module_cache["exe"]
    found = ""
    for exe in python_candidates():
        try:
            if _run([exe, "-c", "import sys"]).returncode == 0:
                found = exe
                break
        except Exception:
            continue
    _module_cache["exe"] = found
    return found


def has_module(name, exe=""):
    """Whether the interpreter can import a module, with the vendored
    folder counted in. Cached per interpreter and module."""
    exe = exe or python_exe()
    if not exe:
        return False
    key = (exe, name)
    if key in _module_cache:
        return _module_cache[key]
    try:
        res = _run([exe, "-c",
                    f"import importlib.util as u,sys;"
                    f"sys.exit(0 if u.find_spec({name!r}) else 1)"],
                   env=child_env())
        ok = res.returncode == 0
    except Exception:
        ok = False
    _module_cache[key] = ok
    return ok


def forget_probes():
    """Drop the caches. For a 'check again' button after the user
    installed python or a missing module."""
    _module_cache.clear()


def port_free(port, ip="127.0.0.1"):
    """Whether a UDP port can still be bound.

    Worth asking because the failure mode is otherwise silent: a second
    listener on 9001 does not error in any obvious place, it simply
    never receives anything while VRChat happily sends to whoever got
    there first.

    The chatbox itself is not the competitor here - it asks the OS for a
    free port (bind to 0) and announces it over OSCQuery, so it never
    holds 9001. VRCFaceTracking, another OSC tool or a leftover OSCLeash
    from a previous run very much can.
    """
    try:
        port = int(port)
    except (TypeError, ValueError):
        return True
    if port <= 0:
        return True
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((ip, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def preflight(needs_oscquery=False, port=0, ip="127.0.0.1"):
    """What would stop a start, as a readable sentence. Empty = fine.

    OSCLeash's own error path raises a NameError before it can tell
    anyone what really went wrong (its restart branch uses `sys` without
    importing it), so a missing module has to be caught out here or the
    user is left staring at a traceback about the wrong thing.
    """
    if not bundle_ok():
        return ("the bundled OSCLeash is missing from the plugin's vendor "
                "folder - reinstall the plugin.")
    exe = python_exe()
    if not exe:
        return ("no python interpreter found. The chatbox runs as a frozen "
                "build here, so OSCLeash needs a python 3 on PATH.")
    if needs_oscquery and not has_module("zeroconf", exe):
        return (f"OSCQuery needs the 'zeroconf' module, and {exe} does not "
                f"have it. Switch OSCQuery off for this leash, or install "
                f"zeroconf for that interpreter.")
    # only meaningful without OSCQuery: with it, the port in the config
    # is ignored and OSCLeash asks the system for a free one
    if not needs_oscquery and port and not port_free(port, ip):
        return (f"Port {port} is already in use. Something else is "
                f"listening there - another OSC tool, or an OSCLeash "
                f"left over from a previous run. Switch OSCQuery on for "
                f"this leash, or give it a free port.")
    return ""


def describe():
    """One line about the runtime, for the panel."""
    exe = python_exe()
    if not bundle_ok():
        return "bundled OSCLeash missing"
    if not exe:
        return "bundled OSCLeash \u00b7 no python found"
    return f"bundled OSCLeash \u00b7 {exe}"
