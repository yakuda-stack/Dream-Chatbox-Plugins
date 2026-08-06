"""OSCLeash – run and supervise ZenithVal's OSCLeash from the chatbox.

The plugin does not reimplement OSCLeash. It starts the real thing as a
child process, one per leash, each with its own generated Config.json,
and gives every instance a Start/Stop button and its own console. On top
of that it reads the process output and turns it into placeholders, so
the chatbox can say what the leash is doing:

    {oscleash}           the ready-made line, e.g. "🐕 Leash ↗"
    {oscleash_state}     pulled | ready
    {oscleash_dir}       ↑ ↗ → ↘ ↓ ↙ ← ↖
    {oscleash_compass}   N NE E SE S SW W NW
    {oscleash_name}      name of the leash currently being pulled
    {oscleash_count}     how many instances are running

Direction and state come from OSCLeash's own log output, so they need
"Debug output" switched on for that instance. Without it the plugin
still knows whether a leash is being pulled, just not where to.

OSCLeash itself is MIT licensed and stays untouched; see
https://github.com/ZenithVal/OSCLeash
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from .detect import find_binary

_api = None
_manager = None
_panel = None          # embedded widget, when the host can host one
_window = None         # standalone window, when it cannot

ARROWS = {"N": "\u2191", "NE": "\u2197", "E": "\u2192", "SE": "\u2198",
          "S": "\u2193", "SW": "\u2199", "W": "\u2190", "NW": "\u2196"}


# ---------------------------------------------------------------- setup
def setup(api):
    global _api, _manager
    _api = api
    api.ensure_data_dir()
    from .runner import LeashManager
    _manager = LeashManager(api.data_dir, api.log, _binary, _restart)
    _manager.start_watchdog()

    from .runtime import describe
    api.log(describe())
    if api.get("autostart", False):
        _manager.start_autostart()
    # only useful where the app cannot embed build_widget() itself
    if api.get("panel", False) and not _supports("widget"):
        _open_window()


def teardown():
    global _manager, _panel, _window
    _close_window()
    _panel = None
    if _manager is not None:
        # leaving processes behind would mean a second start spawns a
        # duplicate that silently fights over the same port
        _manager.shutdown(stop_processes=_get("stop_on_exit", True))
        _manager = None


def on_settings(settings):
    """Only the panel toggle needs acting on – everything else is read
    at the moment it is used."""
    if _manager is None:
        return
    if _get("panel", False) and not _supports("widget"):
        _open_window()
    else:
        _close_window()


def on_event(name, data=None):
    """Host events. Unknown names are ignored on purpose – that is what
    makes this hook survive an app update."""
    if name == "app.shutdown" and _manager is not None and \
            _get("stop_on_exit", True):
        # teardown() follows right after, but stopping here means the
        # processes are gone before the window starts tearing itself
        # down, which is the difference between a clean exit and a
        # leftover OSCLeash holding port 9001
        _manager.stop_all()


# ------------------------------------------------------------- settings
def _get(key, default=None):
    return _api.get(key, default) if _api is not None else default


def _supports(feature):
    """Feature detection against the host. An app that predates
    api.supports() answers False for everything, which is exactly the
    fallback path we want there."""
    check = getattr(_api, "supports", None)
    try:
        return bool(check(feature)) if callable(check) else False
    except Exception:
        return False


def _binary():
    """Which OSCLeash to start: the path setting when the user filled
    one in, otherwise the copy bundled with this plugin."""
    manual = str(_get("binary", "") or "").strip()
    return manual or find_binary()


def _restart():
    return bool(_get("restart_on_crash", True))


def _idle():
    try:
        return max(1, int(_get("idle_secs", 3)))
    except (TypeError, ValueError):
        return 3


# ----------------------------------------------------------------- UI
def build_widget(parent=None):
    """Hook for a host that can embed a plugin widget in its settings
    card. Optional on both sides: without it the panel still opens as its
    own window through the “Control panel” toggle."""
    global _panel
    if _manager is None:
        return None
    from .panel import OSCLeashPanel
    if _panel is not None:
        try:
            _panel.isVisible()
        except RuntimeError:
            # the host rebuilt its plugin list and deleted the card the
            # panel was sitting in – the python object survives, the C++
            # one does not, so build a fresh widget instead of handing
            # back a dangling wrapper
            _panel = None
    if _panel is None:
        _panel = OSCLeashPanel(_api, _manager, parent)
    return _panel


def _open_window():
    global _window
    if _manager is None:
        return
    try:
        from .panel import PanelWindow
        if _window is None:
            _window = PanelWindow(_api, _manager, getattr(_api, "host", None))
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


# ------------------------------------------------------------- values
def _active_instance():
    """The instance being pulled right now, or None. Ties go to the one
    with the stronger pull, so two leashes at once still give one line."""
    if _manager is None:
        return None, 0.0
    best, best_mag = None, 0.0
    idle = _idle()
    for inst in _manager.instances:
        ready, active, direction, mag = inst.state(idle)
        if active and (best is None or mag > best_mag):
            best, best_mag = inst, mag
    return best, best_mag


def get_values():
    """Everything unknown stays None: apply_template drops those together
    with their separators, so an idle leash leaves no stray arrows."""
    vals = {"state": None, "dir": None, "compass": None,
            "name": None, "count": None}
    if _manager is None:
        return vals
    running = _manager.running_count()
    if not running:
        return vals
    vals["count"] = str(running)

    inst, _mag = _active_instance()
    if inst is not None:
        _ready, _active, compass, _m = inst.state(_idle())
        vals["state"] = "pulled"
        vals["name"] = inst.name
        if compass:
            vals["compass"] = compass
            vals["dir"] = ARROWS.get(compass, "")
    elif _get("show_ready", False):
        vals["state"] = "ready"
    return vals


def get_text():
    """The line behind {oscleash}."""
    vals = get_values()
    if vals["state"] is None:
        return ""
    icon = str(_get("icon", "\U0001F415") or "").strip()
    if vals["state"] == "ready":
        parts = [icon, str(_get("ready_text", "leash ready") or "")]
    else:
        parts = [icon, vals["name"] or "", vals["dir"] or ""]
    return " ".join(p for p in parts if p).strip()


def get_lines():
    text = get_text()
    return [text] if text else []
