"""OSC Parameter Profiles - read VRChat's avatar parameters, save them
under a name, click load to send them back.

This file is only the hook surface. It holds no state of its own, on
purpose: the loader makes main.py *be* the package, so anything stored
here is invisible to modules that import it back by name. All state
lives in runtime.py - see the comment at the top of that file.

Everything goes through OSCQuery:

    the listen port    the OS picks it and mDNS announces it, so it can
                       never collide with the app's own receiver on 9001
    the send port      read from VRChat's HOST_INFO instead of assuming
                       9000, which is wrong the moment somebody uses a
                       --osc launch argument
    the parameter list fetched from VRChat's own OSCQuery tree, so it is
                       complete the second the plugin starts rather than
                       filling up as the user happens to move sliders
    the types          taken from the tree's TYPE, and whether a write is
                       accepted at all from its ACCESS

Nothing that touches the network runs on the GUI thread.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

from .runtime import state


def setup(api):
    state.setup(api)


def teardown():
    state.teardown()


def on_settings(options):
    """Only the service name touches the connection now - everything
    else that used to live here was a port, and ports are discovered."""
    if state.bridge is None:
        return
    wanted = str(options.get("service_name") or
                 "OSC-DreamChatbox ParamProfiles")
    if wanted != state.bridge.service_name:
        state.bridge.service_name = wanted
        state.restart()
    state.publish_status()


def on_event(name, data=None):
    if name == "app.shutdown":
        state.teardown()


def get_values():
    """{osc_paramprofiles_<key>} for the chatbox template."""
    return state.values()


def get_text():
    """{osc_paramprofiles} - the profile line, or nothing at all."""
    return state.text()


def on_action(key):
    if key == "restart":
        return state.restart()
    if key == "refresh_now":
        return state.refresh_now()
    if key == "open_folder":
        return state.open_folder()
    return ""


def build_widget(parent=None):
    from .panel import ProfilesPanel
    return ProfilesPanel.instance(parent)
