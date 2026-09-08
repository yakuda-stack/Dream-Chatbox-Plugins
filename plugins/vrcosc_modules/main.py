"""VRCOSC Modules (Linux) - Bluscream's module set for OSC-DreamChatbox.

One plugin, one collapsible block per module, each with its own `enable`
switch. Nothing is started until its block is switched on: a disabled
module has no thread, no socket and no subprocess, and its placeholders
stay empty so they drop out of the line together with their separators.

    Linux Hardware Stats   CPU, GPU, RAM, VRAM, network, FPS, VR mode
    Linux Media            the track from any MPRIS player
    Linux Process Manager  which of your programs are running
    OpenXR                 runtime, session and VR uptime
    Home Assistant         entity states over the REST API
    HTTP                   any URL or JSON API as text
    HTTP / MCP Server      REST + MCP endpoint into the chatbox
    IRC Bridge             the newest message from a channel
    VRCX Bridge            world, friends and friend events
    VRChat Settings        values out of VRChat's config.json
    Notifications          push changes to desktop / XSOverlay / webhook

Upstream: https://github.com/Bluscream/VRCOSC-Modules (GPL-3.0)
See THIRD_PARTY_NOTICES.md for what was taken and what was rewritten.
"""

# Copyright (C) 2026 yakuda
# Bundled scripts: Copyright (c) Bluscream
# SPDX-License-Identifier: GPL-3.0-or-later

import importlib
import platform

# Order matters twice: it is the order of the blocks in the settings and
# the order of the parts in the combined line.
MODULE_NAMES = (
    "mod_hwstats", "mod_media", "mod_process", "mod_openxr",
    "mod_homeassistant", "mod_http", "mod_server", "mod_irc",
    "mod_vrcx", "mod_vrchat", "mod_notify",
)

_api = None
_ctx = None
_modules = []          # the ones that imported cleanly
_running = set()       # ids currently started

IS_LINUX = platform.system() == "Linux"


class Ctx:
    """Typed access to the settings, handed to every module.

    Modules never see the raw api: they get `flag`, `text` and `num` so a
    missing or garbage value can never raise in the middle of building a
    chatbox line.
    """

    def __init__(self, api):
        self._api = api

    def log(self, message):
        try:
            self._api.log(message)
        except Exception:
            pass

    def get(self, key, default=None):
        try:
            return self._api.get(key, default)
        except Exception:
            return default

    def flag(self, key, default=False):
        return bool(self.get(key, default))

    def text(self, key, default=""):
        value = self.get(key, default)
        return str(value if value is not None else default).strip()

    def num(self, key, default, low, high):
        try:
            value = int(float(self.get(key, default)))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))


# ---------------------------------------------------------------- setup
def setup(api):
    global _api, _ctx, _modules
    _api = api
    _ctx = Ctx(api)
    if not IS_LINUX:
        api.log("this suite only works on Linux - staying idle")
        return

    _modules = []
    for name in MODULE_NAMES:
        try:
            _modules.append(importlib.import_module(f".{name}", __package__))
        except Exception as e:
            api.log(f"{name} not importable ({e}) - block skipped")

    for module in _modules:
        if module.__name__.endswith("mod_server"):
            module.set_provider(_provider)

    _sync()
    api.log(f"{len(_running)} of {len(_modules)} blocks running")


def teardown():
    for module in _modules:
        _stop(module)
    _running.clear()


def on_settings(settings):
    """A block was switched on or off, or one of its settings changed."""
    _sync()


def _enabled(module):
    return _ctx is not None and _ctx.flag(f"{module.ID}_enable")


def _sync():
    """Bring every block in line with its enable switch."""
    for module in _modules:
        want = _enabled(module)
        have = module.ID in _running
        if want and not have:
            _start(module)
        elif have and not want:
            _stop(module)
        elif have and want:
            try:
                module.on_settings(_ctx)
            except Exception as e:
                _ctx.log(f"{module.ID}: settings not applied ({e})")


def _start(module):
    try:
        module.start(_ctx)
        _running.add(module.ID)
    except Exception as e:
        _ctx.log(f"{module.ID}: could not start ({e})")
        _stop(module)


def _stop(module):
    try:
        module.stop()
    except Exception:
        pass
    _running.discard(module.ID)


# --------------------------------------------------------------- values
def _provider():
    """What the HTTP server hands out under /status."""
    return {key: value for key, value in get_values().items() if value}


def get_values():
    """Fills the placeholders of every module.

    A block that is off contributes its keys as None, which apply_template
    drops together with its surrounding separators - so a template like
    "{hw_cpu} | {md_media}" never leaves a stray '|' behind when the media
    block is disabled.
    """
    vals = {}
    for module in _modules:
        if module.ID in _running:
            try:
                vals.update(module.values(_ctx))
                continue
            except Exception as e:
                _ctx.log(f"{module.ID}: no values ({e})")
        vals.update(dict.fromkeys(module.KEYS))

    # notifications react to what the other blocks just produced
    for module in _modules:
        if module.ID == "nt" and "nt" in _running:
            try:
                module.observe(_ctx, vals)
            except Exception:
                pass
    return vals


def get_text():
    """The combined line -> {vrcosc_modules}."""
    vals = get_values()
    parts = []
    for module in _modules:
        if module.ID not in _running:
            continue
        try:
            parts.extend(module.line(vals))
        except Exception:
            pass
    separator = _ctx.text("sep", "|") if _ctx else "|"
    glue = f" {separator} " if separator else " "
    return glue.join(part for part in parts if part)


def get_lines():
    """Used when the custom string is switched off."""
    text = get_text()
    return [text] if text else []
