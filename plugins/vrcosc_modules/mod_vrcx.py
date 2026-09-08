"""VRCX Bridge - world, friends and the newest friend event.

Port of `VRCXBridge` from Bluscream's VRCOSC-Modules. His version talks
to VRCX over a Windows named pipe (`\\\\.\\pipe\\vrcx-ipc`), which does not
exist on Linux, so this module takes the other road: VRCX keeps
everything it knows in a SQLite file, and SQLite is happy to be read
while VRCX writes.

The database is opened read-only. VRCX prefixes its tables with the user
id (`usr_1234…_gamelog_location`), so we look tables up by suffix instead
of by name and simply report nothing when a table is missing - the schema
belongs to VRCX and may change without asking us.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import sqlite3
from pathlib import Path

from . import util

ID = "vx"
NAME = "VRCX Bridge"

KEYS = ("vx_world", "vx_friends", "vx_last_friend", "vx_last_event",
        "vx_status")

# Where VRCX puts its database - native build first, then the usual
# Wine/Proton prefixes.
_CANDIDATES = (
    Path.home() / ".config/VRCX/VRCX.sqlite3",
    Path.home() / ".local/share/VRCX/VRCX.sqlite3",
)
_ROAMING = "AppData/Roaming/VRCX/VRCX.sqlite3"

_poller = None
_ctx = None


def start(ctx):
    global _poller, _ctx
    stop()
    _ctx = ctx
    _poller = util.Poller(_tick, ctx.log, ctx.num("vx_interval", 15, 5, 600),
                          "vrcx")
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
        _poller.set_interval(ctx.num("vx_interval", 15, 5, 600))
        _poller.poke()


# ----------------------------------------------------------- database
def _find_db(override=""):
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    for path in _CANDIDATES:
        if path.is_file():
            return path
    # Wine and Proton prefixes: users/<name>/AppData/Roaming/VRCX/...
    roots = [Path.home() / ".wine/drive_c/users"]
    steam = Path.home() / ".local/share/Steam/steamapps/compatdata"
    if steam.is_dir():
        for app in steam.iterdir():
            roots.append(app / "pfx/drive_c/users")
    for root in roots:
        if not root.is_dir():
            continue
        for user in root.iterdir():
            candidate = user / _ROAMING
            if candidate.is_file():
                return candidate
    return None


def _tables(cur, suffix):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [row[0] for row in cur.fetchall() if row[0].endswith(suffix)]


def _rows(cur, table, columns, limit=1):
    try:
        cur.execute(f'SELECT {columns} FROM "{table}" '
                    f'ORDER BY rowid DESC LIMIT {int(limit)}')
        return cur.fetchall()
    except sqlite3.Error:
        return []


def _tick():
    ctx = _ctx
    if ctx is None:
        return None
    path = _find_db(ctx.text("vx_db"))
    if path is None:
        return {"found": False, "world": "", "friends": None,
                "last": None, "event": ""}

    out = {"found": True, "world": "", "friends": None,
           "last": None, "event": ""}
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=3.0) as conn:
        cur = conn.cursor()

        for table in _tables(cur, "_gamelog_location"):
            rows = _rows(cur, table, "world_name")
            if rows and rows[0][0]:
                out["world"] = str(rows[0][0])
                break

        for table in _tables(cur, "_feed_online_offline"):
            rows = _rows(cur, table, "display_name, type", limit=2000)
            if not rows:
                continue
            # rows come newest first, so the first time a name shows up is
            # its current state - that is the online list without VRCX
            # having to hand us one
            seen, online = set(), 0
            for name, kind in rows:
                if name in seen:
                    continue
                seen.add(name)
                if str(kind).lower().startswith("online"):
                    online += 1
            out["friends"] = online
            out["last"] = str(rows[0][0] or "")
            out["event"] = str(rows[0][1] or "")
            break
    return out


def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _poller is None:
        return vals
    snap = _poller.snapshot(max_age=max(60.0,
                                        ctx.num("vx_interval", 15, 5, 600) * 4))
    if not snap or not snap["found"]:
        return vals

    vals["vx_status"] = ctx.text("vx_icon", "🅥") or None
    if snap["world"] and ctx.flag("vx_show_world", True):
        vals["vx_world"] = util.cut(snap["world"],
                                    ctx.num("vx_world_max", 24, 6, 64)) or None
    if snap["friends"] is not None and ctx.flag("vx_show_friends", True):
        vals["vx_friends"] = util.join(ctx.text("vx_friend_icon", "👥"),
                                       str(snap["friends"]))
    if snap["last"] and ctx.flag("vx_show_last"):
        vals["vx_last_friend"] = util.cut(snap["last"], 24) or None
        vals["vx_last_event"] = snap["event"] or None
    return vals


def line(vals):
    return [vals["vx_friends"], vals["vx_world"]]
