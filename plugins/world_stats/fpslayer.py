"""
fpslayer.py - the built-in FPS source on Linux.

Frames per second only exist inside the process drawing them. Nothing in
/proc or /sys knows how fast a game is rendering, so something has to be
loaded into the game. On Linux that something is a Vulkan layer, and this
plugin ships one: layer/dreamfps_layer.c, about 500 lines of C whose
entire job is to count calls to vkQueuePresentKHR and publish the rate in
a shared memory page.

This module is the other end of that page, plus the switch that arms it.

THE SWITCH IS A FILE
--------------------
A Vulkan layer is enabled by the presence of a manifest in
``~/.local/share/vulkan/implicit_layer.d``. There is no API and no daemon
that helps here - Steam starts the game, so nothing this process exports
is inherited by it. Writing the manifest when the FPS checkbox goes on
and deleting it when it goes off is the whole mechanism, which also makes
it honest: nothing stays running, and ``ls`` in that folder tells the
truth.

The consequence has to be said out loud in the UI: a game that was
already running when the box was ticked will not be measured. The loader
reads that folder once, at instance creation.

WHY THE PLUGIN BUILDS IT INSTEAD OF SHIPPING A BINARY
-----------------------------------------------------
A plugin is a ZIP of Python files that people install from a store. A
prebuilt .so in there would be a binary from the internet loading into
every Vulkan application on the machine - which is a lot to ask of
somebody who wanted a chatbox line. Building from the source next to it
takes a second, needs gcc and the Vulkan headers, and leaves the user
with something they could have read first.

If the app itself or a distribution package already installed the layer,
that copy is used and nothing is built.

WINDOWS
-------
Not applicable, and not for lack of trying: VRChat on Windows renders
D3D11 natively rather than through DXVK, so a Vulkan layer would never
see it. Windows reads RTSS instead - see fpsrtss.py.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")

# ------------------------------------------------------------ the page
#: must stay in lockstep with struct dreamfps_shm in
#: layer/dreamfps_shm.h - the _Static_assert there and the check below
#: are the two halves of the same agreement. Edit one without the other
#: and every reading turns to garbage silently.
SHM_FORMAT = "<IIIIffdQ64s"
SHM_SIZE = struct.calcsize(SHM_FORMAT)

SHM_MAGIC = 0x53504644          # "DFPS"
SHM_VERSION = 1

SHM_DIR = Path("/dev/shm")
SHM_PREFIX = "dreamfps-"

#: a sample older than this is from a game that stopped drawing - either
#: minimised, or gone without getting to clean up. Two publish windows
#: plus slack; the layer writes every 0.5 s.
FRESH_SEC = 3.0

# ------------------------------------------------------- the manifest
LAYER_NAME = "VK_LAYER_YAKUDA_dreamfps"
MANIFEST_NAME = "VkLayer_dreamfps.json"
LIB_NAME = "libVkLayer_dreamfps.so"
TEMPLATE_NAME = "VkLayer_dreamfps.json.in"

#: Processes that draw with Vulkan but are not the game. Without this the
#: compositor - which presents every frame of the desktop - wins on an
#: idle machine and the chatbox proudly reports the panel's refresh rate.
#:
#: gamescope is the deliberate exception: people who run VRChat inside it
#: want gamescope's number, because that IS the game's number.
IGNORE_COMM = {
    "kwin_wayland", "kwin_x11", "kwin", "plasmashell", "gnome-shell",
    "Xwayland", "sway", "Hyprland", "weston", "mutter", "wayfire",
    "labwc", "river", "niri", "cosmic-comp",
    # the chatbox itself: Qt can end up on the Vulkan RHI, and measuring
    # our own window instead of the game is a confusing bug report
    "osc-dreamchatbo", "osc-dreamchatbox", "python3", "python",
}

#: only used to break a tie between two live games, never to reject
PREFER_COMM = ("vrchat", "wine", "proton", "gamescope", "steam")


# ==================================================================== #
#  reading
# ==================================================================== #
def _pid_alive(pid):
    """Cheaper and more honest than a timestamp: a game killed with -9
    never ran the layer's cleanup, so its page sits in /dev/shm looking
    exactly like a live one until it goes stale."""
    try:
        return Path(f"/proc/{int(pid)}").is_dir()
    except (ValueError, OSError):
        return False


def _read_one(path):
    """One page -> dict, or None if it is not ours or not consistent.

    The seqlock: the layer bumps ``seq`` to an odd number before writing
    and to the next even one after. A reader that sees odd caught a write
    in progress and tries again. Three attempts is plenty against a
    writer that holds it for nanoseconds twice a second.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < SHM_SIZE:
        return None

    for _ in range(3):
        (magic, version, seq, pid, fps, frametime,
         updated, frames, name) = struct.unpack(SHM_FORMAT, raw[:SHM_SIZE])
        if magic != SHM_MAGIC or version != SHM_VERSION:
            return None
        if seq % 2 == 0:
            break
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        if len(raw) < SHM_SIZE:
            return None
    else:
        return None

    comm = name.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
    return {
        "pid": int(pid),
        "fps": float(fps),
        "frametime_ms": float(frametime),
        "frames": int(frames),
        "updated": float(updated),
        "age": max(0.0, time.time() - float(updated)),
        "name": comm,
        "path": path,
    }


