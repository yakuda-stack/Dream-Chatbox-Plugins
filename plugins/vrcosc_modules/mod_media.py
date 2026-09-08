"""Linux Media - the track from any MPRIS player.

Port of `LinuxMedia` from Bluscream's VRCOSC-Modules. His
`vrcosc_mpris_query.sh` is bundled unchanged and asks D-Bus for the first
MPRIS player it finds, writing seven lines: status, title, artist,
length, position, bus name, volume.

Two lines get patched on deploy: the output path (so a real VRCOSC keeps
its own `~/.vrcosc_mpris.txt`) and the debug log the script appends to on
every single call - at a three second interval that file would grow
forever.

Note that OSC-DreamChatbox has its own media card. This module is for
people who want the MPRIS values as free placeholders next to it, or who
run a player the built-in card does not pick up.
"""

# Copyright (C) 2026 yakuda
# Bundled script: Copyright (c) Bluscream
# SPDX-License-Identifier: GPL-3.0-or-later

from . import util

ID = "md"
NAME = "Linux Media"
SCRIPT = "vrcosc_mpris_query.sh"

KEYS = ("md_media", "md_title", "md_artist", "md_player", "md_status",
        "md_position", "md_duration", "md_progress", "md_volume")

_poller = None
_script = None
_ready = False
_out = util.cache_dir("media") / "mpris.txt"

_ICONS = {"Playing": "▶", "Paused": "⏸", "Stopped": "⏹"}


def start(ctx):
    global _poller, _script, _ready
    stop()
    if util.which("dbus-send") is None:
        ctx.log("media: dbus-send not found - install dbus, staying idle")
        return
    _script = util.deploy(SCRIPT, "media", override=ctx.text("md_script"),
                          patches=[
        (r"~/\.vrcosc_mpris_debug\.log", "/dev/null"),
        (r"~/\.vrcosc_mpris\.txt", str(_out)),
        (r"~/\.vrcosc_mpris\.txt", str(_out)),   # the "Stopped" branch
    ])
    _ready = True
    _poller = util.Poller(_tick, ctx.log, ctx.num("md_interval", 3, 1, 60),
                          "media")
    _poller.start()


def stop():
    global _poller, _script, _ready
    if _poller is not None:
        _poller.stop()
    _poller = None
    _script = None
    _ready = False


def on_settings(ctx):
    if _poller is not None:
        _poller.set_interval(ctx.num("md_interval", 3, 1, 60))


def _tick():
    if not _ready:
        return None
    util.run_script(_script, timeout=15)
    lines = util.read_lines(_out, minimum=1)
    if lines is None:
        return None
    lines += [""] * (7 - len(lines))
    status = lines[0].strip() or "Stopped"
    return {
        "status": status,
        "title": lines[1].strip(),
        "artist": lines[2].strip(),
        # the script reports microseconds, straight from MPRIS
        "length": util.to_int(lines[3]) // 1_000_000,
        "position": util.to_int(lines[4]) // 1_000_000,
        "player": lines[5].strip().rsplit(".", 1)[-1],
        "volume": util.to_float(lines[6]),
    }


def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _poller is None:
        return vals
    snap = _poller.snapshot(max_age=max(20.0,
                                        ctx.num("md_interval", 3, 1, 60) * 4))
    if not snap:
        return vals

    status = snap["status"]
    if status == "Stopped" and not snap["title"]:
        return vals                       # nothing playing, say nothing

    icon = _ICONS.get(status, "") if ctx.flag("md_icons", True) else ""
    vals["md_status"] = util.join(icon, status if not icon else "") or icon
    vals["md_title"] = util.cut(snap["title"],
                                ctx.num("md_title_max", 32, 6, 120)) or None
    vals["md_artist"] = util.cut(snap["artist"],
                                 ctx.num("md_artist_max", 24, 4, 80)) or None
    vals["md_player"] = snap["player"] or None
    if snap["length"] > 0:
        vals["md_duration"] = util.mmss(snap["length"])
        vals["md_position"] = util.mmss(snap["position"])
        if ctx.flag("md_bar"):
            vals["md_progress"] = util.bar(snap["position"] / snap["length"],
                                           ctx.num("md_bar_width", 10, 4, 20))
    if snap["volume"]:
        vals["md_volume"] = f"{round(snap['volume'] * 100)}%"

    vals["md_media"] = util.join(icon, vals["md_title"],
                                 f"– {vals['md_artist']}"
                                 if vals["md_artist"] else None)
    return vals


def line(vals):
    return [vals["md_media"]]
