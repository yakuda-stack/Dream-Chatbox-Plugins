"""HTTP - any URL or JSON API as a placeholder.

Port of `HTTP` from Bluscream's VRCOSC-Modules. The upstream module fires
requests from flow nodes; here the request runs on a timer and its answer
becomes text you can drop into a line.

Point it at a JSON API and give it a dotted path
(`current.temp_c`, `data.0.title`), or leave the path empty to use the
raw body. That covers weather, a game server status page, a counter on
your own site - anything that answers over HTTP.
"""

# Copyright (C) 2026 yakuda
# SPDX-License-Identifier: GPL-3.0-or-later

import json

from . import netutil, util

ID = "ht"
NAME = "HTTP"

KEYS = ("ht_text", "ht_status", "ht_count", "ht_ok")

_poller = None
_ctx = None
_count = 0


def start(ctx):
    global _poller, _ctx, _count
    stop()
    _ctx = ctx
    _count = 0
    if not ctx.text("ht_url"):
        ctx.log("http: no url configured - staying idle")
        return
    _poller = util.Poller(_tick, ctx.log, ctx.num("ht_interval", 60, 5, 3600),
                          "http")
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
        if ctx.text("ht_url"):
            start(ctx)
        return
    _poller.set_interval(ctx.num("ht_interval", 60, 5, 3600))
    _poller.poke()


def _tick():
    global _count
    ctx = _ctx
    if ctx is None or not ctx.text("ht_url"):
        return None
    headers = {}
    auth = ctx.text("ht_auth")
    if auth:
        headers["Authorization"] = auth
    status, body = netutil.request(ctx.text("ht_url"),
                                   method=ctx.get("ht_method", "GET"),
                                   headers=headers, timeout=10)
    _count += 1
    text = body
    path = ctx.text("ht_path")
    if path:
        try:
            text = util.dig(json.loads(body), path, "")
        except ValueError:
            text = ""                      # not JSON, keep the slot empty
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False)
    # collapse whitespace: a chatbox line has no room for pretty printing
    text = " ".join(str(text).split())
    return {"status": status, "text": text, "count": _count}


def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _poller is None:
        return vals
    snap = _poller.snapshot(max_age=max(300.0,
                                        ctx.num("ht_interval", 60, 5, 3600) * 4))
    if not snap:
        return vals
    ok = 200 <= snap["status"] < 300
    vals["ht_status"] = str(snap["status"])
    vals["ht_count"] = str(snap["count"])
    vals["ht_ok"] = (ctx.text("ht_ok_text", "🟢") or None) if ok else \
        (ctx.text("ht_fail_text", "🔴") or None)
    if ok and snap["text"]:
        vals["ht_text"] = util.join(
            ctx.text("ht_prefix"),
            util.cut(snap["text"], ctx.num("ht_max", 40, 4, 140))) or None
    return vals


def line(vals):
    return [vals["ht_text"]]
