"""Linux Process Manager - is that program running?

Port of the watch half of `LinuxProcessManager` from Bluscream's
VRCOSC-Modules. The upstream module can also start and kill processes;
that half is deliberately left out - a chatbox plugin that can kill
programs is a footgun, and nothing in a chatbox line needs it.

No subprocess and no polling tools: we walk /proc ourselves, which costs
about a millisecond for a few hundred processes.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

from . import util

ID = "pm"
NAME = "Linux Process Manager"

KEYS = ("pm_list", "pm_missing", "pm_count", "pm_state", "pm_total")

_poller = None
_watch = ()


def start(ctx):
    global _poller
    stop()
    on_settings(ctx)
    _poller = util.Poller(_tick, ctx.log, ctx.num("pm_interval", 5, 1, 120),
                          "process")
    _poller.start()


def stop():
    global _poller, _watch
    if _poller is not None:
        _poller.stop()
    _poller = None
    _watch = ()


def on_settings(ctx):
    global _watch
    # "VRChat.exe, wivrn-server , steam" -> three entries, blanks dropped
    raw = ctx.text("pm_watch")
    _watch = tuple(part.strip() for part in raw.replace(";", ",").split(",")
                   if part.strip())
    if _poller is not None:
        _poller.set_interval(ctx.num("pm_interval", 5, 1, 120))
        _poller.poke()


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tick():
    watch = _watch
    running, total = set(), 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        total += 1
        if not watch or len(running) == len(watch):
            continue
        comm = _read(entry / "comm").strip()
        cmdline = None
        for needle in watch:
            if needle in running:
                continue
            if needle.lower() == comm.lower():
                running.add(needle)
                continue
            # a Proton game is "wine64-preloader", never "VRChat.exe" -
            # so fall back to the full command line
            if cmdline is None:
                cmdline = _read(entry / "cmdline").replace("\0", " ")
            if needle.lower() in cmdline.lower():
                running.add(needle)
    return {"running": running, "total": total}


def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _poller is None:
        return vals
    snap = _poller.snapshot(max_age=max(30.0,
                                        ctx.num("pm_interval", 5, 1, 120) * 4))
    if not snap:
        return vals

    running = [name for name in _watch if name in snap["running"]]
    missing = [name for name in _watch if name not in snap["running"]]
    sep = ctx.get("pm_sep", " ") or " "
    on, off = ctx.text("pm_icon_on"), ctx.text("pm_icon_off")

    def fmt(names, icon):
        return sep.join(util.join(icon, name) for name in names) or None

    vals["pm_list"] = fmt(running, on)
    vals["pm_missing"] = fmt(missing, off)
    vals["pm_count"] = str(len(running)) if _watch else None
    vals["pm_state"] = f"{len(running)}/{len(_watch)}" if _watch else None
    vals["pm_total"] = str(snap["total"])
    return vals


def line(vals):
    return [vals["pm_list"]]
