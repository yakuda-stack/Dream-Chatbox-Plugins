"""Stream Stats – Twitch and YouTube live data for OSC-DreamChatbox.

Fills four placeholders:

    {s_name}    the channel name of the platform currently on show
    {s_status}  its title, category or uptime
    {s_viewer}  the concurrent viewer count
    {s_chat}    the newest chat message

Each of the four picks its own source: always Twitch, always YouTube, or
rotating between whichever of them is live right now. Rotation is driven
by a shared timer, so all four switch together instead of drifting apart
into a mixed line.

Nothing here talks to the network: worker.py polls in the background and
this module only reads its snapshot.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import time

_api = None
_worker = None

PLATFORMS = ("twitch", "youtube")
# settings key prefix per platform, so one code path can serve both
PREFIX = {"twitch": "tw", "youtube": "yt"}
MIN_ROTATE = 10


# ---------------------------------------------------------------- setup
def setup(api):
    global _api, _worker
    _api = api
    try:
        from .worker import StreamWorker
    except Exception as e:
        api.log(f"worker.py not importable ({e}) – the plugin stays idle.")
        return
    _worker = StreamWorker(_conf, api.log)
    _worker.start()
    api.log("watching the enabled platforms")


def teardown():
    global _worker
    if _worker is not None:
        try:
            _worker.stop()
        except Exception:
            pass
        _worker = None


def on_settings(settings):
    """Nothing to do by hand: the worker compares the channel and the
    credentials on every tick and re-polls a moment after they settle."""


def _get(key, default=None):
    return _api.get(key, default) if _api is not None else default


def _text(key, default=""):
    return str(_get(key, default) or "").strip()


def _int(key, default, low, high):
    try:
        value = int(_get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


# --------------------------------------------------------- config sheet
def _conf():
    """The worker's view of the settings. Called from its thread, so it
    only reads the live settings dict and never touches Qt."""
    return {
        "poll": _int("poll_secs", 30, 10, 900),
        "want": _wanted(),
        "twitch": {
            "enabled": bool(_get("tw_enable", False)),
            "channel": _text("tw_channel"),
            "mode": _text("tw_mode", "keyless") or "keyless",
            "client_id": _text("tw_client_id"),
            "client_secret": _text("tw_client_secret"),
            "chat": bool(_get("tw_chat", False)),
        },
        "youtube": {
            "enabled": bool(_get("yt_enable", False)),
            "channel": _text("yt_channel"),
            "mode": _text("yt_mode", "keyless") or "keyless",
            "api_key": _text("yt_api_key"),
            "chat": bool(_get("yt_chat", False)),
        },
    }


def _wanted():
    """Which fields the placeholders actually need. The keyless Twitch
    source pays one request per field, so asking for less is faster."""
    want = set()
    if _uses("src_viewer"):
        want.add("viewers")
    if _uses("src_status"):
        want.add(_text("tw_status", "title") or "title")
    return want


def _uses(key):
    """A placeholder counts as used unless its source is switched off."""
    return _text(key, "rotate") != "off"


# ------------------------------------------------------------- platform
def _live(snap, conf, platform):
    return bool(conf[platform]["enabled"] and snap[platform]["live"])


def _pick(key, snap, conf):
    """Which platform this placeholder shows right now, or None."""
    source = _text(key, "rotate") or "rotate"
    if source == "off":
        return None
    if source in PLATFORMS:
        return source if _live(snap, conf, source) else None

    live = [p for p in PLATFORMS if _live(snap, conf, p)]
    if not live:
        return None
    # rotation off (or only one platform live) = simply the first one,
    # so a switched-off timer never leaves a placeholder empty
    if not _get("rotate", True) or len(live) == 1:
        return live[0]
    secs = _int("rotate_secs", 30, MIN_ROTATE, 3600)
    # slot from the wall clock, not from a counter: every placeholder
    # switches at the same instant and a restart does not resync anything
    return live[int(time.time() // secs) % len(live)]


def _cut(text, limit):
    """Hard cut, no ellipsis – chatbox characters are scarce."""
    text = str(text or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text


def _status_of(state, platform):
    field = _text(f"{PREFIX[platform]}_status", "title") or "title"
    value = state.get(field) or ""
    if not value and field != "title":
        value = state.get("title") or ""
    return value


def _fallback_name():
    """Channel name to show while nothing is live."""
    for platform in PLATFORMS:
        if _get(f"{PREFIX[platform]}_enable", False):
            name = _text(f"{PREFIX[platform]}_channel")
            if name:
                return name.lstrip("@")
    return ""


# --------------------------------------------------------------- values
def get_values():
    """Fills {s_name} {s_status} {s_viewer} {s_chat}.

    Anything unknown stays None, which apply_template drops together
    with its surrounding separators – so "{s_name} | {s_viewer}" never
    leaves a stray '|' behind while a stream is offline.
    """
    vals = {"s_name": None, "s_status": None,
            "s_viewer": None, "s_chat": None}
    if _worker is None:
        return vals
    snap = _worker.snapshot()
    conf = _conf()

    platform = _pick("src_name", snap, conf)
    if platform:
        name = snap[platform]["name"] or conf[platform]["channel"]
        vals["s_name"] = _cut(name, _int("name_max", 24, 4, 64)) or None

    platform = _pick("src_status", snap, conf)
    if platform:
        status = _status_of(snap[platform], platform)
        vals["s_status"] = _cut(status, _int("status_max", 40, 8, 120)) or None

    platform = _pick("src_viewer", snap, conf)
    if platform and snap[platform]["viewers"] is not None:
        icon = _text("viewer_icon")
        vals["s_viewer"] = f"{icon} {snap[platform]['viewers']}".strip()

    platform = _pick("src_chat", snap, conf)
    if platform:
        chat = snap[platform].get("chat")
        if chat:
            author, message = chat
            limit = _int("chat_max", 60, 10, 140)
            if _get("chat_author", True) and author:
                message = f"{author}: {message}"
            vals["s_chat"] = _cut(message, limit) or None

    # nothing live anywhere: show the offline text instead of an empty
    # line, but only if the user asked for one
    offline = _text("offline_text")
    if offline and not any(_live(snap, conf, p) for p in PLATFORMS):
        vals["s_status"] = offline
        vals["s_name"] = vals["s_name"] or (_fallback_name() or None)
    return vals


def get_text():
    """The combined line -> {stream_stats}."""
    vals = get_values()
    parts = [vals["s_name"], vals["s_viewer"], vals["s_status"]]
    return " | ".join(p for p in parts if p)


def get_lines():
    """Used when the custom string is switched off."""
    text = get_text()
    return [text] if text else []
