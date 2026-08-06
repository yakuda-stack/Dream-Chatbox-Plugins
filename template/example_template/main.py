"""Example Template – every hook of the plugin API, in one file.

Copy the whole folder, rename it (folder name, "id" and "name" in
plugin.json all match), delete what you do not need. Nothing in here is
required: every hook is optional and the app only calls what it finds.

Read this top to bottom once; it is ordered the way a plugin runs.

    setup()        once, when the plugin is switched on
    on_settings()  whenever the user changes a setting
    on_tick()      once per chatbox frame
    get_values()   right after, to build the placeholders
    get_text()     the plugin's own line
    on_text()      last look at the finished chatbox text
    on_action()    a button in the settings was pressed
    on_event()     the app announced something
    build_widget() the plugin's own UI, built once and re-used
    teardown()     once, when it is switched off or the app closes
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import time

# Module level state. A plugin is imported once and lives until it is
# switched off, so this is where things belong that outlive one call.
# Do NOT import Qt here - see build_widget().
_api = None
_ticks = 0
_started_at = 0.0
_events = []          # what the app has announced, for the panel


# --------------------------------------------------------------- setup
def setup(api):
    """Called once, right after the module was imported.

    Keep the api object; it is the only way back into the app. Anything
    slow belongs in a thread - setup() runs on the GUI thread and the
    window is not painted while it works.
    """
    global _api, _started_at, _ticks
    _api = api
    _started_at = time.time()
    _ticks = 0

    api.log(f"hello from {api.plugin_id} on {api.app_name} {api.app_version}")

    # Feature detection instead of version checks: this plugin declares
    # "api": 2 in its manifest, so api.set() is guaranteed here - the
    # pattern is shown anyway because your plugin may want to stay
    # installable on an older app.
    if api.supports("api.set"):
        api.set("status", "running")

    # api.data_dir is the plugin's own writable folder. It survives an
    # update, which is exactly why a plugin must never ship a configs/
    # folder in its zip - that would overwrite the user's.
    api.ensure_data_dir()


def teardown():
    """Switched off, or the app is closing.

    Stop what you started: threads, subprocesses, files, timers. A
    plugin that leaves a thread running keeps running after the user
    switched it off, which is the one thing they explicitly asked it not
    to do.
    """
    if _api is not None and _api.supports("api.set"):
        _api.set("status", "not running")
    _events.clear()


# ------------------------------------------------------------ settings
def on_settings(options):
    """A setting changed. ``options`` is the live dict, same as
    api.settings - the value is already stored by the time this runs.

    Only react to what actually needs reacting to. Everything that can
    simply be read when it is used should be read when it is used.
    """
    if _api is not None:
        _api.log(f"settings changed – mood is now {options.get('mood')!r}")


# ------------------------------------------------------------- runtime
def on_tick():
    """Once per chatbox frame, before the values are collected.

    A heartbeat for cheap polling. Anything that can block - network, a
    subprocess, a file on a slow mount - belongs in a thread instead:
    this runs on the GUI thread and a slow tick is a stuttering app.
    """
    global _ticks
    _ticks += 1


def get_values():
    """Extra placeholders: {example_template_<key>} for every key here.

    Return None - not "" - for anything unknown right now.
    apply_template() drops a None together with its separators, so
    "{a} | {b}" leaves no stray pipe behind when b is missing.
    """
    values = {"mood": None, "count": None, "ticking": None}
    if _api is None:
        return values
    values["mood"] = f"{_api.get('icon', '')} {_api.get('mood', '')}".strip()
    if _api.get("tick_count"):
        values["count"] = str(_ticks)
        values["ticking"] = "ticking"
    return values


def get_text():
    """The plugin's own line, behind {example_template}.

    Return "" to say nothing this frame - an empty line is dropped, so a
    plugin with nothing to report costs the chatbox nothing.
    """
    if _api is None or not _api.get("enabled_demo", True):
        return ""
    name = str(_api.get("name", "") or "").strip()
    icon = str(_api.get("icon", "") or "").strip()
    return " ".join(p for p in (icon, name) if p)


def get_lines():
    """Whole lines appended to the payload, independent of {…} usage.

    Most plugins want get_text() instead. Use this when the plugin owns
    a line of its own rather than a value inside someone else's.
    """
    return []


def on_text(text):
    """The finished chatbox text, just before it is sent.

    Last chance to change anything - and the easiest hook to get wrong:
    whatever is returned IS the message. Always return a string, and
    when in doubt return the one that came in.
    """
    return text


# ------------------------------------------------------------- buttons
def on_action(key):
    """An action button was pressed. Return a string to show next to it.

    Runs on the GUI thread: a button that takes two seconds freezes the
    window for two seconds. Start a thread and report back through
    api.set() instead.
    """
    if _api is None:
        return ""
    if key == "ping":
        up = int(time.time() - _started_at)
        _api.set("status", f"pinged after {up}s")
        return f"hello – {_ticks} ticks so far"
    if key == "reset":
        _api.set_many({"name": "hello", "mood": "curious", "amount": 5,
                       "level": 40, "icon": "\u2728", "folder": "",
                       "status": "reset", "tick_count": False})
        return "back to defaults"
    return ""


# -------------------------------------------------------------- events
def on_event(name, data=None):
    """The app announced something. React to what you know, ignore the
    rest - a name you have never heard of is normal, not an error. That
    is what makes this hook survive an app update.
    """
    _events.append((time.strftime("%H:%M:%S"), str(name)))
    del _events[:-20]
    if name == "app.shutdown":
        # the last moment where the rest of the app is still standing
        pass


# ------------------------------------------------------------- the UI
def build_widget(parent=None):
    """The plugin's own widget, embedded under its settings.

    For everything the schema cannot express: buttons that need to sit
    next to each other, a live log, a list the user adds rows to.

    Three rules, all learned the hard way:

    * import Qt HERE, not at module level, so the plugin still loads
      where there is no GUI
    * the page can rebuild and delete the widget on the C++ side while
      the python object survives - check before handing a cached one
      back a second time
    * never touch a widget from a worker thread. Poll from a QTimer in
      the GUI thread instead; a widget touched from another thread is a
      segfault, not an exception.
    """
    from .panel import TemplatePanel
    return TemplatePanel.instance(_api, _events, parent)
