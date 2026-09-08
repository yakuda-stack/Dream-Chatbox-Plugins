"""VRChat Settings - values out of VRChat's own config.json.

Port of the read half of `VRChatSettings` from Bluscream's
VRCOSC-Modules. Upstream also writes settings and pokes the Windows
registry; writing into the config of a running game from a chatbox is a
good way to lose a session, so this module only reads.

VRChat keeps `config.json` next to its log files - inside the Proton
prefix on Linux:

    ~/.local/share/Steam/steamapps/compatdata/438100/pfx/drive_c/users/
        steamuser/AppData/LocalLow/VRChat/VRChat/config.json

Two slots, each with a dotted key and a label, so you can put things like
the camera resolution or the cache size into a line.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json
from pathlib import Path

from . import util

ID = "vc"
NAME = "VRChat Settings"

KEYS = ("vc_1", "vc_2", "vc_all", "vc_status")

VRCHAT_APPID = "438100"
_TAIL = "pfx/drive_c/users/steamuser/AppData/LocalLow/VRChat/VRChat"
_STEAM_ROOTS = (
    Path.home() / ".local/share/Steam",
    Path.home() / ".steam/steam",
    Path.home() / ".steam/root",
    Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",
)

_poller = None
_ctx = None


def start(ctx):
    global _poller, _ctx
    stop()
    _ctx = ctx
    _poller = util.Poller(_tick, ctx.log, ctx.num("vc_interval", 60, 10, 900),
                          "vrchat")
    _poller.start()


def stop():
    global _poller, _ctx
    if _poller is not None:
        _poller.stop()
    _poller = None
    _ctx = None


def on_settings(ctx):
    global _ctx
    _ctx = ctx
    if _poller is not None:
        _poller.set_interval(ctx.num("vc_interval", 60, 10, 900))
        _poller.poke()


def _find_config(override=""):
    if override:
        path = Path(override).expanduser()
        if path.is_dir():
            path = path / "config.json"
        return path if path.is_file() else None
    for root in _STEAM_ROOTS:
        candidate = root / "steamapps/compatdata" / VRCHAT_APPID / _TAIL \
            / "config.json"
        if candidate.is_file():
            return candidate
    # Windows layout, in case somebody runs the chatbox there anyway
    native = Path.home() / "AppData/LocalLow/VRChat/VRChat/config.json"
    return native if native.is_file() else None


def _tick():
    ctx = _ctx
    if ctx is None:
        return None
    path = _find_config(ctx.text("vc_path"))
    if path is None:
        return {"found": False, "data": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {"found": False, "data": {}}
    return {"found": True, "data": data if isinstance(data, dict) else {}}


def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _poller is None:
        return vals
    snap = _poller.snapshot()
    if not snap or not snap["found"]:
        return vals

    vals["vc_status"] = ctx.text("vc_icon", "⚙") or None
    parts = []
    for slot in (1, 2):
        key = ctx.text(f"vc_k{slot}")
        if not key:
            continue
        value = util.dig(snap["data"], key)
        if value is None:
            continue
        if isinstance(value, bool):
            value = "on" if value else "off"
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        text = util.join(ctx.text(f"vc_l{slot}"),
                         util.cut(str(value), ctx.num("vc_max", 24, 4, 120)))
        vals[f"vc_{slot}"] = text
        if text:
            parts.append(text)
    vals["vc_all"] = " | ".join(parts) or None
    return vals


def line(vals):
    return [vals["vc_all"]]