def samples(include_stale=False, want=""):
    """Every live reading, best candidate first.

    Ordering in words: a process whose name the user asked for wins; then
    one that looks like a game; then the one that has drawn the most
    frames, because a title running for ten minutes is a better answer
    than something that started rendering four seconds ago.
    """
    if IS_WINDOWS:
        return []
    own = os.getpid()
    want = (want or "").strip().lower()
    out = []
    try:
        pages = sorted(p for p in SHM_DIR.iterdir()
                       if p.is_file() and p.name.startswith(SHM_PREFIX))
    except OSError:
        return []

    for path in pages:
        info = _read_one(path)
        if info is None or info["pid"] == own:
            continue
        if not _pid_alive(info["pid"]):
            # same uid wrote it, so this succeeds; failing is not worth
            # complaining about
            try:
                path.unlink()
            except OSError:
                pass
            continue
        info["stale"] = info["age"] > FRESH_SEC
        if info["stale"] and not include_stale:
            continue
        if info["name"] in IGNORE_COMM:
            continue
        out.append(info)

    def rank(item):
        low = item["name"].lower()
        return (0 if item.get("stale") else 1,
                1 if (want and want in low) else 0,
                1 if any(h in low for h in PREFER_COMM) else 0,
                item["frames"])

    out.sort(key=rank, reverse=True)
    return out


def read(want=""):
    """(fps, frametime_ms, process name) or None."""
    found = samples(want=want)
    if not found:
        return None
    best = found[0]
    if not (0.0 < best["fps"] < 10000.0):
        return None
    return best["fps"], best["frametime_ms"], best["name"]


# ==================================================================== #
#  where things live
# ==================================================================== #
def _data_home():
    return Path(os.environ.get("XDG_DATA_HOME")
                or (Path.home() / ".local/share"))


def plugin_dir():
    return Path(__file__).resolve().parent


def source_dir():
    """The C source shipped with the plugin."""
    return plugin_dir() / "layer"


def layer_dir():
    """The user's own implicit layer folder. Writing here needs no root
    and affects nobody else's session."""
    return _data_home() / "vulkan/implicit_layer.d"


def manifest_path():
    return layer_dir() / MANIFEST_NAME


def find_library():
    """The .so to point the manifest at, or None.

    The plugin's own build comes first, then anything the app or a
    distribution package installed - so somebody who has the layer from
    the AUR package does not have to build it again.
    """
    for path in (source_dir() / LIB_NAME,
                 _data_home() / "osc-dreamchatbox/vulkan-layer" / LIB_NAME,
                 Path("/usr/lib/osc-dreamchatbox") / LIB_NAME,
                 Path("/usr/lib64/osc-dreamchatbox") / LIB_NAME,
                 Path("/usr/local/lib/osc-dreamchatbox") / LIB_NAME):
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _is_ephemeral(path):
    """True for a path that will not exist after this process exits.

    An AppImage mounts itself under /tmp/.mount_XXXX and unmounts on
    close. A manifest pointing in there breaks every Vulkan application
    on the machine the next time one starts, because the loader finds the
    JSON, fails to load the library and logs an error - so this is not a
    nicety.
    """
    appdir = os.environ.get("APPDIR")
    text = str(path)
    return bool(appdir and text.startswith(appdir)) \
        or text.startswith("/tmp/.mount_")


# ==================================================================== #
#  building
# ==================================================================== #
def build_tools():
    """(ok, what is missing) - checked before offering to build."""
    if IS_WINDOWS:
        return False, "not applicable on Windows"
    missing = []
    if not (shutil.which("gcc") or shutil.which("cc")):
        missing.append("a C compiler")
    if not shutil.which("make"):
        missing.append("make")
    header = False
    for base in ("/usr/include", "/usr/local/include"):
        if Path(base, "vulkan/vulkan.h").is_file():
            header = True
            break
    if not header:
        missing.append("the Vulkan headers")
    if missing:
        return False, ", ".join(missing) + " missing"
    return True, ""


