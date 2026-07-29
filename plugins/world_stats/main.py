"""World Stats – live VRChat instance info for OSC-DreamChatbox.

Moves the three "Live info" values out of the Personal Status card into a
plugin: {player_in_world}, {group_world}, {instance_type} and {realtime}.
They are declared as global_placeholders in plugin.json, so the plain
names keep working everywhere – in status texts, in the Apps custom
strings and in All-in-one.

The player/world numbers come from VRChat's output_log. The watcher that
tails it ships WITH this plugin (vrchatlog.py next to this file), so the
chatbox itself carries no log-reading code and no background thread for
it – uninstall the plugin and all of that is gone.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import time

_api = None
_watcher = None
_warned = False


# ---------------------------------------------------------------- setup
def setup(api):
    global _api, _watcher
    _api = api
    try:
        from .vrchatlog import VRChatLogWatcher
    except Exception as e:
        api.log(f"vrchatlog.py not importable ({e}) – "
                f"only the clock will work.")
        return
    _watcher = VRChatLogWatcher(api.log)
    _apply_log_dir()
    _watcher.start()
    api.log("watching the VRChat log for players/world")


def teardown():
    global _watcher
    if _watcher is not None:
        try:
            _watcher.stop()
        except Exception:
            pass
        _watcher = None


def on_settings(settings):
    """The user changed something in the Settings block."""
    if _watcher is None:
        return
    _apply_log_dir()
    # a changed folder only takes effect after a restart of the watcher
    if _needs_log():
        _watcher.start()
    else:
        _watcher.stop()


def _apply_log_dir():
    folder = str(_get("log_dir", "")).strip()
    try:
        _watcher.set_override(folder)
    except Exception as e:
        _api.log(f"could not set the log folder: {e}")


def _get(key, default=None):
    return _api.get(key, default) if _api is not None else default


def _needs_log():
    return bool(_get("players", True) or _get("world", True))


# --------------------------------------------------------------- values
def _snapshot():
    """Reads the watcher, never raising – if the log isn't there yet we
    simply report 'not in a world' instead of breaking the chatbox."""
    global _warned
    if _watcher is None or not _needs_log():
        return None
    try:
        return _watcher.snapshot()
    except Exception as e:
        if not _warned:          # log once, not every send tick
            _warned = True
            _api.log(f"log watcher unavailable: {e}")
        return None


def get_values():
    """Fills {player_in_world} {group_world} {instance_type} {realtime}.

    Anything switched off or unknown stays None, which apply_template
    drops together with its surrounding separators – so a template like
    "{player_in_world} | {group_world}" never leaves a stray '|' behind.
    """
    vals = {"player_in_world": None, "group_world": None,
            "instance_type": None, "realtime": None}

    snap = _snapshot()
    if snap and snap.get("in_world"):
        if _get("players", True) and snap.get("player_count", 0) > 0:
            icon = str(_get("player_icon", "")).strip()
            count = str(snap["player_count"])
            vals["player_in_world"] = f"{icon} {count}".strip()
        if _get("world", True):
            world = (snap.get("world") or "").strip()
            if world:
                limit = int(_get("world_max", 24) or 24)
                if len(world) > limit:
                    # hard cut, no ellipsis – chatbox characters are scarce
                    world = world[:limit].rstrip()
                vals["group_world"] = world
            vals["instance_type"] = (snap.get("instance_type") or "").strip() \
                or None

    if _get("clock", True):
        fmt = str(_get("clock_format", "%H:%M")).strip() or "%H:%M"
        try:
            vals["realtime"] = time.strftime(fmt)
        except Exception:
            vals["realtime"] = time.strftime("%H:%M")
    return vals


def get_text():
    """The combined line -> {world_stats}."""
    vals = get_values()
    parts = [vals["player_in_world"], vals["group_world"], vals["realtime"]]
    return " | ".join(p for p in parts if p)


def get_lines():
    """Used when the custom string is switched off."""
    text = get_text()
    return [text] if text else []
