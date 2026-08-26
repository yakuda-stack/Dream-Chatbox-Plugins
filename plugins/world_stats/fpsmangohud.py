"""
fpsmangohud.py - the fallback FPS source on Linux.

Kept because a lot of people already have MangoHud logging set up and
working, and taking that away to replace it with something they have to
build would be a downgrade for exactly the users who were most invested.
It is the fallback rather than the default because of what it costs to
set up: install MangoHud, switch CSV logging on, find where it writes,
and tell this plugin. Four steps, three of them elsewhere.

The layer in fpslayer.py does the same job in one - but this stays.

WHAT A MANGOHUD LOG LOOKS LIKE
------------------------------
A header row naming the columns, then one row per interval. The fps
column is not always in the same place and the header is not always
present, so the column is looked up by name when it can be and guessed
at position 1 when it cannot - which is where every MangoHud version so
far has put it.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")

#: a log nobody has written to for this long is from a session that
#: ended - reporting it would mean printing last night's frame rate
STALE_SEC = 15.0

#: only the tail is read, so a two-hour benchmark log stays cheap to poll
TAIL_BYTES = 4096

#: the usual places, in the order they are worth trying
COMMON_DIRS = ("~/mangologs", "~/mangohud", "~/.local/share/mangohud",
               "~/Documents/mangohud", "/tmp/mangohud")


def _home():
    return Path(os.path.expanduser("~"))


def _config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config"))


def _config_files():
    """Every MangoHud config that could name an output folder.

    Includes the per-game ones and whatever GOverlay wrote, because that
    is how most people switch logging on without ever seeing a path.
    """
    base = _config_home() / "MangoHud"
    out = []
    for candidate in (base / "MangoHud.conf", _home() / ".mangohud.conf"):
        if candidate.is_file():
            out.append(candidate)
    try:
        if base.is_dir():
            out += sorted(p for p in base.iterdir()
                          if p.is_file() and p.suffix.lower() == ".conf")
    except OSError:
        pass
    # de-duplicate while keeping order
    seen, unique = set(), []
    for path in out:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


_OUTPUT_RE = re.compile(r"^\s*output_folder\s*=\s*(.+?)\s*$", re.MULTILINE)


def _folders_from_configs():
    found = []
    for path in _config_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _OUTPUT_RE.finditer(text):
            folder = Path(os.path.expanduser(match.group(1).strip()))
            if folder.is_dir():
                found.append(folder)
    return found


def _folders_from_env():
    raw = os.environ.get("MANGOHUD_CONFIG", "")
    out = []
    for part in raw.split(","):
        if part.strip().startswith("output_folder"):
            _, _, value = part.partition("=")
            folder = Path(os.path.expanduser(value.strip()))
            if folder.is_dir():
                out.append(folder)
    return out


def detect():
    """Best guess at the log folder, or None.

    A folder holding fresh logs beats one holding stale ones, and one
    holding VRChat logs beats somebody else's benchmark run.
    """
    if IS_WINDOWS:
        return None
    candidates = _folders_from_env() + _folders_from_configs()
    candidates += [Path(os.path.expanduser(p)) for p in COMMON_DIRS]

    best, best_score = None, None
    seen = set()
    for folder in candidates:
        if folder in seen or not folder.is_dir():
            continue
        seen.add(folder)
        score = _score(folder)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best, best_score = folder, score
    return best


def _score(folder):
    """(has a vrchat log, newest mtime) or None when there are no logs."""
    try:
        logs = [p for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() == ".csv"]
    except OSError:
        return None
    if not logs:
        return None
    newest = max(p.stat().st_mtime for p in logs)
    vrchat = any("vrchat" in p.name.lower() for p in logs)
    return (1 if vrchat else 0, newest)


def read(folder):
    """(fps, frametime_ms, log name) from the newest log, or None."""
    if IS_WINDOWS or not folder:
        return None
    folder = Path(os.path.expanduser(str(folder)))
    if not folder.is_dir():
        return None
    try:
        logs = [p for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() == ".csv"]
        if not logs:
            return None
        newest = max(logs, key=lambda p: p.stat().st_mtime)
        if time.time() - newest.stat().st_mtime > STALE_SEC:
            return None
    except OSError:
        return None

    value = _last_fps(newest)
    if value is None or not (0.0 < value < 10000.0):
        return None
    return value, (1000.0 / value if value else 0.0), newest.stem


def _last_fps(path):
    """The fps column of the last data row."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None

    lines = [ln for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return None

    # find the fps column by name when a header made it into the tail;
    # every MangoHud version so far puts it second, so that is the guess
    column = 1
    for line in lines:
        low = line.lower()
        if "fps" in low and "," in low:
            header = [c.strip().lower() for c in line.split(",")]
            if "fps" in header:
                column = header.index("fps")
            break

    for line in reversed(lines):
        cells = [c.strip() for c in line.split(",")]
        if len(cells) <= column:
            continue
        try:
            return float(cells[column])
        except ValueError:
            continue        # a header row, or a partially written line
    return None


def status_line(folder):
    """One line for the settings block."""
    if IS_WINDOWS:
        return "MangoHud is a Linux thing."
    if not folder:
        guess = detect()
        if guess:
            return f"No folder set. Found logs in {guess} - press Detect."
        return ("No folder set, and no MangoHud logs found. Switch CSV "
                "logging on in MangoHud or GOverlay first.")
    folder = Path(os.path.expanduser(str(folder)))
    if not folder.is_dir():
        return f"{folder} does not exist."
    hit = read(folder)
    if hit:
        return f"Active - {hit[2]} ({hit[0]:.0f} fps)."
    try:
        logs = [p for p in folder.iterdir() if p.suffix.lower() == ".csv"]
    except OSError as e:
        return f"{folder} is not readable ({e})."
    if not logs:
        return f"{folder} holds no .csv logs yet."
    return "Logs are there, but none of them is fresh - is the game running?"
