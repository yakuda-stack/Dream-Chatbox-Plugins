"""Home Assistant - entity states in the chatbox.

Port of `HomeAssistant` from Bluscream's VRCOSC-Modules, reduced to the
part that makes sense here: reading. The upstream module also drives
avatar parameters, WebSocket events and Jinja templates; a chatbox line
needs three entities and their states.

Uses the REST API with a long-lived access token:
    Home Assistant -> your profile -> Security -> Long-lived access tokens
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json

from . import netutil, util

ID = "ha"
NAME = "Home Assistant"

KEYS = ("ha_1", "ha_2", "ha_3", "ha_all", "ha_count", "ha_online")

SLOTS = (1, 2, 3)
_poller = None
_ctx = None


def start(ctx):
    global _poller, _ctx
    stop()
    _ctx = ctx
    if not ctx.text("ha_url") or not ctx.text("ha_token"):
        ctx.log("home assistant: url or token missing - staying idle")
        return
    _poller = util.Poller(_tick, ctx.log, ctx.num("ha_interval", 30, 5, 900),
                          "homeassistant")
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
    if _poller is None:
        # credentials may have just been filled in
        if ctx.text("ha_url") and ctx.text("ha_token"):
            start(ctx)
        return
    _poller.set_interval(ctx.num("ha_interval", 30, 5, 900))
    _poller.poke()


def _base(ctx):
    url = ctx.text("ha_url") or "http://homeassistant.local:8123"
    return url.rstrip("/")


def _tick():
    ctx = _ctx
    if ctx is None:
        return None
    headers = {"Authorization": f"Bearer {ctx.text('ha_token')}"}
    out = {"online": False, "states": {}}
    for slot in SLOTS:
        entity = ctx.text(f"ha_e{slot}")
        if not entity:
            continue
        status, text = netutil.request(
            f"{_base(ctx)}/api/states/{entity}", headers=headers, timeout=8)
        if status == 401 or status == 403:
            raise RuntimeError("token rejected (401/403)")
        out["online"] = True
        if status < 200 or status >= 300:
            continue                       # unknown entity, skip the slot
        try:
            data = json.loads(text)
        except ValueError:
            continue
        out["states"][slot] = {
            "state": str(data.get("state", "")),
            "unit": str((data.get("attributes") or {})
                        .get("unit_of_measurement", "")),
            "name": str((data.get("attributes") or {})
                        .get("friendly_name", entity)),
        }
    if not out["states"] and not out["online"]:
        return None                        # nothing configured yet
    return out


def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _poller is None:
        return vals
    snap = _poller.snapshot(max_age=max(120.0,
                                        ctx.num("ha_interval", 30, 5, 900) * 4))
    if not snap:
        return vals

    if snap["online"]:
        vals["ha_online"] = ctx.text("ha_online_text", "🏠") or None
    parts = []
    for slot in SLOTS:
        state = snap["states"].get(slot)
        if not state:
            continue
        text = _format(ctx, state)
        label = ctx.text(f"ha_l{slot}")
        value = util.join(label, text)
        vals[f"ha_{slot}"] = value
        parts.append(value)
    vals["ha_all"] = " | ".join(parts) or None
    vals["ha_count"] = str(len(parts)) if parts else None
    return vals


def _format(ctx, state):
    text = state["state"]
    digits = ctx.num("ha_round", 1, 0, 3)
    try:                                   # round only if it is a number
        number = float(text)
        text = f"{number:.{digits}f}" if digits else str(int(round(number)))
    except (TypeError, ValueError):
        if ctx.flag("ha_title", True):
            text = text.replace("_", " ").title()
    if ctx.flag("ha_unit", True) and state["unit"]:
        text = f"{text}{state['unit']}"
    return text


def line(vals):
    return [vals["ha_all"]]