def build():
    """Compile the layer. Returns (ok, message).

    Blocking - roughly a second - so callers run it off the GUI thread.
    """
    if IS_WINDOWS:
        return False, "The Vulkan layer is a Linux thing; Windows uses RTSS."
    ok, why = build_tools()
    if not ok:
        return False, (
            f"Cannot build the FPS layer: {why}. "
            "Arch: pacman -S base-devel vulkan-headers  ·  "
            "Fedora: dnf install gcc make vulkan-headers  ·  "
            "Debian/Ubuntu: apt install build-essential libvulkan-dev")
    src = source_dir()
    if not (src / "dreamfps_layer.c").is_file():
        return False, f"The layer source is missing from {src}"
    try:
        proc = subprocess.run(
            ["make", "-C", str(src)],
            capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return False, "make is not installed"
    except subprocess.TimeoutExpired:
        return False, "the build took too long and was stopped"
    except Exception as e:                      # noqa: BLE001
        return False, f"the build failed to start: {e}"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else f"exit code {proc.returncode}"
        return False, f"Build failed: {detail}"
    if not (src / LIB_NAME).is_file():
        return False, "The build reported success but produced no library."
    return True, f"FPS layer built: {src / LIB_NAME}"


# ==================================================================== #
#  arming and disarming
# ==================================================================== #
def available():
    """Can this machine use the layer? (ok, reason)."""
    if IS_WINDOWS:
        return False, ("The built-in layer is a Vulkan layer. VRChat on "
                       "Windows renders D3D11 directly, so a Vulkan layer "
                       "would never see it - Windows reads RTSS instead.")
    if find_library() is None:
        return False, "The layer has not been built yet."
    return True, ""


def installed():
    """Is the manifest in place AND pointing at a library that exists?

    Both halves matter. A manifest left behind by an older install whose
    .so was removed is worse than no manifest: the loader logs an error
    in every Vulkan application on the machine.
    """
    try:
        path = manifest_path()
        if not path.is_file():
            return False
        import json
        lib = json.loads(path.read_text(encoding="utf-8")) \
            .get("layer", {}).get("library_path", "")
        return bool(lib) and Path(lib).is_file()
    except (OSError, ValueError):
        return False


def _template_text():
    for base in (source_dir(), Path("/usr/share/osc-dreamchatbox"),
                 _data_home() / "osc-dreamchatbox/vulkan-layer"):
        try:
            path = base / TEMPLATE_NAME
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    # Not fatal - the template is a few lines of JSON around one path,
    # and a plugin whose .json.in went missing should still work.
    return (
        '{\n'
        '    "file_format_version": "1.2.0",\n'
        '    "layer": {\n'
        f'        "name": "{LAYER_NAME}",\n'
        '        "type": "GLOBAL",\n'
        '        "library_path": "@LIBRARY_PATH@",\n'
        '        "api_version": "1.3.0",\n'
        '        "implementation_version": "1",\n'
        '        "description": "OSC-DreamChatbox frame counter",\n'
        '        "disable_environment": { "DREAMFPS_DISABLE": "1" }\n'
        '    }\n'
        '}\n'
    )


def install():
    """Arm the layer. (ok, message). Idempotent - installing over an
    existing manifest rewrites it, which is also how a library path that
    moved gets repaired."""
    ok, reason = available()
    if not ok:
        return False, reason
    lib = find_library()
    try:
        if _is_ephemeral(lib):
            target_dir = _data_home() / "osc-dreamchatbox/vulkan-layer"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / LIB_NAME
            if not (target.is_file()
                    and target.stat().st_size == lib.stat().st_size):
                shutil.copy2(lib, target)
                target.chmod(0o755)
            lib = target

        layer_dir().mkdir(parents=True, exist_ok=True)
        text = _template_text().replace("@LIBRARY_PATH@", str(lib))
        # written whole, then moved into place: the loader may be reading
        # that folder at any moment, and half a JSON file is an error
        # message in every Vulkan application on the machine
        tmp = manifest_path().with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(manifest_path())
    except OSError as e:
        return False, f"Could not install the layer: {e}"
    return True, ("FPS layer active. Games started from now on report "
                  "their frame rate; one that is already running has to "
                  "be restarted.")


def uninstall():
    """Disarm it. (ok, message)."""
    try:
        path = manifest_path()
        if path.is_file():
            path.unlink()
    except OSError as e:
        return False, f"Could not remove the layer: {e}"
    # the .so is left alone: it does nothing while no manifest names it,
    # and removing it would mean rebuilding on the next tick
    return True, "FPS layer removed."


def status_line():
    """One line for the settings block."""
    if IS_WINDOWS:
        return available()[1]
    if find_library() is None:
        ok, why = build_tools()
        if not ok:
            return f"Not built - {why}."
        return "Not built yet - press Build below."
    if not installed():
        return "Built, but not active."
    live = samples()
    if live:
        best = live[0]
        return (f"Active - reading {best['name'] or 'a game'} "
                f"({best['fps']:.0f} fps).")
    return ("Active - nothing is drawing yet. A game that was already "
            "running when this was switched on has to be restarted.")
