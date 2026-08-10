"""VR Autostart – start a set of programs when another one starts.

The idea is one press instead of six: pick what has to be running (VRChat,
SteamVR, WiVRn – one program, or two at the same time), pick what should
come up with it (a shell script, an AppImage, an .exe, a command line, or
the OSCLeash plugin next door), press **Start autostart** once and leave
it. The plugin watches the process list from then on, brings the set up
when the trigger appears and takes it down again when it disappears.

    {vr_autostart}          the ready-made line, e.g. "🚀 VRChat · 3 running"
    {vr_autostart_state}    armed | running | off
    {vr_autostart_rule}     name of the rule that is currently active
    {vr_autostart_count}    how many programs this plugin has running
    {vr_autostart_targets}  how many are configured in total

The two big buttons in the panel are the whole interface: the left one
arms the watcher, the right one stops the watcher *and* everything it
started. Everything else is setup.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

_api = None
_engine = None
_panel = None          # embedded widget, when the host can host one
_window = None         # standalone window, when it cannot
_last_status = ""


# ---------------------------------------------------------------- setup
def setup(api):
    global _api, _engine, _last_status
    _api = api
    api.ensure_data_dir()

    from .engine import Engine
    _engine = Engine(api.data_dir, api.log, _setting)
    _engine.start_thread()

    from .procs import backend_name
    api.log(f"process watcher: {backend_name()}")

    _last_status = ""
    if api.get("arm_on_start", True):
        _engine.arm()
    _push_status()

    if api.get("panel", False) and not _supports("widget"):
        _open_window()


def teardown():
    global _engine, _panel
    _close_window()
    _panel = None
    if _engine is not None:
        # a plugin that is switched off has to leave the machine the way
        # it found it – otherwise the programs it started keep running
        # with nothing left to stop them
        _engine.shutdown(stop_targets=_get("stop_on_exit", True))
        _engine = None


def on_settings(settings):
    if _engine is None:
        return
    if _get("panel", False) and not _supports("widget"):
        _open_window()
    else:
        _close_window()


def on_event(name, data=None):
    """Host events. An unknown name is normal, not an error."""
    if name == "app.shutdown" and _engine is not None \
            and _get("stop_on_exit", True):
        # before teardown, while the rest of the app is still standing:
        # stopping here is the difference between a clean exit and an
        # orphaned SteamVR nobody asked for
        _engine.disarm(stop_targets=True)


def on_tick():
    """Once per chatbox frame – cheap enough for a changed-status check.
    The watching itself happens in the engine's own thread."""
    _push_status()


# ------------------------------------------------------------- helpers
def _get(key, default=None):
    return _api.get(key, default) if _api is not None else default


def _setting(key, default=None):
    return _get(key, default)


def _supports(feature):
    check = getattr(_api, "supports", None)
    try:
        return bool(check(feature)) if callable(check) else False
    except Exception:
        return False


def _push_status():
    """Keep the read-only Status row in the settings honest.

    Written only when it changed: api.set() persists, and rewriting the
    same sentence twice a second would mean a config write twice a
    second.
    """
    global _last_status
    if _api is None or _engine is None or not _supports("api.set"):
        return
    running = _engine.running_count()
    if not _engine.armed:
        text = f"stopped \u00b7 {running} program(s) still running" \
            if running else "stopped"
    else:
        rule = _engine.active_rule()
        text = (f"armed \u00b7 {rule.name} active \u00b7 {running} running"
                if rule is not None
                else f"armed \u00b7 waiting for a trigger "
                     f"({_engine.target_count()} configured)")
    if text != _last_status:
        _last_status = text
        try:
            _api.set("status", text)
        except Exception:
            pass


# ------------------------------------------------------------------ UI
def build_widget(parent=None):
    global _panel
    if _engine is None:
        return None
    from .panel import AutostartPanel
    if _panel is not None:
        try:
            _panel.isVisible()
        except RuntimeError:
            # the host rebuilt its plugin list and deleted the card the
            # panel sat in: the python object survives, the C++ one does
            # not, so build a fresh widget instead of handing back a
            # dangling wrapper
            _panel = None
    if _panel is None:
        _panel = AutostartPanel(_api, _engine, parent)
    return _panel


def _open_window():
    global _window
    if _engine is None:
        return
    try:
        from .panel import PanelWindow
        if _window is None:
            _window = PanelWindow(_api, _engine, getattr(_api, "host", None))
        _window.show()
        _window.raise_()
        _window.activateWindow()
    except Exception as e:
        if _api is not None:
            _api.log(f"could not open the panel: {e}")


def _close_window():
    global _window
    if _window is not None:
        try:
            _window.close()
        except Exception:
            pass
        _window = None


# -------------------------------------------------------------- values
def get_values():
    """Unknown stays None – apply_template drops a None together with its
    separators, so an idle autostart leaves no stray dots behind."""
    vals = {"state": None, "rule": None, "count": None, "targets": None}
    if _engine is None:
        return vals
    running = _engine.running_count()
    vals["targets"] = str(_engine.target_count())
    if running:
        vals["count"] = str(running)
    rule = _engine.active_rule()
    if rule is not None:
        vals["rule"] = rule.name
        vals["state"] = "running"
    elif _engine.armed:
        vals["state"] = "armed"
    elif _get("show_off", False):
        vals["state"] = "off"
    return vals


def get_text():
    """The line behind {vr_autostart}. Empty while there is nothing to
    say, so the chatbox costs nothing when the plugin is only waiting."""
    if _engine is None or not _get("show_line", False):
        return ""
    vals = get_values()
    if vals["state"] is None:
        return ""
    icon = str(_get("icon", "\U0001F680") or "").strip()
    if vals["state"] == "running":
        parts = [icon, vals["rule"] or "",
                 f"{vals['count']} running" if vals["count"] else ""]
    elif vals["state"] == "armed":
        parts = [icon, str(_get("armed_text", "autostart armed") or "")]
    else:
        parts = [icon, str(_get("off_text", "autostart off") or "")]
    return " ".join(p for p in parts if p).strip()


def get_lines():
    text = get_text()
    return [text] if text else []
