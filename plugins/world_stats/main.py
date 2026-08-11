"""World Stats – live VRChat instance info, a clock and battery levels.

Three things that all want to sit in the same chatbox line, and none of
which the chatbox itself should have to know about:

  world      {player_in_world} {group_world} {instance_type}
             parsed out of VRChat's output_log by vrchatlog.py, which
             ships next to this file – uninstall the plugin and the log
             reading and its thread are gone with it.

  session    {world_time} {vr_time}
             how long you have been in this instance and how long
             VRChat has been running. Both come out of the log's own
             timestamps, not from a stopwatch started here, so they
             survive restarting the chatbox mid-session.

  clock      {realtime} {realdate} {realday} {realtime_alt}
             deliberately independent of everything above. It has no
             connection to the log watcher, needs VRChat neither running
             nor installed, and keeps working when the world half is
             switched off entirely. Its own time zone, so the line can
             show a friend's local time next to yours.

  battery    {hmd_battery} {controller_battery} {tracker_battery}
             from battery.py, over adb for a standalone headset or over
             SteamVR/OpenVR for everything else. Also independent – it
             does not care where you are or whether you are in VR at all.

All of these are declared as global_placeholders in plugin.json, so the
plain names work everywhere: status texts, Apps custom strings, AIO.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime
import threading
import time

_api = None
_watcher = None
_battery = None
_warned = False
_tz_warned = set()
_app_start = 0.0     # only used by the "since the app started" source

# Read-only rows in the settings block are written from _sync_status(),
# which only ever runs on the GUI thread. A worker that wants to say
# something leaves it in _notes instead of calling api.set() itself -
# touching a widget from a worker thread is a segfault, not an
# exception. _written caches what is already there so the same string
# is not pushed back into the config once a frame.
_notes = {}          # key -> (text, valid_until)
_written = {}


# ---------------------------------------------------------------- setup
def setup(api):
    global _api, _watcher, _battery, _app_start
    _api = api
    _app_start = time.time()

    try:
        from .vrchatlog import VRChatLogWatcher
    except Exception as e:
        api.log(f"vrchatlog.py not importable ({e}) – "
                f"clock and battery still work.")
    else:
        _watcher = VRChatLogWatcher(api.log)
        _apply_log_dir()
        if _needs_log():
            _watcher.start()
            api.log("watching the VRChat log for players/world")

    try:
        from .battery import BatteryMonitor
    except Exception as e:
        api.log(f"battery.py not importable ({e}) – "
                f"battery placeholders stay empty.")
    else:
        _battery = BatteryMonitor(api.log)
        _apply_battery()

    try:
        from .battery import openvr_available
        _note("openvr_status", openvr_available()[1], 10 ** 6)
    except Exception:
        pass
    _sync_status()


def teardown():
    global _watcher, _battery
    for obj in (_watcher, _battery):
        try:
            if obj is not None:
                obj.stop()
        except Exception:
            pass
    _watcher = None
    _battery = None
    _notes.clear()
    _written.clear()


def on_event(name, data=None):
    if name == "app.shutdown":
        teardown()


# ------------------------------------------------------------- settings
def on_settings(settings):
    """The user changed something in the Settings block."""
    if _watcher is not None:
        _apply_log_dir()
        if _needs_log():
            _watcher.start()
        else:
            _watcher.stop()
    _apply_battery()
    _sync_status()


def _get(key, default=None):
    return _api.get(key, default) if _api is not None else default


def _set(key, value):
    """Write a settings row, but only when it really changed.

    on_settings() fires on every write, and on_settings() calls back
    into here - without this guard that is a loop."""
    if _written.get(key) == value:
        return
    _written[key] = value
    if _api is not None and _api.supports("api.set"):
        try:
            _api.set(key, value)
        except Exception:
            pass


def _note(key, text, secs=15.0):
    """Leave a message for the next _sync_status(). Thread safe: a dict
    assignment is all that happens here."""
    _notes[key] = (text, time.time() + secs)


def _apply_log_dir():
    folder = str(_get("log_dir", "")).strip()
    try:
        _watcher.set_override(folder)
    except Exception as e:
        _api.log(f"could not set the log folder: {e}")


def _needs_log():
    """Anything that has to read the log file.

    The world timer always needs it. The VR timer only needs it while
    it is set to the VRChat session - on "since the app started" it is
    a plain subtraction and the watcher can stay off."""
    if _get("players", True) or _get("world", True):
        return True
    if _get("world_time", False):
        return True
    return bool(_get("vr_time", False)
                and str(_get("vr_time_source", "vrchat")) == "vrchat")


def _apply_battery():
    """Push the current settings into the monitor and start or stop it.

    Stopping matters: a switched-off battery block must not keep an adb
    subprocess or an OpenVR session alive in the background."""
    if _battery is None:
        return
    if not _get("battery", False):
        _battery.stop()
        return
    _battery.set_config(
        source=str(_get("battery_source", "auto")),
        interval=int(_get("battery_interval", 60) or 60),
        adb_path=str(_get("adb_path", "")),
        adb_serial=str(_get("adb_serial", "")),
        controllers=bool(_get("battery_controllers", True)),
    )
    _battery.start()


def _sync_status():
    """Rewrite the read-only rows. GUI thread only – called from
    on_settings(), from setup() and once per chatbox frame."""
    for key in ("battery_status", "openvr_status"):
        note = _notes.get(key)
        if note and time.time() < note[1]:
            _set(key, note[0])
        elif key == "battery_status":
            _notes.pop(key, None)
            if _battery is None or not _get("battery", False):
                _set(key, "off")
            else:
                _set(key, _status_line())


def _status_line():
    snap = _battery.snapshot() if _battery is not None else {}
    if not snap:
        return "starting …"
    if not snap.get("ok"):
        if not snap.get("at"):
            return "looking for a headset …"
        return snap.get("error") or "no battery source"
    bits = [snap.get("source", ""), snap.get("device", "")]
    hmd = snap.get("hmd")
    if hmd:
        bits.append(f"{hmd['pct']}%" + (" charging" if hmd.get("charging")
                                        else ""))
    extra = len(snap.get("controllers") or []) + len(snap.get("trackers")
                                                     or [])
    if extra:
        bits.append(f"+{extra} device{'s' if extra != 1 else ''}")
    return " · ".join(b for b in bits if b)


# -------------------------------------------------------------- buttons
def on_action(key):
    if key == "battery_detect":
        if _battery is None:
            return "battery.py is not loaded"
        if not _get("battery", False):
            return "switch the battery block on first"
        _apply_battery()
        _battery.poll_now()
        # the poll itself happens in the worker thread; the Status row
        # picks the result up on the next frame
        _note("battery_status", "reading …", 4.0)
        _sync_status()
        return "reading – watch the Status row"

    if key == "adb_connect_now":
        target = str(_get("adb_connect", "")).strip()
        if not target:
            return "put an address like 192.168.1.42 above first"
        threading.Thread(target=_connect_later, args=(target,),
                         daemon=True).start()
        return "connecting …"

    if key == "battery_install_openvr":
        threading.Thread(target=_install_later, daemon=True).start()
        return "installing – this takes a moment"

    return ""


def _connect_later(target):
    """adb blocks for seconds, so it runs here – and reports back
    through _notes rather than touching the settings itself."""
    adb_path = str(_get("adb_path", ""))
    try:
        from .battery import adb_connect, find_adb
        answer = adb_connect(find_adb(adb_path), target)
    except Exception as e:
        answer = f"failed: {e}"
    if _api is not None:
        _api.log(f"adb connect: {answer}")
    _note("battery_status", answer, 20.0)
    if _battery is not None:
        _battery.poll_now()


def _install_later():
    try:
        from .battery import install_openvr
        answer = install_openvr(_api.log if _api is not None else print)
    except Exception as e:
        answer = f"install failed: {e}"
    _note("openvr_status", answer, 10 ** 6)


# ---------------------------------------------------------------- clock
def _now(tz_name):
    """Local time, or the time in a named zone.

    zoneinfo is stdlib, but on Windows it needs the tzdata package, so a
    bad or unavailable zone falls back to local time instead of leaving
    the placeholder empty."""
    tz_name = (tz_name or "").strip()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.datetime.now(ZoneInfo(tz_name))
        except Exception as e:
            if tz_name not in _tz_warned:
                _tz_warned.add(tz_name)
                if _api is not None:
                    _api.log(f"time zone {tz_name!r} unusable ({e}) – "
                             f"using local time")
    return datetime.datetime.now()


def _fmt(moment, fmt, fallback):
    try:
        return moment.strftime(fmt) or None
    except Exception:
        return moment.strftime(fallback)


def _clock_values(vals):
    """Fills the clock placeholders. Runs whatever the world half does –
    no watcher, no log, no VRChat."""
    if not _get("clock", True):
        return
    now = _now(_get("clock_tz", ""))
    fmt = str(_get("clock_format", "%H:%M")).strip() or "%H:%M"
    vals["realtime"] = _fmt(now, fmt, "%H:%M")

    if _get("clock_date", False):
        dfmt = str(_get("date_format", "%d.%m.")).strip() or "%d.%m."
        vals["realdate"] = _fmt(now, dfmt, "%d.%m.")
        vals["realday"] = _fmt(now, "%a", "%a")

    if _get("clock_alt", False):
        afmt = str(_get("alt_format", "%H:%M")).strip() or "%H:%M"
        alt = _fmt(_now(_get("alt_tz", "")), afmt, "%H:%M")
        label = str(_get("alt_label", "")).strip()
        vals["realtime_alt"] = f"{label} {alt}".strip() if alt else None


# -------------------------------------------------------------- battery
_BAR_FULL = "\u25b0"
_BAR_EMPTY = "\u25b1"


def _bar(pct, blocks=5):
    filled = int(round(pct / 100.0 * blocks))
    return _BAR_FULL * filled + _BAR_EMPTY * (blocks - filled)


def _battery_values(vals):
    if _battery is None or not _get("battery", False):
        return
    snap = _battery.snapshot()
    if not snap.get("ok"):
        return

    icon = str(_get("battery_icon", "") or "").strip()
    charge_icon = str(_get("battery_charge_icon", "") or "").strip()
    limit = int(_get("battery_show_below", 100) or 100)

    hmd = snap.get("hmd")
    if hmd and hmd.get("pct") is not None and hmd["pct"] <= limit:
        pct = hmd["pct"]
        shown = charge_icon if (hmd.get("charging") and charge_icon) else icon
        vals["hmd_battery"] = f"{shown} {pct}%".strip()
        vals["hmd_battery_raw"] = str(pct)
        vals["hmd_battery_icon"] = shown or None
        vals["hmd_battery_bar"] = _bar(pct)

    if not _get("battery_controllers", True):
        return

    ctl = [c for c in snap.get("controllers") or []
           if c.get("pct") is not None]
    if ctl:
        cicon = str(_get("controller_icon", "") or "").strip()
        parts = []
        for c in ctl:
            role = c.get("role") or ""
            mark = charge_icon if (c.get("charging") and charge_icon) else ""
            parts.append(f"{role}{c['pct']}%{mark}".strip())
        if min(c["pct"] for c in ctl) <= limit:
            vals["controller_battery"] = f"{cicon} {' '.join(parts)}".strip()

    trk = [t for t in snap.get("trackers") or [] if t.get("pct") is not None]
    if trk:
        lowest = min(t["pct"] for t in trk)
        if lowest <= limit:
            ticon = str(_get("tracker_icon", "") or "").strip()
            suffix = f" \u00d7{len(trk)}" if len(trk) > 1 else ""
            vals["tracker_battery"] = f"{ticon} {lowest}%{suffix}".strip()


# ----------------------------------------------------------------- world
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


def _duration(seconds, style):
    """A span of time, short enough for a chatbox line."""
    seconds = int(max(0, seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if style == "clock":
        return f"{hours}:{minutes:02d}"
    if style == "minutes":
        return f"{hours * 60 + minutes}m"
    # auto: drop the unit that would only ever read as zero
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def _timer_values(vals, snap):
    """{world_time} and {vr_time}.

    Both stay empty rather than showing a zero: a line that says you
    have been here for 0m is worse than one that says nothing."""
    want_world = _get("world_time", False)
    want_vr = _get("vr_time", False)
    if not (want_world or want_vr):
        return

    style = str(_get("time_format", "auto"))
    floor = int(_get("time_min", 1) or 0) * 60
    now = time.time()

    if want_world and snap and snap.get("in_world"):
        started = float(snap.get("joined_at") or 0.0)
        if started and now - started >= floor:
            icon = str(_get("world_time_icon", "") or "").strip()
            vals["world_time"] = f"{icon} {_duration(now - started, style)}" \
                .strip()

    if not want_vr:
        return
    if str(_get("vr_time_source", "vrchat")) == "app":
        started = _app_start
    else:
        started = float((snap or {}).get("session_start") or 0.0)
    if started and now - started >= floor:
        icon = str(_get("vr_time_icon", "") or "").strip()
        vals["vr_time"] = f"{icon} {_duration(now - started, style)}".strip()


def _world_values(vals):
    snap = _snapshot()
    _timer_values(vals, snap)
    if not (snap and snap.get("in_world")):
        return
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


# --------------------------------------------------------------- values
KEYS = ("player_in_world", "group_world", "instance_type",
        "world_time", "vr_time",
        "realtime", "realdate", "realday", "realtime_alt",
        "hmd_battery", "hmd_battery_raw", "hmd_battery_icon",
        "hmd_battery_bar", "controller_battery", "tracker_battery")


def get_values():
    """Fills every placeholder this plugin owns.

    Anything switched off or unknown stays None, which apply_template
    drops together with its surrounding separators – so a template like
    "{player_in_world} | {group_world}" never leaves a stray '|' behind.
    """
    vals = {k: None for k in KEYS}
    _world_values(vals)
    _clock_values(vals)
    _battery_values(vals)
    _sync_status()      # same thread as on_tick(), so this is safe here
    return vals


def get_text():
    """The combined line -> {world_stats}."""
    vals = get_values()
    parts = [vals["player_in_world"], vals["group_world"],
             vals["world_time"], vals["vr_time"],
             vals["realtime"], vals["realtime_alt"], vals["hmd_battery"],
             vals["controller_battery"]]
    return " | ".join(p for p in parts if p)


def get_lines():
    """Used when the custom string is switched off."""
    text = get_text()
    return [text] if text else []


# ------------------------------------------------------------- the UI
def build_widget(parent=None):
    """A small live view of what the battery backend actually sees.

    Worth its own widget because a single label row cannot show a list:
    which backend answered, which devices it found, and – the part
    people actually need – the adb serial to paste into the setting
    above when more than one device is attached."""
    from .panel import BatteryPanel
    return BatteryPanel.instance(_api, lambda: _battery, parent)
