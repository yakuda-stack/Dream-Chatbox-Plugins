"""Social Media – your handles in the chatbox.

Fills seven placeholders:

    {sm_social}     one enabled network at a time, on a timer
    {sm_discord}    the Discord entry – name, or server › voice channel
    {sm_guild}      the Discord server on its own
    {sm_channel}    the voice channel on its own
    {sm_tiktok}     TikTok handle
    {sm_spotify}    Spotify name
    {sm_instagram}  Instagram handle

Three of the four networks are plain text: you type the handle once and
switch it on or off. Discord can do the same, or run in live mode and
report the server and voice channel you are sitting in right now – read
from the local Discord socket by worker.py, never from this module.

Anything switched off or unknown stays None, which apply_template drops
together with its surrounding separators – so "{sm_tiktok} | {sm_guild}"
never leaves a stray '|' behind.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import time

_api = None
_worker = None

# order is fixed: it decides how the rotation walks through them
NETWORKS = ("discord", "tiktok", "spotify", "instagram")
PREFIX = {"discord": "dc", "tiktok": "tt", "spotify": "sp",
          "instagram": "ig"}
MIN_ROTATE = 10


# ---------------------------------------------------------------- setup
def setup(api):
    global _api, _worker
    _api = api
    try:
        from .worker import SocialWorker
    except Exception as e:
        api.log(f"worker.py not importable ({e}) – "
                f"only the typed-in handles will work.")
        return
    _worker = SocialWorker(_conf, api.log, _store_path())
    _worker.start()
    api.log("ready")


def teardown():
    global _worker
    if _worker is not None:
        try:
            _worker.stop()
        except Exception:
            pass
        _worker = None


def on_settings(settings):
    """Nothing to do by hand: the worker compares the credentials on
    every tick and reconnects a moment after they settle."""


def _store_path():
    """Where the Discord token is cached – next to the plugin's own
    config, so uninstalling the plugin takes it along."""
    folder = getattr(_api, "config_dir", "") or getattr(_api, "plugin_dir", "")
    if not folder:
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "configs")
    return os.path.join(str(folder), "discord_token.json")


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
        "discord": {
            "enabled": bool(_get("dc_enable", True)),
            "live": _text("dc_mode", "manual") == "live",
            "client_id": _text("dc_client_id"),
            "client_secret": _text("dc_client_secret"),
            "redirect": _text("dc_redirect", "http://localhost")
                        or "http://localhost",
            "poll": _int("dc_poll_secs", 5, 2, 60),
        }
    }


# --------------------------------------------------------------- pieces
def _cut(text, limit):
    """Hard cut, no ellipsis – chatbox characters are scarce."""
    text = str(text or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text


def _limit():
    return _int("name_max", 26, 6, 64)


def _handle(name, at=True):
    """'@yakuda', 'yakuda' or a whole URL -> what the user asked for."""
    name = str(name or "").strip()
    if not name:
        return ""
    # a pasted link stays a link, an @ is never doubled
    if at and _get("at_prefix", True) and "/" not in name and "." not in name:
        name = "@" + name.lstrip("@")
    return name


def _decorate(icon, body):
    body = str(body or "").strip()
    if not body:
        return None
    icon = str(icon or "").strip()
    return _cut(f"{icon} {body}".strip(), _limit()) or None


# -------------------------------------------------------------- discord
def _discord_live():
    """(guild, channel) as far as they are switched on, else (None, None)."""
    if _worker is None:
        return None, None
    snap = _worker.snapshot()
    if not snap.get("connected"):
        return None, None
    guild = snap.get("guild") if _get("dc_guild", True) else None
    channel = snap.get("channel") if _get("dc_channel", True) else None
    return (guild or None), (channel or None)


def _discord_parts():
    """(entry, guild, channel) for the Discord placeholders."""
    if not _get("dc_enable", True):
        return None, None, None

    guild = channel = None
    if _text("dc_mode", "manual") == "live":
        guild, channel = _discord_live()

    body = ""
    if guild or channel:
        sep = _text("dc_sep", "›") or "›"
        body = f" {sep} ".join(p for p in (guild, channel) if p)
    else:
        # not in a voice channel, Discord closed, or plain name mode
        body = _handle(_text("dc_name"))

    return _decorate(_text("dc_icon", "🎧"), body), guild, channel


def _simple(network):
    """TikTok, Spotify and Instagram all work the same way. Spotify is
    the exception on the '@': profile names there are not handles."""
    prefix = PREFIX[network]
    if not _get(f"{prefix}_enable", False):
        return None
    name = _handle(_text(f"{prefix}_name"), at=network != "spotify")
    return _decorate(_text(f"{prefix}_icon"), name)


# --------------------------------------------------------------- values
def get_values():
    """Fills the seven placeholders."""
    vals = {"sm_social": None, "sm_discord": None, "sm_guild": None,
            "sm_channel": None, "sm_tiktok": None, "sm_spotify": None,
            "sm_instagram": None}

    discord, guild, channel = _discord_parts()
    vals["sm_discord"] = discord
    vals["sm_guild"] = _cut(guild, _limit()) or None
    vals["sm_channel"] = _cut(channel, _limit()) or None
    for network in ("tiktok", "spotify", "instagram"):
        vals[f"sm_{network}"] = _simple(network)

    entries = [vals[f"sm_{network}"] for network in NETWORKS]
    entries = [e for e in entries if e]
    if entries:
        if _get("rotate", True) and len(entries) > 1:
            secs = _int("rotate_secs", 20, MIN_ROTATE, 3600)
            # slot from the wall clock, not from a counter: a restart
            # does not resync anything and nothing drifts apart
            vals["sm_social"] = entries[int(time.time() // secs)
                                        % len(entries)]
        else:
            vals["sm_social"] = _join(entries)
    return vals


def _join(entries):
    sep = _get("sep", " | ")
    sep = sep if isinstance(sep, str) and sep else " | "
    return sep.join(entries)


def get_text():
    """The combined line -> {social_media}."""
    vals = get_values()
    entries = [vals[f"sm_{network}"] for network in NETWORKS]
    return _join([e for e in entries if e])


def get_lines():
    """Used when the custom string is switched off."""
    text = get_text()
    return [text] if text else []
